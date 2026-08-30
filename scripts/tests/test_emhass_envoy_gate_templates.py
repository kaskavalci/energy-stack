"""HA /api/template behaviour tests for binary_sensor.emhass_envoy_required.

Loads production gate Jinja from the HA package and evaluates it through the
real Home Assistant template engine with mechanical substitutes only (no
Python gate model). Also covers PV / house-load / C1 consumers.
"""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ruamel.yaml import YAML

from ha_template_eval import eval_ha_template, require_ha_or_skip


REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "home-assistant" / "ha_emhass_sensors.yaml"
MONITORING = REPO_ROOT / "home-assistant" / "ha_emhass_monitoring.yaml"
ENVOY_REQUIRED_ID = "binary_sensor.emhass_envoy_required"
PV_ENTITY_ID = "sensor.emhass_pv_power_w"
HOUSE_LOAD_ENTITY_ID = "sensor.emhass_house_load_power_w"
C1_ENTITY_ID = "binary_sensor.emhass_fail_pv_sensor"
AMSTERDAM = ZoneInfo("Europe/Amsterdam")

GATE_EXPR = "is_state('binary_sensor.emhass_envoy_required', 'on')"
ENVOY_AGE_RAW = "(now() - raw.last_updated).total_seconds() > ENVOY_STALE_S"
ENVOY_AGE_RAW_E = "(now() - raw_e.last_updated).total_seconds() > stale_after_s"
# Valid HA entity id stub so /api/template accepts states[...] without using live Envoy.
ENVOY_PLACEHOLDER = "'__ENVOY_PV_ENTITY__'"
ENVOY_STUB_ENTITY = "'sensor.emhass_test_envoy_stub'"
P1_BATT_FOR_LOOP = (
    "{% for entity_id in [\n"
    "  '__P1_POWER_NET_ENTITY__',\n"
    "  'sensor.__SIGEN_PLANT_SLUG___plant_battery_power',\n"
    "] %}\n"
    "  {% set entity = states[entity_id] %}\n"
    "  {% if entity is none\n"
    "        or entity.state in bad\n"
    "        or (now() - entity.last_updated).total_seconds() > stale_after_s %}\n"
    "    {% set ns.ok = false %}\n"
    "  {% endif %}\n"
    "{% endfor %}"
)
P1_BATT_FORCED = (
    "{% if P1_INPUT_STALE %}\n"
    "  {% set ns.ok = false %}\n"
    "{% endif %}\n"
    "{% if BATT_INPUT_STALE %}\n"
    "  {% set ns.ok = false %}\n"
    "{% endif %}"
)

# Last-rising reference unix; next_rising ISO uses last + 86400 so production
# (as_timestamp(now()) - (rising_ts - 86400)) yields the case offset.
RISING_UNIX = 1_714_046_400  # 2024-03-26T12:00:00+00:00 last-rising reference
NEXT_RISING_UNIX = RISING_UNIX + 86_400
RISING_ISO = datetime.fromtimestamp(NEXT_RISING_UNIX, tz=timezone.utc).strftime(
    "%Y-%m-%dT%H:%M:%S+00:00"
)

# Europe/Amsterdam DST calendar days (fixed unix pairs; grace via as_timestamp).
# Spring-forward 2026-03-29; fall-back 2026-10-25.
_DST_SPRING_LAST = int(
    datetime(2026, 3, 29, 7, 0, tzinfo=AMSTERDAM).timestamp()
)
_DST_SPRING_NEXT = _DST_SPRING_LAST + 86_400
_DST_SPRING_ISO = datetime.fromtimestamp(
    _DST_SPRING_NEXT, tz=timezone.utc
).strftime("%Y-%m-%dT%H:%M:%S+00:00")
_DST_FALL_LAST = int(
    datetime(2026, 10, 25, 7, 0, tzinfo=AMSTERDAM).timestamp()
)
_DST_FALL_NEXT = _DST_FALL_LAST + 86_400
_DST_FALL_ISO = datetime.fromtimestamp(
    _DST_FALL_NEXT, tz=timezone.utc
).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def load_package(path: Path = PACKAGE) -> Mapping[str, Any]:
    with path.open(encoding="utf-8") as stream:
        document = YAML(typ="safe").load(stream)
    if not isinstance(document, Mapping):
        raise TypeError("HA package root must be a mapping")
    return document


def _find_template_entity(
    package_yaml: Mapping[str, Any],
    entity_id: str,
    *,
    kind: str = "sensor",
) -> Mapping[str, Any]:
    matches: list[Mapping[str, Any]] = []
    for template_item in package_yaml.get("template", []):
        if not isinstance(template_item, Mapping):
            continue
        for sensor in template_item.get(kind, []):
            if (
                isinstance(sensor, Mapping)
                and sensor.get("default_entity_id") == entity_id
            ):
                matches.append(sensor)
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one {entity_id}, found {len(matches)}"
        )
    return matches[0]


def extract_envoy_required_templates(
    package_yaml: Mapping[str, Any],
) -> tuple[str, str]:
    """Return (state_j2, reason_j2) for binary_sensor.emhass_envoy_required."""
    sensor = _find_template_entity(
        package_yaml, ENVOY_REQUIRED_ID, kind="binary_sensor"
    )
    state = sensor.get("state")
    attrs = sensor.get("attributes")
    if not isinstance(state, str):
        raise AssertionError(f"{ENVOY_REQUIRED_ID} state template missing")
    if not isinstance(attrs, Mapping) or "gate_reason" not in attrs:
        raise AssertionError(f"{ENVOY_REQUIRED_ID} gate_reason attribute missing")
    reason = attrs["gate_reason"]
    if not isinstance(reason, str):
        raise AssertionError(f"{ENVOY_REQUIRED_ID} gate_reason must be a string")
    # Global gate contract: Task 2 must keep is_number + mpc freshness in
    # both state and reason; fail extract loudly if either is omitted.
    for label, tmpl in (("state", state), ("gate_reason", reason)):
        if "is is_number" not in tmpl:
            raise AssertionError(
                f"{ENVOY_REQUIRED_ID} {label} must use 'is is_number' "
                "(HA forecast numeric gate)"
            )
        if "binary_sensor.emhass_mpc_fresh" not in tmpl:
            raise AssertionError(
                f"{ENVOY_REQUIRED_ID} {label} must reference "
                "binary_sensor.emhass_mpc_fresh"
            )
        if "is_state('binary_sensor.emhass_mpc_fresh', 'on')" not in tmpl:
            raise AssertionError(
                f"{ENVOY_REQUIRED_ID} {label} must use "
                "is_state('binary_sensor.emhass_mpc_fresh', 'on')"
            )
    return state, reason


def extract_consumer_templates(
    sensors_yaml: Mapping[str, Any],
    monitoring_yaml: Mapping[str, Any],
) -> tuple[str, str, str, str]:
    """Return (pv_state, pv_avail, house_avail, c1_state) production Jinja."""
    pv = _find_template_entity(sensors_yaml, PV_ENTITY_ID)
    house = _find_template_entity(sensors_yaml, HOUSE_LOAD_ENTITY_ID)
    c1 = _find_template_entity(
        monitoring_yaml, C1_ENTITY_ID, kind="binary_sensor"
    )
    pv_state = pv.get("state")
    pv_avail = pv.get("availability")
    house_avail = house.get("availability")
    c1_state = c1.get("state")
    for label, tmpl in (
        ("pv state", pv_state),
        ("pv availability", pv_avail),
        ("house-load availability", house_avail),
        ("C1 state", c1_state),
    ):
        if not isinstance(tmpl, str):
            raise AssertionError(f"{label} template missing")
    assert isinstance(pv_state, str)
    assert isinstance(pv_avail, str)
    assert isinstance(house_avail, str)
    assert isinstance(c1_state, str)
    if P1_BATT_FOR_LOOP not in house_avail:
        raise AssertionError(
            "house-load availability must contain P1+battery freshness for-loop"
        )
    return pv_state, pv_avail, house_avail, c1_state


def substitute_once(src: str, old: str, new: str) -> str:
    assert src.count(old) == 1, f"expected exactly one {old!r}, found {src.count(old)}"
    out = src.replace(old, new, 1)
    assert out.count(old) == 0
    return out


def apply_gate_substitutes(
    template: str,
    *,
    sun_up: bool,
    next_rising_subst: str | None,
    now_ts: int,
    fc: str,
    mpc_fresh: bool,
) -> str:
    """Apply mechanical substitutes; keep production as_timestamp(..., default=0)."""
    out = template
    out = substitute_once(
        out,
        "is_state('sun.sun', 'above_horizon')",
        "true" if sun_up else "false",
    )
    if next_rising_subst is None:
        rising_new = "none"
    else:
        rising_new = f"'{next_rising_subst}'"
    out = substitute_once(
        out,
        "state_attr('sun.sun', 'next_rising')",
        rising_new,
    )
    out = substitute_once(out, "as_timestamp(now())", str(now_ts))
    out = substitute_once(
        out,
        "states('sensor.p_pv_forecast')",
        f"'{fc}'",
    )
    out = substitute_once(
        out,
        "is_state('binary_sensor.emhass_mpc_fresh', 'on')",
        "true" if mpc_fresh else "false",
    )
    return out


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("true", "on", "1")


def _falsey(value: str) -> bool:
    return value.strip().lower() in ("false", "off", "0", "")


def apply_envoy_age_substitutes(
    template: str,
    *,
    age_s: int,
    gate_on: bool,
    age_expr: str,
    age_cmp: str,
    none_expr: str,
) -> str:
    """Force gate + Envoy age; neutralize live entity bad/none checks."""
    out = template
    out = substitute_once(out, GATE_EXPR, "true" if gate_on else "false")
    out = substitute_once(out, ENVOY_PLACEHOLDER, ENVOY_STUB_ENTITY)
    out = substitute_once(out, age_expr, f"AGE_S > {age_cmp}")
    out = substitute_once(out, "states(raw_envoy) in bad", "false")
    out = substitute_once(out, none_expr, "false")
    return f"{{% set AGE_S = {age_s} %}}\n{out}"


def force_p1_batt_inputs(
    house_avail: str,
    *,
    p1_stale: bool,
    batt_stale: bool,
) -> str:
    """Replace production P1/battery freshness loop with forced flags."""
    out = substitute_once(house_avail, P1_BATT_FOR_LOOP, P1_BATT_FORCED)
    assert "__P1_POWER_NET_ENTITY__" not in out
    assert "sensor.__SIGEN_PLANT_SLUG___plant_battery_power" not in out
    assert "(now() - entity.last_updated)" not in out
    prefix = (
        f"{{% set P1_INPUT_STALE = {'true' if p1_stale else 'false'} %}}\n"
        f"{{% set BATT_INPUT_STALE = {'true' if batt_stale else 'false'} %}}\n"
    )
    return prefix + out


# (name, sun_up, next_rising_subst, now_ts, fc, mpc_fresh, expect_reason, expect_on)
# next_rising_subst: None means literal jinja `none`; str means quoted ISO for as_timestamp
CASES: list[tuple[str, bool, str | None, int, str, bool, str, bool]] = [
    (
        "night",
        False,
        "2026-08-30T04:00:00+00:00",
        1_000_000,
        "200",
        True,
        "skip_night",
        False,
    ),
    (
        "grace_1799",
        True,
        RISING_ISO,
        RISING_UNIX + 1799,
        "200",
        True,
        "skip_dawn_grace",
        False,
    ),
    (
        "grace_1800",
        True,
        RISING_ISO,
        RISING_UNIX + 1800,
        "200",
        True,
        "skip_dawn_grace",
        False,
    ),
    (
        "grace_1801",
        True,
        RISING_ISO,
        RISING_UNIX + 1801,
        "200",
        True,
        "require_fresh",
        True,
    ),
    (
        "fc_149",
        True,
        RISING_ISO,
        RISING_UNIX + 2000,
        "149",
        True,
        "skip_low_forecast",
        False,
    ),
    (
        "fc_150",
        True,
        RISING_ISO,
        RISING_UNIX + 2000,
        "150",
        True,
        "require_fresh",
        True,
    ),
    (
        "fc_151",
        True,
        RISING_ISO,
        RISING_UNIX + 2000,
        "151",
        True,
        "require_fresh",
        True,
    ),
    (
        "fc_unavailable",
        True,
        RISING_ISO,
        RISING_UNIX + 2000,
        "unavailable",
        True,
        "skip_forecast_unavailable",
        False,
    ),
    (
        "fc_nan",
        True,
        RISING_ISO,
        RISING_UNIX + 2000,
        "nan",
        True,
        "skip_forecast_unavailable",
        False,
    ),
    (
        "fc_inf",
        True,
        RISING_ISO,
        RISING_UNIX + 2000,
        "inf",
        True,
        "skip_forecast_unavailable",
        False,
    ),
    (
        "fc_ninf",
        True,
        RISING_ISO,
        RISING_UNIX + 2000,
        "-inf",
        True,
        "skip_forecast_unavailable",
        False,
    ),
    (
        "mpc_stale",
        True,
        RISING_ISO,
        RISING_UNIX + 2000,
        "500",
        False,
        "skip_mpc_stale",
        False,
    ),
    (
        "rising_missing",
        True,
        None,
        RISING_UNIX + 2000,
        "500",
        True,
        "skip_sunrise_unavailable",
        False,
    ),
    (
        "dst_spring_grace_elapsed",
        True,
        _DST_SPRING_ISO,
        _DST_SPRING_LAST + 2000,
        "200",
        True,
        "require_fresh",
        True,
    ),
    (
        "dst_fall_grace_elapsed",
        True,
        _DST_FALL_ISO,
        _DST_FALL_LAST + 2000,
        "200",
        True,
        "require_fresh",
        True,
    ),
]


class EmhassEnvoyGateTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        require_ha_or_skip(self)
        self.package = load_package()

    def _templates(self) -> tuple[str, str]:
        return extract_envoy_required_templates(self.package)

    def test_extract_envoy_required_templates(self) -> None:
        state_j2, reason_j2 = self._templates()
        self.assertIn("state_attr('sun.sun', 'next_rising')", state_j2)
        self.assertIn("as_timestamp(", state_j2)
        self.assertIn("default=0", state_j2)
        self.assertIn("states('sensor.p_pv_forecast')", state_j2)
        self.assertIn("30 * 60", state_j2)
        self.assertIn("150", state_j2)
        # Locked again here so a future extract that drops contract asserts
        # still fails this structural test loudly.
        self.assertIn("is is_number", state_j2)
        self.assertIn("is is_number", reason_j2)
        self.assertIn("binary_sensor.emhass_mpc_fresh", state_j2)
        self.assertIn("binary_sensor.emhass_mpc_fresh", reason_j2)
        self.assertIn(
            "is_state('binary_sensor.emhass_mpc_fresh', 'on')",
            state_j2,
        )
        self.assertIn(
            "is_state('binary_sensor.emhass_mpc_fresh', 'on')",
            reason_j2,
        )
        self.assertIn("skip_night", reason_j2)
        self.assertIn("require_fresh", reason_j2)

    def test_night_plus_missing_rising_prefers_skip_night(self) -> None:
        state_j2, reason_j2 = self._templates()
        kwargs = dict(
            sun_up=False,
            next_rising_subst=None,
            now_ts=RISING_UNIX + 2000,
            fc="500",
            mpc_fresh=True,
        )
        reason = eval_ha_template(apply_gate_substitutes(reason_j2, **kwargs))
        state = eval_ha_template(apply_gate_substitutes(state_j2, **kwargs))
        self.assertEqual(reason, "skip_night")
        self.assertNotEqual(reason, "skip_sunrise_unavailable")
        self.assertIn(state.lower(), ("false", "off", "0", ""))

    def test_gate_cases(self) -> None:
        state_j2, reason_j2 = self._templates()
        for (
            name,
            sun_up,
            next_rising_subst,
            now_ts,
            fc,
            mpc_fresh,
            expect_reason,
            expect_on,
        ) in CASES:
            with self.subTest(name=name):
                kwargs = dict(
                    sun_up=sun_up,
                    next_rising_subst=next_rising_subst,
                    now_ts=now_ts,
                    fc=fc,
                    mpc_fresh=mpc_fresh,
                )
                reason = eval_ha_template(
                    apply_gate_substitutes(reason_j2, **kwargs)
                )
                state = eval_ha_template(
                    apply_gate_substitutes(state_j2, **kwargs)
                )
                self.assertEqual(reason, expect_reason, name)
                if expect_on:
                    self.assertIn(
                        state.lower(),
                        ("true", "on", "1"),
                        f"{name}: expected on, got {state!r}",
                    )
                else:
                    self.assertIn(
                        state.lower(),
                        ("false", "off", "0", ""),
                        f"{name}: expected off, got {state!r}",
                    )


class EmhassEnvoyConsumerTemplateTests(unittest.TestCase):
    """PV / house-load / C1 consumers via HA /api/template + forced inputs."""

    def setUp(self) -> None:
        require_ha_or_skip(self)
        self.sensors = load_package(PACKAGE)
        self.monitoring = load_package(MONITORING)
        (
            self.pv_state,
            self.pv_avail,
            self.house_avail,
            self.c1_state,
        ) = extract_consumer_templates(self.sensors, self.monitoring)

    def _prep_pv_avail(self, *, age_s: int, gate_on: bool) -> str:
        return apply_envoy_age_substitutes(
            self.pv_avail,
            age_s=age_s,
            gate_on=gate_on,
            age_expr=ENVOY_AGE_RAW,
            age_cmp="ENVOY_STALE_S",
            none_expr="raw is none",
        )

    def _prep_pv_state(self, *, age_s: int, gate_on: bool) -> str:
        return apply_envoy_age_substitutes(
            self.pv_state,
            age_s=age_s,
            gate_on=gate_on,
            age_expr=ENVOY_AGE_RAW,
            age_cmp="ENVOY_STALE_S",
            none_expr="raw is none",
        )

    def _prep_c1(self, *, age_s: int, gate_on: bool) -> str:
        return apply_envoy_age_substitutes(
            self.c1_state,
            age_s=age_s,
            gate_on=gate_on,
            age_expr=ENVOY_AGE_RAW,
            age_cmp="ENVOY_STALE_S",
            none_expr="raw is none",
        )

    def _prep_house_avail(
        self,
        *,
        age_s: int,
        gate_on: bool,
        p1_stale: bool = False,
        batt_stale: bool = False,
    ) -> str:
        gated = apply_envoy_age_substitutes(
            self.house_avail,
            age_s=age_s,
            gate_on=gate_on,
            age_expr=ENVOY_AGE_RAW_E,
            age_cmp="stale_after_s",
            none_expr="raw_e is none",
        )
        # AGE_S prefix is already prepended; force P1/batt on the body after it.
        prefix, _, body = gated.partition("\n")
        forced = force_p1_batt_inputs(
            body, p1_stale=p1_stale, batt_stale=batt_stale
        )
        return prefix + "\n" + forced

    def test_house_load_availability_contains_p1_batt_for_loop(self) -> None:
        self.assertIn(P1_BATT_FOR_LOOP, self.house_avail)
        self.assertIn("__P1_POWER_NET_ENTITY__", self.house_avail)
        self.assertIn(
            "sensor.__SIGEN_PLANT_SLUG___plant_battery_power",
            self.house_avail,
        )

    def test_envoy_age_1200_gate_on_all_available_c1_off(self) -> None:
        pv_a = eval_ha_template(self._prep_pv_avail(age_s=1200, gate_on=True))
        house = eval_ha_template(
            self._prep_house_avail(age_s=1200, gate_on=True)
        )
        c1 = eval_ha_template(self._prep_c1(age_s=1200, gate_on=True))
        self.assertTrue(_truthy(pv_a), pv_a)
        self.assertTrue(_truthy(house), house)
        self.assertTrue(_falsey(c1), c1)

    def test_envoy_age_1201_gate_on_unavailable_c1_on(self) -> None:
        pv_a = eval_ha_template(self._prep_pv_avail(age_s=1201, gate_on=True))
        house = eval_ha_template(
            self._prep_house_avail(age_s=1201, gate_on=True)
        )
        c1 = eval_ha_template(self._prep_c1(age_s=1201, gate_on=True))
        self.assertTrue(_falsey(pv_a), pv_a)
        self.assertTrue(_falsey(house), house)
        self.assertTrue(_truthy(c1), c1)

    def test_envoy_age_1201_gate_off_pv_zero_available_c1_off(self) -> None:
        pv_state = eval_ha_template(
            self._prep_pv_state(age_s=1201, gate_on=False)
        )
        pv_a = eval_ha_template(self._prep_pv_avail(age_s=1201, gate_on=False))
        house = eval_ha_template(
            self._prep_house_avail(age_s=1201, gate_on=False)
        )
        c1 = eval_ha_template(self._prep_c1(age_s=1201, gate_on=False))
        self.assertEqual(pv_state.strip(), "0")
        self.assertTrue(_truthy(pv_a), pv_a)
        self.assertTrue(_truthy(house), house)
        self.assertTrue(_falsey(c1), c1)

    def test_envoy_age_0_gate_on_all_available_c1_off(self) -> None:
        pv_a = eval_ha_template(self._prep_pv_avail(age_s=0, gate_on=True))
        house = eval_ha_template(
            self._prep_house_avail(age_s=0, gate_on=True)
        )
        c1 = eval_ha_template(self._prep_c1(age_s=0, gate_on=True))
        self.assertTrue(_truthy(pv_a), pv_a)
        self.assertTrue(_truthy(house), house)
        self.assertTrue(_falsey(c1), c1)

    def test_p1_stale_makes_house_unavailable_gate_on_and_off(self) -> None:
        for gate_on in (True, False):
            with self.subTest(gate_on=gate_on):
                house = eval_ha_template(
                    self._prep_house_avail(
                        age_s=0,
                        gate_on=gate_on,
                        p1_stale=True,
                        batt_stale=False,
                    )
                )
                self.assertTrue(_falsey(house), house)

    def test_batt_stale_makes_house_unavailable_gate_on_and_off(self) -> None:
        for gate_on in (True, False):
            with self.subTest(gate_on=gate_on):
                house = eval_ha_template(
                    self._prep_house_avail(
                        age_s=0,
                        gate_on=gate_on,
                        p1_stale=False,
                        batt_stale=True,
                    )
                )
                self.assertTrue(_falsey(house), house)

    def test_envoy_stale_gate_off_house_available_when_p1_batt_fresh(self) -> None:
        house = eval_ha_template(
            self._prep_house_avail(age_s=1201, gate_on=False)
        )
        self.assertTrue(_truthy(house), house)


if __name__ == "__main__":
    unittest.main()
