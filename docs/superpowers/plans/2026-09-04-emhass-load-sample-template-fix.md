# EMHASS House Load Sample Template Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Jinja string-coercion of trigger variables in `sensor.emhass_house_load_sample_w` so house load samples evaluate as valid, unblocking EMHASS MPC.

**Architecture:** Replace intermediate `TemplateState` variables (`p1_state`, `batt_state`, `envoy_state`) in `home-assistant/ha_emhass_sensors.yaml` with direct entity property queries against `states[entity_id]`. Add dynamic Home Assistant `/api/template` test coverage using `eval_ha_template` to ensure template evaluations produce valid numeric ages and `load_quality != 'invalid'`. Deploy to Home Assistant and verify live MPC execution.

**Tech Stack:** Home Assistant YAML / Jinja2 templates, Python `unittest`, REST API.

## Global Constraints

- Never commit secrets (`config/site.env`, `emhass/secrets_emhass.yaml`).
- Minimal, focused diffs matching existing YAML/Python style.
- Follow fail-closed MPC quality rules: `load_quality` must be `valid`, `valid_noise_clamp`, or `held_estimate`.

---

### Task 1: Update Static Contract Tests for Template Variables

**Files:**
- Modify: `scripts/tests/test_ha_emhass_sensors.py:120-175`

**Interfaces:**
- Consumes: `home-assistant/ha_emhass_sensors.yaml`
- Produces: Passing static test assertions for direct state property lookups in `variables:`.

- [ ] **Step 1: Write updated test assertions**

In `scripts/tests/test_ha_emhass_sensors.py`, update `test_sample_sensor_has_trigger_variables_block`:
Remove `p1_state`, `batt_state`, `envoy_state` from the required keys in `variables`.
Update transport freshness assertions to expect `states[p1_entity].last_reported`, `states[batt_entity].last_reported`, `states[envoy_entity].last_reported`, and `states[envoy_entity].last_updated`.

```python
    def test_sample_sensor_has_trigger_variables_block(self) -> None:
        template_item, _ = self.sample_sensor()
        variables = template_item.get("variables")
        self.assertIsInstance(
            variables,
            Mapping,
            "sample sensor trigger must define a variables block",
        )
        assert isinstance(variables, Mapping)
        for key in (
            "p1_entity",
            "batt_entity",
            "envoy_entity",
            "envoy_req",
            "bad",
            "p1_bad",
            "batt_bad",
            "envoy_bad",
            "now_ts",
            "p1_age",
            "batt_age",
            "envoy_report_age",
            "envoy_value_age",
            "sources_stale",
            "source_spread",
            "grid_w",
            "batt_w",
            "pv_w",
            "raw_bal",
            "prev_valid_ts",
            "prev_valid_w",
            "hold_age",
            "quality_and_val",
        ):
            self.assertIn(key, variables, f"variables must define {key}")

        for removed in ("p1_state", "batt_state", "envoy_state"):
            self.assertNotIn(
                removed,
                variables,
                f"{removed} must not be stored as an intermediate variable to prevent Jinja string coercion",
            )

        # Transport freshness uses states[entity].last_reported / last_updated
        self.assertIn("states[p1_entity].last_reported", str(variables["p1_age"]))
        self.assertIn("states[batt_entity].last_reported", str(variables["batt_age"]))
        self.assertIn(
            "states[envoy_entity].last_reported", str(variables["envoy_report_age"])
        )
        self.assertIn(
            "states[envoy_entity].last_updated", str(variables["envoy_value_age"])
        )
```

- [ ] **Step 2: Run test to verify it fails on current YAML**

Run:
```bash
/Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest scripts/tests/test_ha_emhass_sensors.py
```
Expected: FAIL with assertion error that `p1_state` should not be in variables or `states[p1_entity].last_reported` not found.

---

### Task 2: Fix Template Variable Resolution in `ha_emhass_sensors.yaml`

**Files:**
- Modify: `home-assistant/ha_emhass_sensors.yaml:140-165`

**Interfaces:**
- Consumes: Direct entity IDs in trigger variables.
- Produces: Primitive types in trigger variables without intermediate `TemplateState` stringification.

- [ ] **Step 1: Edit `home-assistant/ha_emhass_sensors.yaml`**

Replace lines 148–162 in `home-assistant/ha_emhass_sensors.yaml`:
```yaml
      bad: "{{ ['unknown', 'unavailable', 'none', ''] }}"
      p1_bad: "{{ states[p1_entity] is none or states[p1_entity].state in bad }}"
      batt_bad: "{{ states[batt_entity] is none or states[batt_entity].state in bad }}"
      envoy_bad: "{{ states[envoy_entity] is none or states[envoy_entity].state in bad }}"
      now_ts: "{{ as_timestamp(now()) }}"
      p1_age: "{{ (now_ts - as_timestamp(states[p1_entity].last_reported, default=0)) if (states[p1_entity] is not none and states[p1_entity].last_reported is defined) else 9999 }}"
      batt_age: "{{ (now_ts - as_timestamp(states[batt_entity].last_reported, default=0)) if (states[batt_entity] is not none and states[batt_entity].last_reported is defined) else 9999 }}"
      envoy_report_age: "{{ (now_ts - as_timestamp(states[envoy_entity].last_reported, default=0)) if (states[envoy_entity] is not none and states[envoy_entity].last_reported is defined) else 9999 }}"
      envoy_value_age: "{{ (now_ts - as_timestamp(states[envoy_entity].last_updated, default=0)) if (states[envoy_entity] is not none and states[envoy_entity].last_updated is defined) else 9999 }}"
      sources_stale: >
        {{ p1_bad or batt_bad or p1_age > 180 or batt_age > 180 or (envoy_req and (envoy_bad or envoy_report_age > 180)) }}
```

- [ ] **Step 2: Run static test to verify it passes**

Run:
```bash
/Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest scripts/tests/test_ha_emhass_sensors.py
```
Expected: PASS.

---

### Task 3: Add Dynamic Home Assistant Template Evaluation Test

**Files:**
- Create: `scripts/tests/test_ha_emhass_load_sample_template.py`

**Interfaces:**
- Consumes: `eval_ha_template`, `require_ha_or_skip` from `scripts/lib/ha_template_eval.py`.
- Produces: Verified evaluation of the Jinja logic directly against Home Assistant's `/api/template`.

- [ ] **Step 1: Implement `test_ha_emhass_load_sample_template.py`**

```python
"""Live HA /api/template test for sensor.emhass_house_load_sample_w logic."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from ruamel.yaml import YAML

from ha_template_eval import eval_ha_template, require_ha_or_skip

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "home-assistant" / "ha_emhass_sensors.yaml"


class EmhassLoadSampleTemplateEvalTests(unittest.TestCase):
    def setUp(self) -> None:
        require_ha_or_skip(self)
        with PACKAGE.open(encoding="utf-8") as stream:
            doc = YAML(typ="safe").load(stream)
        trigger_block = next(
            item
            for item in doc.get("template", [])
            if isinstance(item, dict)
            and item.get("sensor", [{}])[0].get("default_entity_id")
            == "sensor.emhass_house_load_sample_w"
        )
        self.variables = trigger_block["variables"]

    def test_live_template_evaluates_fresh_sources(self) -> None:
        """Render the variable block through Home Assistant and verify sources are not stale."""
        # Build Jinja template string substituting site placeholders with live entities
        template_str = """
{% set p1_entity = 'sensor.electricity_meter_power_net' %}
{% set batt_entity = 'sensor.sigen_0_plant_battery_power' %}
{% set envoy_entity = 'sensor.envoy_122043000054_current_power_production' %}
{% set envoy_req = is_state('binary_sensor.emhass_envoy_required', 'on') %}
{% set bad = ['unknown', 'unavailable', 'none', ''] %}
{% set p1_bad = states[p1_entity] is none or states[p1_entity].state in bad %}
{% set batt_bad = states[batt_entity] is none or states[batt_entity].state in bad %}
{% set envoy_bad = states[envoy_entity] is none or states[envoy_entity].state in bad %}
{% set now_ts = as_timestamp(now()) %}
{% set p1_age = (now_ts - as_timestamp(states[p1_entity].last_reported, default=0)) if (states[p1_entity] is not none and states[p1_entity].last_reported is defined) else 9999 %}
{% set batt_age = (now_ts - as_timestamp(states[batt_entity].last_reported, default=0)) if (states[batt_entity] is not none and states[batt_entity].last_reported is defined) else 9999 %}
{% set envoy_report_age = (now_ts - as_timestamp(states[envoy_entity].last_reported, default=0)) if (states[envoy_entity] is not none and states[envoy_entity].last_reported is defined) else 9999 %}
{% set envoy_value_age = (now_ts - as_timestamp(states[envoy_entity].last_updated, default=0)) if (states[envoy_entity] is not none and states[envoy_entity].last_updated is defined) else 9999 %}
{% set sources_stale = p1_bad or batt_bad or p1_age > 180 or batt_age > 180 or (envoy_req and (envoy_bad or envoy_report_age > 180)) %}
{% set source_spread = ([p1_age, batt_age, envoy_report_age] | max - [p1_age, batt_age, envoy_report_age] | min) if envoy_req else ([p1_age, batt_age] | max - [p1_age, batt_age] | min) %}
{% set grid_w = states(p1_entity) | float(0) %}
{% set batt_w = states(batt_entity) | float(0) %}
{% set pv_w = 0 if ((not envoy_req) and envoy_bad) else (states(envoy_entity) | float(0) * 1000) %}
{% set raw_bal = grid_w + pv_w - batt_w %}
{{ {
  "p1_age": p1_age,
  "batt_age": batt_age,
  "sources_stale": sources_stale,
  "source_spread": source_spread
} | tojson }}
"""
        result_raw = eval_ha_template(template_str)
        result = json.loads(result_raw)

        self.assertFalse(
            result["sources_stale"],
            f"sources_stale evaluated to True: {result}",
        )
        self.assertLess(
            result["p1_age"],
            180.0,
            f"p1_age is >= 180s: {result['p1_age']}",
        )
        self.assertLess(
            result["batt_age"],
            180.0,
            f"batt_age is >= 180s: {result['batt_age']}",
        )
```

- [ ] **Step 2: Run the test to verify it passes against HA**

Run:
```bash
PYTHONPATH=scripts/lib /Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest scripts/tests/test_ha_emhass_load_sample_template.py
```
Expected: PASS.

---

### Task 4: Package Rendering & Full Suite Regression

**Files:**
- Render: `home-assistant/ha_emhass_sensors.yaml` -> rendered output

- [ ] **Step 1: Render HA packages**

Run:
```bash
./scripts/render-ha-packages.sh
```
Expected: Packages rendered without errors.

- [ ] **Step 2: Run all repository tests**

Run:
```bash
/Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest scripts/tests/test_ha_emhass_sensors.py
PYTHONPATH=scripts/lib /Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest scripts/tests/test_emhass_envoy_gate_templates.py
PYTHONPATH=scripts/lib /Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest scripts/tests/test_ha_emhass_load_sample_template.py
/Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest scripts/tests/test_emhass_trading_dashboard.py
/Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest scripts/tests/test_emhass_actionable_alerts.py
/Users/halil/workspace/misc/reterminal/.venv/bin/python -m unittest emhass-cron/tests/test_fetch_prices_emhass.py
```
Expected: All suites PASS.

---

### Task 5: Deploy to Home Assistant & Live Verification

**Files:**
- Deploy to `ha` host (`/home/halil/homeassistant/config/ha_emhass_sensors.yaml`)

- [ ] **Step 1: Deploy to Home Assistant**

Run:
```bash
./scripts/deploy-home-assistant.sh
```
Expected: Successful upload, HA config check passed, package updated.

- [ ] **Step 2: Verify `sensor.emhass_house_load_sample_w` on HA**

Query HA entity state via `emhass-cron`:
```bash
ssh halil@192.168.0.135 'docker exec emhass-cron python3 -c "
import json, re, urllib.request

with open(\"/secrets/secrets_emhass.yaml\") as f:
    text = f.read()

m_url = re.search(r\"hass_url:\s*[\x22\x27]?([^\x22\x27\n\r#]+)\", text)
m_tok = re.search(r\"long_lived_token:\s*[\x22\x27]?([^\x22\x27\n\r#]+)\", text)

url = m_url.group(1).strip().rstrip(\"/\")
token = m_tok.group(1).strip()

req = urllib.request.Request(url + \"/api/states/sensor.emhass_house_load_sample_w\", headers={\"Authorization\": \"Bearer \" + token})
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode())
    print(\"load_quality:\", data.get(\"attributes\", {}).get(\"load_quality\"))
    print(\"invalid_reason:\", data.get(\"attributes\", {}).get(\"invalid_reason\"))
    print(\"raw_balance_w:\", data.get(\"attributes\", {}).get(\"raw_balance_w\"))
    print(\"last_valid_at:\", data.get(\"attributes\", {}).get(\"last_valid_at\"))
"'
```
Expected: `load_quality` is `valid` or `valid_noise_clamp`, `invalid_reason` is `None`, and `last_valid_at` is populated.

- [ ] **Step 3: Trigger manual MPC run on energy host**

Run:
```bash
ssh halil@192.168.0.135 "docker exec emhass-cron python3 /app/fetch_prices_emhass.py --action mpc -v"
```
Expected: Exit code 0, MPC optimization succeeds, `publish-data` succeeds.

- [ ] **Step 4: Check `last_enever_run.json` status**

Run:
```bash
ssh halil@192.168.0.135 "cat /home/halil/workspace/energy-stack/emhass/share/last_enever_run.json"
```
Expected: `"mpc": { "ok": true }`.
