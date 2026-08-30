"""Semantic contract tests for EMHASS Home Assistant sensors."""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "home-assistant" / "ha_emhass_sensors.yaml"
MONITORING_PACKAGE = REPO_ROOT / "home-assistant" / "ha_emhass_monitoring.yaml"
SAMPLE_ENTITY_ID = "sensor.emhass_house_load_sample_w"
RAW_ENTITY_ID = "sensor.emhass_house_load_power_w"
PV_ENTITY_ID = "sensor.emhass_pv_power_w"
ENVOY_REQUIRED_ID = "binary_sensor.emhass_envoy_required"
C1_FAIL_PV_ID = "binary_sensor.emhass_fail_pv_sensor"
LEGACY_ENVOY_GATE = "sun_up or raw_pv_w > 50"


def load_package(path: Path = PACKAGE) -> Mapping[str, Any]:
    with path.open(encoding="utf-8") as stream:
        document = YAML(typ="safe").load(stream)
    if not isinstance(document, Mapping):
        raise TypeError("HA sensor package root must be a mapping")
    return document


def find_template_sensors(
    package: Mapping[str, Any],
    entity_id: str,
    *,
    kind: str = "sensor",
) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    matches: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for template_item in package.get("template", []):
        if not isinstance(template_item, Mapping):
            continue
        for sensor in template_item.get(kind, []):
            if (
                isinstance(sensor, Mapping)
                and sensor.get("default_entity_id") == entity_id
            ):
                matches.append((template_item, sensor))
    return matches


class EmhassLoadSampleSensorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.package = load_package()

    def _find_sensors(
        self, entity_id: str, *, kind: str = "sensor"
    ) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
        return find_template_sensors(self.package, entity_id, kind=kind)

    def sample_sensor(self) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        matches = self._find_sensors(SAMPLE_ENTITY_ID)
        self.assertEqual(
            len(matches),
            1,
            f"expected exactly one {SAMPLE_ENTITY_ID} template sensor",
        )
        return matches[0]

    def raw_sensor(self) -> Mapping[str, Any]:
        matches = self._find_sensors(RAW_ENTITY_ID)
        self.assertEqual(
            len(matches),
            1,
            f"expected exactly one preserved {RAW_ENTITY_ID} template sensor",
        )
        return matches[0][1]

    def pv_sensor(self) -> Mapping[str, Any]:
        matches = self._find_sensors(PV_ENTITY_ID)
        self.assertEqual(
            len(matches),
            1,
            f"expected exactly one {PV_ENTITY_ID} template sensor",
        )
        return matches[0][1]

    def envoy_required_sensor(self) -> Mapping[str, Any]:
        matches = self._find_sensors(ENVOY_REQUIRED_ID, kind="binary_sensor")
        self.assertEqual(
            len(matches),
            1,
            f"expected exactly one {ENVOY_REQUIRED_ID} binary sensor",
        )
        return matches[0][1]

    def test_sample_sensor_uses_minute_trigger(self) -> None:
        template_item, _ = self.sample_sensor()
        self.assertEqual(
            template_item.get("trigger"),
            [{"platform": "time_pattern", "seconds": "0"}],
        )

    def test_sample_sensor_has_power_metadata(self) -> None:
        _, sensor = self.sample_sensor()
        self.assertEqual(sensor.get("name"), "EMHASS house load sample")
        self.assertEqual(sensor.get("unique_id"), "emhass_house_load_sample_w")
        self.assertEqual(sensor.get("unit_of_measurement"), "W")
        self.assertEqual(sensor.get("device_class"), "power")
        self.assertEqual(sensor.get("state_class"), "measurement")

    def test_sample_sensor_uses_numeric_source_and_forces_update(self) -> None:
        _, sensor = self.sample_sensor()
        raw_sensor = self.raw_sensor()
        self.assertEqual(raw_sensor.get("name"), "EMHASS house load power")
        self.assertEqual(
            sensor.get("state"),
            "{{ states('sensor.emhass_house_load_power_w')"
            " | float(0) | round(0) }}\n",
        )
        availability = " ".join(str(sensor.get("availability", "")).split())
        self.assertEqual(
            availability,
            "{% set value = states('sensor.emhass_house_load_power_w') %} "
            "{{ value not in ['unknown', 'unavailable', 'none', ''] "
            "and (value | float(none)) is not none }}",
        )
        self.assertEqual(
            sensor.get("attributes"),
            {"sampled_at": "{{ now().isoformat() }}"},
        )

    def test_raw_house_load_requires_live_p1_battery_and_daylight_pv(self) -> None:
        sensor = self.raw_sensor()
        state = " ".join(str(sensor.get("state", "")).split())
        availability = " ".join(
            str(sensor.get("availability", "")).split()
        )
        self.assertIn("__P1_POWER_NET_ENTITY__", state)
        self.assertIn(
            "sensor.__SIGEN_PLANT_SLUG___plant_battery_power",
            state,
        )
        self.assertIn("__ENVOY_PV_ENTITY__", state)
        self.assertIn("__P1_POWER_NET_ENTITY__", availability)
        self.assertIn(
            "sensor.__SIGEN_PLANT_SLUG___plant_battery_power",
            availability,
        )
        self.assertIn("__ENVOY_PV_ENTITY__", availability)
        self.assertIn("20 * 60", availability)
        self.assertIn(ENVOY_REQUIRED_ID, state)
        self.assertIn(ENVOY_REQUIRED_ID, availability)
        self.assertNotIn(LEGACY_ENVOY_GATE, state)
        self.assertNotIn(LEGACY_ENVOY_GATE, availability)
        self.assertNotIn("next_rising", state)
        self.assertNotIn("next_rising", availability)
        self.assertNotIn("p_pv_forecast", state)
        self.assertNotIn("p_pv_forecast", availability)
        self.assertNotIn("is_state('sun.sun'", state)
        self.assertNotIn("is_state('sun.sun'", availability)

    def test_envoy_required_binary_sensor_owns_gate(self) -> None:
        sensor = self.envoy_required_sensor()
        state = str(sensor.get("state", ""))
        attrs = sensor.get("attributes")
        self.assertIsInstance(attrs, Mapping)
        self.assertIn("gate_reason", attrs)
        reason = str(attrs["gate_reason"])
        self.assertIn("30 * 60", state)
        self.assertIn("150", state)
        self.assertIn("30 * 60", reason)
        self.assertIn("150", reason)
        self.assertIn("state_attr('sun.sun', 'next_rising')", state)
        self.assertIn("states('sensor.p_pv_forecast')", state)
        self.assertIn("is is_number", state)
        self.assertIn("is is_number", reason)
        self.assertIn("binary_sensor.emhass_mpc_fresh", state)
        self.assertIn("binary_sensor.emhass_mpc_fresh", reason)
        self.assertIn(
            "is_state('binary_sensor.emhass_mpc_fresh', 'on')",
            state,
        )
        self.assertIn(
            "is_state('binary_sensor.emhass_mpc_fresh', 'on')",
            reason,
        )

    def test_pv_sensor_consumes_shared_envoy_gate(self) -> None:
        sensor = self.pv_sensor()
        state = " ".join(str(sensor.get("state", "")).split())
        availability = " ".join(
            str(sensor.get("availability", "")).split()
        )
        self.assertIn("__ENVOY_PV_ENTITY__", state)
        self.assertIn(ENVOY_REQUIRED_ID, state)
        self.assertIn(ENVOY_REQUIRED_ID, availability)
        self.assertNotIn(LEGACY_ENVOY_GATE, state)
        self.assertNotIn(LEGACY_ENVOY_GATE, availability)
        self.assertNotIn("next_rising", state)
        self.assertNotIn("next_rising", availability)
        self.assertNotIn("p_pv_forecast", state)
        self.assertNotIn("p_pv_forecast", availability)
        self.assertNotIn("is_state('sun.sun'", state)
        self.assertNotIn("is_state('sun.sun'", availability)
        self.assertIn("20 * 60", state + availability)


class C1FailPvSensorGateTests(unittest.TestCase):
    """C1 must consume shared envoy gate; no elevation heuristic (Step 4)."""

    def test_c1_state_consumes_shared_envoy_gate_not_elevation(self) -> None:
        package = load_package(MONITORING_PACKAGE)
        matches = find_template_sensors(
            package, C1_FAIL_PV_ID, kind="binary_sensor"
        )
        self.assertEqual(
            len(matches),
            1,
            f"expected exactly one {C1_FAIL_PV_ID} binary sensor",
        )
        state = str(matches[0][1].get("state", ""))
        self.assertIn(ENVOY_REQUIRED_ID, state)
        self.assertNotIn("elevation", state)


if __name__ == "__main__":
    unittest.main()
