# EMHASS Envoy Forecast Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Require a fresh Envoy PV reading only when the sun is up, dawn grace has elapsed, the current 30-minute EMHASS PV forecast is material (≥150 W), and MPC is fresh — via one shared `binary_sensor.emhass_envoy_required`.

**Architecture:** Add a single template binary sensor that owns the gate and a deterministic `gate_reason`. PV power, house-load power, and C1 fail-PV consume that entity only. Prove behaviour by evaluating the same Jinja through Home Assistant’s `/api/template` (real HA filters: `as_timestamp`, `is_number`), not a standalone Python reimplementation of the gate.

**Tech Stack:** Home Assistant YAML/Jinja templates, `ruamel.yaml`, `unittest`, existing `scripts/render-ha-packages.sh` / `scripts/deploy-home-assistant.sh`, HA REST `/api/template`.

**Spec:** [docs/superpowers/specs/2026-08-30-emhass-envoy-forecast-gate-design.md](../specs/2026-08-30-emhass-envoy-forecast-gate-design.md)

## Global Constraints

- `sensor.p_pv_forecast` **state** is the **current 30-minute slot PV forecast in watts** (EMHASS publish-data). Do not use forecast attribute rows for the gate; do not treat `last_updated` as freshness.
- Gate material forecast only when `states('sensor.p_pv_forecast') is is_number` **and** value `≥ 150` **and** `binary_sensor.emhass_mpc_fresh` is `on`.
- Guard `as_timestamp(..., default=0)`; fail open when `next_rising` is missing.
- Constants (local `{% set %}` in the shared sensor only): `ENVOY_STALE_S = 20 * 60`, `ENVOY_DAWN_GRACE_S = 30 * 60`, `ENVOY_MATERIAL_FORECAST_W = 150`. Strict `>` for grace and Envoy age (1800 not elapsed; 1801 elapsed; 1200 not stale; 1201 stale).
- **`gate_reason` priority (first match wins, exactly one value):**
  1. `skip_night` — `sun.sun` is not `above_horizon`
  2. `skip_sunrise_unavailable` — sun up and `rising_ts <= 0`
  3. `skip_dawn_grace` — sun up, `rising_ts > 0`, grace not elapsed
  4. `skip_forecast_unavailable` — `p_pv_forecast` is not `is_number`
  5. `skip_low_forecast` — `is_number` and value `< 150`
  6. `skip_mpc_stale` — forecast material and `emhass_mpc_fresh` is not `on`
  7. `require_fresh` — all gate conditions true (`binary_sensor` state `on`)
- One shared entity: `binary_sensor.emhass_envoy_required`. Consumers must not duplicate sunrise / forecast / MPC-fresh logic.
- When Envoy is not required and Envoy is bad/stale, PV contribution is **0 W** (do not substitute forecast into house load).
- Preserve P1 + Sigen battery 20-minute freshness on house-load availability.
- Replace C1 `elevation <= 8` with the shared gate.
- Behaviour tests must call **real HA template rendering**. A separate Python model of the gate is forbidden as the primary oracle. Mechanical test substitutes must use `substitute_once` (exactly one match). For missing `next_rising`, substitute only `state_attr('sun.sun', 'next_rising')` → `none` and keep production `as_timestamp(..., default=0)`.
- Consumer tests must **force** P1 and battery freshness with mechanical substitutes (`P1_INPUT_STALE` / `BATT_INPUT_STALE`); never rely on live HA for those inputs. Assert no unmocked P1/battery freshness checks remain in the evaluated string.
- Offline HA tests: raise/catch `HAUnavailable` for missing credentials or connection failure → **skip**; `EMHASS_HA_TEMPLATE_TESTS=1` → **fail hard**.
- Use `config/site.env` for hosts; do not hardcode IPs or Envoy entity IDs in tracked tests (use placeholders / rendered package).
- Do not create a git commit unless the operator explicitly asks.
- After intentionally red Task 1 tests, **continue to Task 2** unless the operator asks to pause.
- Read `memory-bank/activeContext.md`, `memory-bank/techContext.md`, and `.agents/skills/emhass-energy-stack/SKILL.md` before deploy.

---

## File Map

| File | Role |
|------|------|
| `home-assistant/ha_emhass_sensors.yaml` | Add `binary_sensor.emhass_envoy_required`; rewrite PV + house-load to consume it |
| `home-assistant/ha_emhass_monitoring.yaml` | Rewrite `binary_sensor.emhass_fail_pv_sensor` (C1) to consume the shared gate |
| `scripts/tests/test_ha_emhass_sensors.py` | Structural contracts; remove obsolete `sun_up or raw_pv_w > 50` assertion |
| `scripts/tests/test_emhass_envoy_gate_templates.py` | **New** — HA `/api/template` behaviour tests (boundaries + scenarios) |
| `scripts/lib/ha_template_eval.py` | **New** — thin helper: POST `/api/template` using secrets + site HA URL |
| `scripts/tests/test_emhass_actionable_alerts.py` | Mandatory: C1 consumes `binary_sensor.emhass_envoy_required`; still wires phone via `emhass_fail_pv_sensor` |
| `docs/ENTITY_CONTRACT.md` | Document `binary_sensor.emhass_envoy_required` |
| `docs/superpowers/specs/2026-08-30-emhass-envoy-forecast-gate-design.md` | Record `gate_reason` priority table if not already identical |
| `memory-bank/activeContext.md` | Post-deploy note |

---

### Task 1: HA template eval helper + failing behaviour tests

**Files:**
- Create: `scripts/lib/ha_template_eval.py`
- Create: `scripts/tests/test_emhass_envoy_gate_templates.py`
- Modify: `scripts/tests/test_ha_emhass_sensors.py` (failing structural expectations for the new entity)
- Test: same

**Interfaces:**
- Consumes: HA REST `POST /api/template` with body `{"template": "<jinja>"}`; long-lived token + `hass_url` from `emhass/secrets_emhass.yaml`
- Produces: `HAUnavailable`, `probe_ha_template() -> None`, `eval_ha_template(template: str) -> str`; tests load **production** gate Jinja from YAML

- [ ] **Step 1: Add `scripts/lib/ha_template_eval.py` with probeable skips**

```python
"""Evaluate Jinja via Home Assistant /api/template (real HA template engine)."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class HAUnavailable(Exception):
    """Credentials missing/invalid or HA /api/template unreachable."""


def load_ha_credentials() -> tuple[str, str]:
    path = REPO_ROOT / "emhass" / "secrets_emhass.yaml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HAUnavailable(f"cannot read {path}: {exc}") from exc
    url_m = re.search(r"(?m)^hass_url:\s*(.+)$", text)
    tok_m = re.search(r"(?m)^long_lived_token:\s*(.+)$", text)
    if not url_m or not tok_m:
        raise HAUnavailable("hass_url or long_lived_token missing in secrets_emhass.yaml")
    url = url_m.group(1).strip().strip("\"'")
    token = tok_m.group(1).strip().strip("\"'")
    if not url or not token:
        raise HAUnavailable("hass_url or long_lived_token empty")
    return url.rstrip("/"), token


def eval_ha_template(template: str, *, timeout: float = 30.0) -> str:
    try:
        base, token = load_ha_credentials()
    except HAUnavailable:
        raise
    req = urllib.request.Request(
        f"{base}/api/template",
        data=json.dumps({"template": template}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode().strip()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise HAUnavailable(f"HA /api/template HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise HAUnavailable(f"HA /api/template connection failed: {exc}") from exc
    except TimeoutError as exc:
        raise HAUnavailable(f"HA /api/template timeout: {exc}") from exc


def probe_ha_template() -> None:
    """Raise HAUnavailable if HA template API is not usable."""
    eval_ha_template("{{ 1 + 1 }}")


def require_ha_or_skip(test_case) -> None:
    """unittest helper: skip unless EMHASS_HA_TEMPLATE_TESTS=1 forces hard fail."""
    try:
        probe_ha_template()
    except HAUnavailable as exc:
        if os.environ.get("EMHASS_HA_TEMPLATE_TESTS") == "1":
            raise
        test_case.skipTest(f"HA template API unavailable: {exc}")
```

- [ ] **Step 2: Write failing tests that extract the future gate template**

In `test_emhass_envoy_gate_templates.py`:

- `setUpModule` / per-test: call `require_ha_or_skip(self)` so missing credentials, `URLError`, and timeouts **skip** offline; with `EMHASS_HA_TEMPLATE_TESTS=1` they **fail hard**.
- Helper `extract_envoy_required_templates(package_yaml) -> tuple[state_j2, reason_j2]` finds `default_entity_id: binary_sensor.emhass_envoy_required` (fails until Task 2).

**Mechanical substitution rules (no Python gate model):**

Copy production state/reason Jinja verbatim, then apply **only** these replacements. Assert each pattern occurs **exactly once** in the extracted production string before replace, and **zero** times after:

| Production substring (must remain in YAML) | Test substitute |
|--------------------------------------------|-----------------|
| `is_state('sun.sun', 'above_horizon')` | `true` / `false` |
| `state_attr('sun.sun', 'next_rising')` | `none` **or** a quoted ISO datetime string for grace cases |
| `as_timestamp(now())` | integer `now_ts` literal |
| `states('sensor.p_pv_forecast')` | quoted forecast token |
| `is_state('binary_sensor.emhass_mpc_fresh', 'on')` | `true` / `false` |

**Critical — missing `next_rising`:** substitute **only**
`state_attr('sun.sun', 'next_rising')` → `none`.
**Keep** the production expression
`as_timestamp(..., default=0) | float(0)` intact so the test exercises the real guard. Do **not** replace the whole `as_timestamp(state_attr(...), default=0)` call with a numeric `rising_ts`.

For grace boundary cases, substitute `state_attr('sun.sun', 'next_rising')` with a quoted UTC ISO timestamp whose unix value is `rising_ts`, and replace `as_timestamp(now())` with `now_ts`, so production still runs `as_timestamp(<iso>, default=0)`.

Add a unit assertion helper:

```python
def substitute_once(src: str, old: str, new: str) -> str:
    assert src.count(old) == 1, f"expected exactly one {old!r}, found {src.count(old)}"
    out = src.replace(old, new, 1)
    assert out.count(old) == 0
    return out
```

Example cases:

```python
CASES = [
    # (name, sun_up, next_rising_subst, now_ts, fc, mpc_fresh, expect_reason, expect_on)
    # next_rising_subst: None means literal jinja `none`; str means quoted ISO for as_timestamp
    ("night", False, "2026-08-30T04:00:00+00:00", 1_000_000, "200", True, "skip_night", False),
    ("grace_1799", True, rising_iso, rising_unix + 1799, "200", True, "skip_dawn_grace", False),
    ("grace_1800", True, rising_iso, rising_unix + 1800, "200", True, "skip_dawn_grace", False),
    ("grace_1801", True, rising_iso, rising_unix + 1801, "200", True, "require_fresh", True),
    ("fc_149", True, rising_iso, rising_unix + 2000, "149", True, "skip_low_forecast", False),
    ("fc_150", True, rising_iso, rising_unix + 2000, "150", True, "require_fresh", True),
    ("fc_151", True, rising_iso, rising_unix + 2000, "151", True, "require_fresh", True),
    ("fc_unavailable", True, rising_iso, rising_unix + 2000, "unavailable", True, "skip_forecast_unavailable", False),
    ("fc_nan", True, rising_iso, rising_unix + 2000, "nan", True, "skip_forecast_unavailable", False),
    ("fc_inf", True, rising_iso, rising_unix + 2000, "inf", True, "skip_forecast_unavailable", False),
    ("fc_ninf", True, rising_iso, rising_unix + 2000, "-inf", True, "skip_forecast_unavailable", False),
    ("mpc_stale", True, rising_iso, rising_unix + 2000, "500", False, "skip_mpc_stale", False),
    ("rising_missing", True, None, rising_unix + 2000, "500", True, "skip_sunrise_unavailable", False),
]
```

Also assert priority: night + missing rising → `skip_night` (not `skip_sunrise_unavailable`).

DST cases: fixed unix pairs on Europe/Amsterdam spring-forward and fall-back calendar days; grace still uses `as_timestamp` on ISO + unix `now` substitute.

- [ ] **Step 3: Add failing structural tests in `test_ha_emhass_sensors.py`**

- Assert package defines `binary_sensor.emhass_envoy_required` with `gate_reason` attribute.
- Assert PV + house-load reference `binary_sensor.emhass_envoy_required` and do **not** contain `sun_up or raw_pv_w > 50` or consumer-owned `next_rising` gate logic.
- Assert `30 * 60` and `150` appear on the shared binary sensor; `20 * 60` may appear on shared consumers for Envoy stale age.

- [ ] **Step 4: Run tests — expect fail (red checkpoint)**

```bash
cd /Users/halil/workspace/misc/energy-stack
PYTHONPATH=scripts/lib python3 -m unittest scripts.tests.test_ha_emhass_sensors -v
EMHASS_HA_TEMPLATE_TESTS=1 PYTHONPATH=scripts/lib python3 -m unittest scripts.tests.test_emhass_envoy_gate_templates -v
```

Expected: missing `emhass_envoy_required` / old availability strings → FAIL.

- [ ] **Step 5: Red checkpoint, then continue**

Confirm the failures match the missing entity / old strings (not infra errors). **Do not stop the plan here** unless the operator explicitly asks for a pause. Proceed to Task 2. Commit only if the operator asks.

---

### Task 2: Implement `binary_sensor.emhass_envoy_required`

**Files:**
- Modify: `home-assistant/ha_emhass_sensors.yaml` (top of `template:` sensor/binary_sensor list)
- Modify: `docs/superpowers/specs/2026-08-30-emhass-envoy-forecast-gate-design.md` (add gate_reason priority table matching Global Constraints)
- Test: `scripts/tests/test_emhass_envoy_gate_templates.py`, `scripts/tests/test_ha_emhass_sensors.py`

**Interfaces:**
- Produces: `binary_sensor.emhass_envoy_required` state `on`/`off`; attr `gate_reason` per priority list

- [ ] **Step 1: Add binary sensor to `ha_emhass_sensors.yaml`**

Place under `template:` as a `binary_sensor` entry (same file as PV sensors):

```yaml
      - name: EMHASS Envoy required
        unique_id: emhass_envoy_required
        default_entity_id: binary_sensor.emhass_envoy_required
        device_class: safety
        state: >
          {% set ENVOY_DAWN_GRACE_S = 30 * 60 %}
          {% set ENVOY_MATERIAL_FORECAST_W = 150 %}
          {% set sun_up = is_state('sun.sun', 'above_horizon') %}
          {% set rising_ts = as_timestamp(state_attr('sun.sun', 'next_rising'), default=0) | float(0) %}
          {% set dawn_grace_elapsed =
               rising_ts > 0
               and (as_timestamp(now()) - (rising_ts - 86400)) > ENVOY_DAWN_GRACE_S %}
          {% set fc = states('sensor.p_pv_forecast') %}
          {% set forecast_material =
               fc is is_number
               and (fc | float(0)) >= ENVOY_MATERIAL_FORECAST_W %}
          {% set mpc_fresh = is_state('binary_sensor.emhass_mpc_fresh', 'on') %}
          {{ sun_up and dawn_grace_elapsed and forecast_material and mpc_fresh }}
        attributes:
          gate_reason: >
            {% set ENVOY_DAWN_GRACE_S = 30 * 60 %}
            {% set ENVOY_MATERIAL_FORECAST_W = 150 %}
            {% set sun_up = is_state('sun.sun', 'above_horizon') %}
            {% set rising_ts = as_timestamp(state_attr('sun.sun', 'next_rising'), default=0) | float(0) %}
            {% set dawn_grace_elapsed =
                 rising_ts > 0
                 and (as_timestamp(now()) - (rising_ts - 86400)) > ENVOY_DAWN_GRACE_S %}
            {% set fc = states('sensor.p_pv_forecast') %}
            {% set forecast_ok = fc is is_number %}
            {% set forecast_material =
                 forecast_ok and (fc | float(0)) >= ENVOY_MATERIAL_FORECAST_W %}
            {% set mpc_fresh = is_state('binary_sensor.emhass_mpc_fresh', 'on') %}
            {% if not sun_up %}
              skip_night
            {% elif rising_ts <= 0 %}
              skip_sunrise_unavailable
            {% elif not dawn_grace_elapsed %}
              skip_dawn_grace
            {% elif not forecast_ok %}
              skip_forecast_unavailable
            {% elif not forecast_material %}
              skip_low_forecast
            {% elif not mpc_fresh %}
              skip_mpc_stale
            {% else %}
              require_fresh
            {% endif %}
```

Add a short YAML comment above the sensor:

```yaml
# p_pv_forecast state = current 30-minute EMHASS PV forecast (W).
# Freshness = binary_sensor.emhass_mpc_fresh (not p_pv_forecast.last_updated).
```

- [ ] **Step 2: Sync spec** with the same priority list and `p_pv_forecast` definition one-liner.

- [ ] **Step 3: Re-run Task 1 tests** that only need the shared sensor extract — gate behaviour cases that mock entity reads should pass for reason/on once extract works.

```bash
EMHASS_HA_TEMPLATE_TESTS=1 PYTHONPATH=scripts/lib python3 -m unittest scripts.tests.test_emhass_envoy_gate_templates -v
```

Expected: gate reason/on cases PASS; consumer structural tests may still FAIL.

---

### Task 3: Wire PV, house-load, and C1 consumers

**Files:**
- Modify: `home-assistant/ha_emhass_sensors.yaml` (`emhass_pv_power_w`, `emhass_house_load_power_w`)
- Modify: `home-assistant/ha_emhass_monitoring.yaml` (`emhass_fail_pv_sensor`)
- Modify: `docs/ENTITY_CONTRACT.md`
- Modify: `scripts/tests/test_ha_emhass_sensors.py`
- Modify: `scripts/tests/test_emhass_actionable_alerts.py` (**mandatory** C1 → shared gate)
- Modify: `scripts/tests/test_emhass_envoy_gate_templates.py` (consumer behaviour via `/api/template`)
- Test: same

**Interfaces:**
- Consumes: `is_state('binary_sensor.emhass_envoy_required', 'on')`
- Produces: availability/state behaviour per spec

- [ ] **Step 1: Rewrite `sensor.emhass_pv_power_w`**

```yaml
        state: >
          {% set raw_envoy = '__ENVOY_PV_ENTITY__' %}
          {% set ENVOY_STALE_S = 20 * 60 %}
          {% set required = is_state('binary_sensor.emhass_envoy_required', 'on') %}
          {% set raw = states[raw_envoy] %}
          {% set bad = ['unknown', 'unavailable', 'none', ''] %}
          {% set envoy_bad = states(raw_envoy) in bad %}
          {% set envoy_stale = raw is none
             or (now() - raw.last_updated).total_seconds() > ENVOY_STALE_S %}
          {% set raw_pv_w = states(raw_envoy) | float(0) * 1000 %}
          {{ 0 if ((not required) and (envoy_stale or envoy_bad)) else raw_pv_w | round(0) }}
        availability: >
          {% set raw_envoy = '__ENVOY_PV_ENTITY__' %}
          {% set ENVOY_STALE_S = 20 * 60 %}
          {% set required = is_state('binary_sensor.emhass_envoy_required', 'on') %}
          {% set raw = states[raw_envoy] %}
          {% set bad = ['unknown', 'unavailable', 'none', ''] %}
          {% set envoy_bad = states(raw_envoy) in bad %}
          {% set envoy_stale = raw is none
             or (now() - raw.last_updated).total_seconds() > ENVOY_STALE_S %}
          {% if not required %}
            true
          {% else %}
            {{ not (envoy_bad or envoy_stale) }}
          {% endif %}
```

- [ ] **Step 2: Rewrite `sensor.emhass_house_load_power_w`**

- State: `pv = 0` when `(not required) and (envoy_stale or envoy_bad)`, else Envoy×1000; keep `grid + pv - batt` clamp.
- Availability: P1 + battery freshness unchanged; Envoy freshness **only if** `required`.

- [ ] **Step 3: Rewrite C1 `binary_sensor.emhass_fail_pv_sensor`**

Remove `elevation <= 8`. New state:

```yaml
        state: >
          {% set raw_envoy = '__ENVOY_PV_ENTITY__' %}
          {% set ENVOY_STALE_S = 20 * 60 %}
          {% if not is_state('binary_sensor.emhass_envoy_required', 'on') %}
            false
          {% else %}
            {% set raw = states[raw_envoy] %}
            {% set bad = ['unknown', 'unavailable', 'none', ''] %}
            {{ raw is none
               or states(raw_envoy) in bad
               or (now() - raw.last_updated).total_seconds() > ENVOY_STALE_S }}
          {% endif %}
```

- [ ] **Step 4: Mandatory structural proofs**

In `test_ha_emhass_sensors.py` and `test_emhass_actionable_alerts.py`:

- C1 state template **must** contain `binary_sensor.emhass_envoy_required`.
- C1 state template **must not** contain `elevation`.
- PV and house-load **must** contain `binary_sensor.emhass_envoy_required` and **must not** contain `next_rising` or `p_pv_forecast` gate logic.
- Phone path still uses `binary_sensor.emhass_fail_pv_sensor` / existing C1 escalate automations (no new alert id).

- [ ] **Step 5: Document entity in `docs/ENTITY_CONTRACT.md`**

| Entity | Source | Role |
|--------|--------|------|
| `binary_sensor.emhass_envoy_required` | energy-stack templates | Envoy freshness required when forecast material + MPC fresh + dawn grace |

- [ ] **Step 6: Consumer `/api/template` behaviour tests**

Extract production PV state, PV availability, house-load availability, and C1 state Jinja. Use `substitute_once` for gate and Envoy controls. **Do not leave P1 or battery freshness dependent on live HA state.**

**Envoy / gate substitutes (each exactly once where present):**

- `is_state('binary_sensor.emhass_envoy_required', 'on')` → `true` / `false`
- Envoy age: replace  
  `(now() - raw.last_updated).total_seconds() > ENVOY_STALE_S`  
  (or the house-load / PV equivalent using `raw_e` / `stale_after_s` after rewrite — match production substring exactly)  
  with `AGE_S > ENVOY_STALE_S` (or `AGE_S > stale_after_s`), and prepend `{% set AGE_S = 1200 %}` (or 1201 / 0) in the test only.
- When testing age alone: `states(raw_envoy) in bad` → `false`; `raw is none` / `raw_e is none` → `false` (each exactly once).

**P1 / battery freshness substitutes (mandatory, deterministic):**

House-load availability’s production loop (both `__P1_POWER_NET_ENTITY__` and `sensor.__SIGEN_PLANT_SLUG___plant_battery_power`) must not run against live `states[...]`.

1. Assert production house-load availability still contains the for-loop over those two placeholders.
2. `substitute_once` the **entire** `{% for entity_id in [ ... ] %} ... {% endfor %}` block with:

```jinja
{% if P1_INPUT_STALE %}
  {% set ns.ok = false %}
{% endif %}
{% if BATT_INPUT_STALE %}
  {% set ns.ok = false %}
{% endif %}
```

3. Prepend `{% set P1_INPUT_STALE = false %}` / `true` and `{% set BATT_INPUT_STALE = false %}` / `true` per case.
4. After substitution, assert the evaluated template string contains **no** remaining:
   - `__P1_POWER_NET_ENTITY__`
   - `sensor.__SIGEN_PLANT_SLUG___plant_battery_power`
   - `(now() - entity.last_updated)` (or any unmocked entity freshness check from that loop)

**Case → forced inputs:**

| Case | `P1_INPUT_STALE` | `BATT_INPUT_STALE` | Gate / Envoy |
|------|------------------|--------------------|--------------|
| Envoy-focused (ages 0 / 1200 / 1201, gate on or off) | `false` | `false` | as case requires |
| Independent P1 failure | `true` | `false` | gate on **and** gate off (two runs) |
| Independent battery failure | `false` | `true` | gate on **and** gate off (two runs) |

**Required expectations:**

| Case | Expect |
|------|--------|
| Envoy age 1200, gate on, P1+batt forced fresh | PV available; house-load available; C1 off |
| Envoy age 1201, gate on, P1+batt forced fresh | PV unavailable; house-load unavailable; C1 on |
| Envoy age 1201, gate off, P1+batt forced fresh | PV state `0`; PV available; house-load available; C1 off |
| Envoy age 0, gate on, P1+batt forced fresh | PV available; house-load available; C1 off |
| P1 forced stale, batt fresh, gate off | house-load **unavailable** |
| P1 forced stale, batt fresh, gate on | house-load **unavailable** |
| Batt forced stale, P1 fresh, gate off | house-load **unavailable** |
| Batt forced stale, P1 fresh, gate on | house-load **unavailable** |
| Envoy age 1201, gate off, P1+batt forced fresh | house-load **available** (Envoy silence ignored) |

- [ ] **Step 7: Run full focused suite**

```bash
./scripts/render-ha-packages.sh
PYTHONPATH=scripts/lib python3 -m unittest \
  scripts.tests.test_ha_emhass_sensors \
  scripts.tests.test_emhass_actionable_alerts \
  -v
EMHASS_HA_TEMPLATE_TESTS=1 PYTHONPATH=scripts/lib python3 -m unittest \
  scripts.tests.test_emhass_envoy_gate_templates -v
```

Expected: all PASS when HA is reachable with `EMHASS_HA_TEMPLATE_TESTS=1`.

---

### Task 4: Render, HA config check, deploy, live sunrise acceptance

**Files:**
- Modify: `memory-bank/activeContext.md` (after verify)
- No energy-host cron changes

- [ ] **Step 1: Render**

```bash
./scripts/render-ha-packages.sh
```

Expected: exit 0; `.rendered/home-assistant/ha_emhass_sensors.yaml` contains `emhass_envoy_required` and site Envoy entity id (not placeholder).

- [ ] **Step 2: Deploy HA packages**

```bash
./scripts/deploy-home-assistant.sh
```

Expected: container config check passes; HA restarts clean; backup path printed.

- [ ] **Step 3: Live smoke (any time of day)**

Via HA states API or Developer Tools:

1. `binary_sensor.emhass_envoy_required` exists with `gate_reason` in the priority enum.
2. At night: reason `skip_night`, sensor `off`; `sensor.emhass_house_load_sample_w` available.
3. `sensor.p_pv_forecast` documented/understood as current 30-minute W value matching active plan step.
4. With master/MPC healthy midday and forecast ≥150: reason `require_fresh` when grace long elapsed.

- [ ] **Step 4: Live sunrise acceptance (next clear dawn, or wait through grace)**

Checklist:

| Check | Pass criteria |
|-------|----------------|
| Through sunrise + 30 min while forecast &lt;150 or MPC not fresh | `gate_reason` in `skip_dawn_grace` / `skip_low_forecast` / `skip_mpc_stale`; house-load sample keeps `sampled_at` advancing; no MPC abort solely from Envoy silence |
| After grace with forecast ≥150 and `emhass_mpc_fresh=on` | `gate_reason=require_fresh`; if Envoy still age&gt;20 min, PV/house-load unavailable and C1 on |
| Missing `next_rising` simulation not required live if startup already covered by template test | — |

- [ ] **Step 5: Update `memory-bank/activeContext.md`** with deploy time, backup path, and sunrise acceptance result (or “pending next dawn”).

- [ ] **Step 6: Commit only if the operator asks.**

---

## Spec coverage (self-review)

| Spec / review requirement | Task |
|---------------------------|------|
| Shared `binary_sensor.emhass_envoy_required` | 2 |
| Deterministic `gate_reason` priority | Global Constraints + 2 |
| Guarded `as_timestamp`, `is_number`, `emhass_mpc_fresh` | 2 |
| Missing-`next_rising` tests keep production `as_timestamp(..., default=0)` | 1 |
| `substitute_once` exact-count replacements | 1 |
| `HAUnavailable` + probe skip / force-fail | 1 |
| `p_pv_forecast` = current 30-minute W | Global Constraints + YAML comment + 4 smoke |
| PV / house-load / C1 consumers | 3 |
| Consumer behaviour: gate off/on × Envoy stale/fresh; P1/batt independent | 3 |
| P1/batt freshness forced via mechanical substitutes (no live HA) | 3 Step 6 |
| Assert no unmocked P1/batt freshness checks remain in consumer templates | 3 Step 6 |
| Mandatory C1 consumes shared gate (structural) | 3 |
| `nan` / `inf` / `-inf` / `unavailable` forecast | 1 |
| Fail open on missing `next_rising` | 2 + template tests |
| Boundaries 1799/1800/1801, 149/150/151, 1200/1201 | 1 + 3 |
| Stale numeric forecast + mpc_fresh off | 1 |
| HA template behaviour tests (not Python gate model) | 1 (`/api/template`) |
| Red tests checkpoint without stopping plan | 1 Step 5 |
| Render + config-check deploy + live sunrise | 4 |
| ENTITY_CONTRACT + memory bank | 3, 4 |

**Placeholder scan:** none intentional. Operator-gated commits are explicit.

**Type/name consistency:** entity id `binary_sensor.emhass_envoy_required`; attr `gate_reason`; `HAUnavailable` / `probe_ha_template` / `require_ha_or_skip`; reason strings match Global Constraints throughout.
