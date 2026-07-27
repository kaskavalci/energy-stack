# Sigenergy Native Fallback Restoration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every EMHASS fail-safe transition return Sigenergy to confirmed native self-consumption with device-valid power limits, and automatically repair that state after a Home Assistant start or Sigenergy reconnect.

**Architecture:** Home Assistant remains the control-plane owner. Normal MPC control continues to select command charge, discharge, or standby; only master-off and control-failure paths call `script.sigen_restore_native_control`. Low-level power-limit scripts clamp requests to the Sigenergy MQTT number entity's advertised `max`, while native restoration becomes a restartable, two-phase transaction: confirm native mode and entity availability, write clamped limits, confirm the reported limits, and only then record success.

**Tech Stack:** Home Assistant package YAML and Jinja templates, `sigenergy2mqtt` MQTT-discovered `select`/`number` entities, Python `unittest` structural regression tests, repository render/deploy scripts, Docker Compose.

## Global Constraints

- Native fallback is `Maximum Self-consumption (Default)` and is selected only when the EMHASS master is off or a control health gate fails.
- Healthy MPC trading must continue to use the existing command charge, command discharge, and standby modes.
- Never hardcode the observed 8.8 kW charging or 9.6 kW discharging ratings in tracked HA YAML; derive them from each MQTT number entity's `max` attribute.
- Preserve the operator caps in `input_number.emhass_max_charge_cap_w` and `input_number.emhass_max_discharge_cap_w`; the commanded target is `min(operator cap, device max)`.
- A missing, unavailable, non-numeric, or non-positive device maximum fails closed and must not produce a successful restoration timestamp.
- During native restoration, `input_datetime.emhass_last_limit_write` advances only after the inverter reports native mode and both expected limit values within 0.02 kW. The existing active-MPC scripts retain their current timestamp semantics.
- Keep `input_boolean.emhass_auto_control_master` off throughout fallback deployment and reconnect testing.
- Preserve the MPC health changes already present in the dirty worktree, especially the recovery-charge checks in `home-assistant/ha_sigen_ems.yaml`; do not overwrite unrelated agent edits.
- Before implementation, preserve or commit the existing overlapping worktree changes. If execution uses an isolated worktree, create it with `superpowers:using-git-worktrees` from a commit that contains those changes.
- Do not change `optimization_time_step` (30 minutes), `prediction_horizon` (32 maximum), price methods (`csv`), weather method (`open-meteo`), or `open_meteo_cache_max_age` (120 minutes).
- Do not commit `config/site.env`, `emhass/secrets_emhass.yaml`, `emhass/share/config.json`, rendered HA packages, live recorder data, or recovery snapshots.

## Scope Boundaries

- This plan fixes native fallback, actuator clamping, HA-start/reconnect reconciliation, and their tests/documentation.
- The misleading `binary_sensor.emhass_plan_valid` problem-device-class semantics are independent and are not changed here.
- Site-specific EMHASS optimizer power limits are not changed here; actuator writes are made safe at the Home Assistant boundary.

## File Structure

- Modify `home-assistant/ha_sigen_ems.yaml`: clamp all charge/discharge actuator writes, make native restoration transactional, and reconcile native state after HA start or reconnect.
- Create `scripts/tests/test_sigen_ems_package.py`: focused structural regression tests for ordering, clamping, confirmation, and HA-start/reconnect behavior in the HA package source.
- Modify `docs/SETUP.md`: document native fallback semantics and operator verification.
- Modify `memory-bank/activeContext.md`: record deployment status and live verification after implementation.
- Modify `memory-bank/techContext.md`: record the native-fallback and device-limit invariants.

---

### Task 1: Clamp Every Active Power-Limit Write to the Device-Advertised Maximum

**Files:**
- Create: `scripts/tests/test_sigen_ems_package.py`
- Modify: `home-assistant/ha_sigen_ems.yaml:104-165`
- Test: `scripts/tests/test_sigen_ems_package.py`

**Interfaces:**
- Consumes: `watts` script field, `number.__SIGEN_PLANT_SLUG___plant_max_charging_limit` attribute `max`, and `number.__SIGEN_PLANT_SLUG___plant_max_discharging_limit` attribute `max`.
- Produces: Jinja variables `requested_kw`, `device_max_kw`, and `limit_kw` in each low-level limit script; later tasks rely on these scripts never publishing an out-of-range active command.

- [ ] **Step 1: Create the failing structural regression tests**

Create `scripts/tests/test_sigen_ems_package.py` with this complete content:

```python
"""Structural regression tests for the Sigenergy EMS Home Assistant package."""

from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_PATH = REPO_ROOT / "home-assistant" / "ha_sigen_ems.yaml"


def package_text() -> str:
    return PACKAGE_PATH.read_text(encoding="utf-8")


def script_block(name: str, next_name: str) -> str:
    text = package_text()
    return text.split(f"  {name}:\n", 1)[1].split(f"  {next_name}:\n", 1)[0]


def automation_block(automation_id: str, next_automation_id: str) -> str:
    text = package_text()
    return text.split(f"  - id: {automation_id}\n", 1)[1].split(
        f"  - id: {next_automation_id}\n", 1
    )[0]


class SigenActiveLimitClampTests(unittest.TestCase):
    def test_charge_limit_clamps_to_advertised_device_max(self) -> None:
        charge = script_block(
            "sigen_apply_charge_limits", "sigen_apply_discharge_limits"
        )
        self.assertIn(
            "state_attr('number.__SIGEN_PLANT_SLUG___plant_max_charging_limit', 'max')",
            charge,
        )
        self.assertIn(
            "[requested_kw | float(0), device_max_kw | float(0)] | min",
            charge,
        )

    def test_discharge_limit_clamps_to_advertised_device_max(self) -> None:
        discharge = script_block(
            "sigen_apply_discharge_limits", "sigen_apply_idle_limits"
        )
        self.assertIn(
            "state_attr('number.__SIGEN_PLANT_SLUG___plant_max_discharging_limit', 'max')",
            discharge,
        )
        self.assertIn(
            "[requested_kw | float(0), device_max_kw | float(0)] | min",
            discharge,
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the new tests and verify the current implementation fails**

Run:

```bash
python3 -m unittest discover -s scripts/tests -p 'test_sigen_ems_package.py' -v
```

Expected: two failures because neither active limit script reads the MQTT number entity's `max` attribute.

- [ ] **Step 3: Clamp the charge script's requested watts to the device maximum**

In `home-assistant/ha_sigen_ems.yaml`, replace the `sigen_apply_charge_limits` variable block with:

```yaml
      - variables:
          requested_kw: "{{ [0, (watts | float(0) / 1000)] | max | round(2) }}"
          device_max_kw: >-
            {{ state_attr('number.__SIGEN_PLANT_SLUG___plant_max_charging_limit', 'max')
               | float(0) }}
          limit_kw: >-
            {{ [requested_kw | float(0), device_max_kw | float(0)] | min | round(2) }}
```

Leave the existing `number.set_value` actions and `value: "{{ limit_kw }}"` unchanged. A missing maximum becomes `0`, which fails closed instead of sending an oversized value.

- [ ] **Step 4: Clamp the discharge script's requested watts to the device maximum**

In the same file, replace the `sigen_apply_discharge_limits` variable block with:

```yaml
      - variables:
          requested_kw: "{{ [0, (watts | float(0) / 1000)] | max | round(2) }}"
          device_max_kw: >-
            {{ state_attr('number.__SIGEN_PLANT_SLUG___plant_max_discharging_limit', 'max')
               | float(0) }}
          limit_kw: >-
            {{ [requested_kw | float(0), device_max_kw | float(0)] | min | round(2) }}
```

Leave the existing discharge `number.set_value` action and inactive charge-limit reset unchanged.

- [ ] **Step 5: Run the focused tests and the existing HA merge tests**

Run:

```bash
python3 -m unittest discover -s scripts/tests -p 'test_sigen_ems_package.py' -v
python3 -m unittest discover -s scripts/tests -p 'test_ha_config_merge.py' -v
```

Expected: `2 tests` pass in the Sigenergy package suite and `5 tests` pass in the HA merge suite.

- [ ] **Step 6: Commit the independently safe actuator clamp**

```bash
git add home-assistant/ha_sigen_ems.yaml scripts/tests/test_sigen_ems_package.py
git commit -m "fix(sigen): clamp EMS limits to device ratings"
```

---

### Task 2: Make Native Restoration a Verified Transaction

**Files:**
- Modify: `scripts/tests/test_sigen_ems_package.py`
- Modify: `home-assistant/ha_sigen_ems.yaml:187-223`
- Test: `scripts/tests/test_sigen_ems_package.py`

**Interfaces:**
- Consumes: native option from `input_select.emhass_ems_mode_native`, operator caps in watts, MQTT number entity `max` attributes in kW, `script.emhass_push_alert`, and the three live Sigenergy actuator entities.
- Produces: restartable `script.sigen_restore_native_control` and `script.sigen_disable_remote_ems` wrapper with two confirmed postconditions; `input_datetime.emhass_last_limit_write` becomes a success marker rather than an attempt marker for fallback.

- [ ] **Step 1: Add failing tests for native-first ordering, clamped targets, verification, and timestamp semantics**

Add this class above the `if __name__ == "__main__":` block in `scripts/tests/test_sigen_ems_package.py`:

```python
class SigenNativeRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.restore = script_block(
            "sigen_restore_native_control", "sigen_disable_remote_ems"
        )

    def test_restore_is_restartable_and_confirms_native_before_limit_writes(self) -> None:
        self.assertIn("mode: restart", self.restore)
        select_position = self.restore.index("service: select.select_option")
        first_wait_position = self.restore.index("- wait_template:")
        target_calculation_position = self.restore.index("charge_device_max_kw:")
        charge_write_position = self.restore.index(
            "entity_id: number.__SIGEN_PLANT_SLUG___plant_max_charging_limit"
        )
        self.assertLess(select_position, first_wait_position)
        self.assertLess(first_wait_position, target_calculation_position)
        self.assertLess(target_calculation_position, charge_write_position)
        self.assertIn('timeout: "00:01:15"', self.restore)

    def test_restore_clamps_both_operator_caps_to_device_maxima(self) -> None:
        self.assertIn("charge_device_max_kw", self.restore)
        self.assertIn("discharge_device_max_kw", self.restore)
        self.assertIn(
            "[charge_cap_kw | float(0), charge_device_max_kw | float(0)]",
            self.restore,
        )
        self.assertIn(
            "[discharge_cap_kw | float(0), discharge_device_max_kw | float(0)]",
            self.restore,
        )
        self.assertGreaterEqual(self.restore.count("| min | round(2)"), 2)

    def test_restore_records_timestamp_only_after_limit_confirmation(self) -> None:
        second_wait_position = self.restore.rindex("- wait_template:")
        timestamp_position = self.restore.index(
            "entity_id: input_datetime.emhass_last_limit_write"
        )
        self.assertLess(second_wait_position, timestamp_position)
        self.assertIn('timeout: "00:00:30"', self.restore)
        self.assertIn("Native fallback mode confirmation failed", self.restore)
        self.assertIn("Native fallback limit confirmation failed", self.restore)

    def test_disable_wrapper_is_restartable(self) -> None:
        disable = script_block(
            "sigen_disable_remote_ems", "sigen_apply_emhass_plan"
        )
        self.assertIn("mode: restart", disable)
        self.assertIn("service: script.sigen_restore_native_control", disable)
```

- [ ] **Step 2: Run the focused suite and verify the new tests fail**

Run:

```bash
python3 -m unittest discover -s scripts/tests -p 'test_sigen_ems_package.py' -v
```

Expected: the two Task 1 tests pass and the four fallback tests fail because both current scripts are `mode: single`, restoration performs no waits, does not clamp to entity maxima, and writes the timestamp unconditionally.

- [ ] **Step 3: Replace `sigen_restore_native_control` with the verified implementation**

Replace the complete `sigen_restore_native_control` block in `home-assistant/ha_sigen_ems.yaml` with:

```yaml
  sigen_restore_native_control:
    alias: Sigen restore native control
    description: >-
      Return control to native Maximum Self-consumption, restore device-valid
      operator limits, and record success only after Sigenergy confirms both phases.
    mode: restart
    sequence:
      - variables:
          native_mode: "{{ states('input_select.emhass_ems_mode_native') }}"
          charge_cap_kw: >-
            {{ [0, (states('input_number.emhass_max_charge_cap_w')
                      | float(0) / 1000)] | max | round(2) }}
          discharge_cap_kw: >-
            {{ [0, (states('input_number.emhass_max_discharge_cap_w')
                      | float(0) / 1000)] | max | round(2) }}
      - service: select.select_option
        continue_on_error: true
        target:
          entity_id: select.__SIGEN_PLANT_SLUG___plant_remote_ems_control_mode
        data:
          option: "{{ native_mode }}"
      - wait_template: >-
          {% set bad = ['unknown', 'unavailable', 'none', ''] %}
          {{ is_state('select.__SIGEN_PLANT_SLUG___plant_remote_ems_control_mode', native_mode)
             and states('number.__SIGEN_PLANT_SLUG___plant_max_charging_limit') not in bad
             and states('number.__SIGEN_PLANT_SLUG___plant_max_discharging_limit') not in bad
             and (state_attr('number.__SIGEN_PLANT_SLUG___plant_max_charging_limit', 'max')
                  | float(0)) > 0
             and (state_attr('number.__SIGEN_PLANT_SLUG___plant_max_discharging_limit', 'max')
                  | float(0)) > 0 }}
        timeout: "00:01:15"
        continue_on_timeout: true
      - if:
          - condition: template
            value_template: "{{ not wait.completed }}"
        then:
          - service: script.emhass_push_alert
            data:
              title: "EMHASS D2: Native fallback mode confirmation failed"
              message: >-
                Master is off, but Sigenergy did not confirm native mode and available
                limit entities within 75 seconds. Keep EMHASS control disabled.
          - stop: "Native fallback mode confirmation failed"
            error: true
      - variables:
          charge_device_max_kw: >-
            {{ state_attr('number.__SIGEN_PLANT_SLUG___plant_max_charging_limit', 'max')
               | float(0) }}
          discharge_device_max_kw: >-
            {{ state_attr('number.__SIGEN_PLANT_SLUG___plant_max_discharging_limit', 'max')
               | float(0) }}
          charge_target_kw: >-
            {{ [charge_cap_kw | float(0), charge_device_max_kw | float(0)]
               | min | round(2) }}
          discharge_target_kw: >-
            {{ [discharge_cap_kw | float(0), discharge_device_max_kw | float(0)]
               | min | round(2) }}
      - service: number.set_value
        continue_on_error: true
        target:
          entity_id: number.__SIGEN_PLANT_SLUG___plant_max_charging_limit
        data:
          value: "{{ charge_target_kw }}"
      - service: number.set_value
        continue_on_error: true
        target:
          entity_id: number.__SIGEN_PLANT_SLUG___plant_max_discharging_limit
        data:
          value: "{{ discharge_target_kw }}"
      - wait_template: >-
          {% set charge_actual_kw =
               states('number.__SIGEN_PLANT_SLUG___plant_max_charging_limit') | float(none) %}
          {% set discharge_actual_kw =
               states('number.__SIGEN_PLANT_SLUG___plant_max_discharging_limit') | float(none) %}
          {{ charge_actual_kw is not none
             and discharge_actual_kw is not none
             and ((charge_actual_kw - (charge_target_kw | float(0))) | abs) <= 0.02
             and ((discharge_actual_kw - (discharge_target_kw | float(0))) | abs) <= 0.02 }}
        timeout: "00:00:30"
        continue_on_timeout: true
      - if:
          - condition: template
            value_template: "{{ not wait.completed }}"
        then:
          - service: script.emhass_push_alert
            data:
              title: "EMHASS D2: Native fallback limit confirmation failed"
              message: >-
                Sigenergy is in native mode, but limits did not converge to
                charge={{ charge_target_kw }} kW and discharge={{ discharge_target_kw }} kW
                within 30 seconds. Keep EMHASS control disabled.
          - stop: "Native fallback limit confirmation failed"
            error: true
      - service: input_datetime.set_datetime
        target:
          entity_id: input_datetime.emhass_last_limit_write
        data:
          datetime: "{{ now().strftime('%Y-%m-%d %H:%M:%S') }}"
```

Immediately after that block, replace `sigen_disable_remote_ems` with:

```yaml
  sigen_disable_remote_ems:
    alias: Sigen disable Remote EMS control
    description: Restore limits and set self-consumption mode when master is turned off.
    mode: restart
    sequence:
      - service: script.sigen_restore_native_control
```

The 75-second mode timeout exceeds the live select entity's 60-second scan interval. Both scripts use `mode: restart` so a reconnect-triggered invocation can cancel an earlier unavailable-state wait and reissue the native selection after MQTT returns.

- [ ] **Step 4: Run the focused tests**

Run:

```bash
python3 -m unittest discover -s scripts/tests -p 'test_sigen_ems_package.py' -v
```

Expected: all `6 tests` pass.

- [ ] **Step 5: Render the package and verify all site tokens resolve**

Run:

```bash
./scripts/render-ha-packages.sh
rg -n '__SIGEN_PLANT_SLUG__' .rendered/home-assistant/ha_sigen_ems.yaml
```

Expected: render succeeds; the `rg` command prints nothing and exits with status 1 because no unresolved Sigenergy token remains.

- [ ] **Step 6: Commit the transactional restore**

```bash
git add home-assistant/ha_sigen_ems.yaml scripts/tests/test_sigen_ems_package.py
git commit -m "fix(sigen): verify native fallback restoration"
```

---

### Task 3: Reconcile Native Fallback After Sigenergy Reconnects or HA Starts

**Files:**
- Modify: `scripts/tests/test_sigen_ems_package.py`
- Modify: `home-assistant/ha_sigen_ems.yaml:289-316`
- Test: `scripts/tests/test_sigen_ems_package.py`

**Interfaces:**
- Consumes: `trigger.id`, the Sigenergy mode state transition, Home Assistant's start event, master-switch state, and restartable `script.sigen_disable_remote_ems`/`script.sigen_restore_native_control` from Task 2.
- Produces: `emhass_sigen_enforce_master_off` behavior that restores after an HA start or any mode-entity reconnect while the master is off, even when the inverter reports native mode with stale limits.

- [ ] **Step 1: Add failing HA-start and reconnect tests**

Add this class above the `if __name__ == "__main__":` block in `scripts/tests/test_sigen_ems_package.py`:

```python
class SigenReconnectReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.automation = automation_block(
            "emhass_sigen_enforce_master_off",
            "emhass_sigen_apply_plan_scheduled",
        )

    def test_reconnect_and_ha_start_have_distinct_trigger_ids(self) -> None:
        self.assertIn("- id: command_mode", self.automation)
        self.assertIn("- id: reconnect", self.automation)
        self.assertIn("- id: ha_start", self.automation)
        self.assertIn("platform: homeassistant", self.automation)
        self.assertIn('"unknown"', self.automation)
        self.assertIn('"unavailable"', self.automation)

    def test_native_reconnect_and_ha_start_are_not_filtered_out(self) -> None:
        self.assertIn("trigger.id in ['reconnect', 'ha_start']", self.automation)
        self.assertIn("service: script.sigen_disable_remote_ems", self.automation)
```

- [ ] **Step 2: Run the focused suite and verify the reconciliation tests fail**

Run:

```bash
python3 -m unittest discover -s scripts/tests -p 'test_sigen_ems_package.py' -v
```

Expected: the six earlier tests pass and the two reconciliation tests fail because the current automation has no trigger IDs or HA-start trigger and filters every reconnect through the command-mode-only condition.

- [ ] **Step 3: Give command-mode, reconnect, and HA-start transitions distinct trigger IDs**

Replace the complete `emhass_sigen_enforce_master_off` automation with:

```yaml
  - id: emhass_sigen_enforce_master_off
    alias: EMHASS Sigen — enforce master off after start or reconnect
    mode: restart
    trigger:
      - id: command_mode
        platform: state
        entity_id: select.__SIGEN_PLANT_SLUG___plant_remote_ems_control_mode
        to:
          - "Command Discharging (Output power from PV first)"
          - "Command Discharging (Output power from the battery first)"
          - "Command Charging (Consume power from the grid first)"
      - id: reconnect
        platform: state
        entity_id: select.__SIGEN_PLANT_SLUG___plant_remote_ems_control_mode
        from:
          - "unknown"
          - "unavailable"
      - id: ha_start
        platform: homeassistant
        event: start
    condition:
      - condition: state
        entity_id: input_boolean.emhass_auto_control_master
        state: "off"
      - condition: template
        value_template: >-
          {{ trigger.id in ['reconnect', 'ha_start']
             or states('select.__SIGEN_PLANT_SLUG___plant_remote_ems_control_mode') in [
                  'Command Discharging (Output power from PV first)',
                  'Command Discharging (Output power from the battery first)',
                  'Command Charging (Consume power from the grid first)'
                ] }}
    action:
      - service: script.sigen_disable_remote_ems
```

The reconnect and HA-start trigger IDs intentionally bypass the command-mode predicate. That repairs the observed hole where Sigenergy returned directly to native mode while retaining charge=0 kW and discharge=9 kW, and it makes the same invariant explicit after Home Assistant Core restarts.

- [ ] **Step 4: Run the focused and full repository unit suites**

Run:

```bash
python3 -m unittest discover -s scripts/tests -p 'test_sigen_ems_package.py' -v
python3 -m unittest discover -s scripts/tests -v
python3 -m unittest discover -s emhass-cron/tests -v
```

Expected: `8 tests` pass in the Sigenergy suite, all scripts tests pass, and all emhass-cron tests pass with zero failures or errors.

- [ ] **Step 5: Commit HA-start and reconnect reconciliation**

```bash
git add home-assistant/ha_sigen_ems.yaml scripts/tests/test_sigen_ems_package.py
git commit -m "fix(sigen): reconcile native fallback after restarts"
```

---

### Task 4: Document the Fallback Contract and Verification Signals

**Files:**
- Modify: `docs/SETUP.md`
- Modify: `memory-bank/techContext.md`
- Test: `scripts/tests/test_sigen_ems_package.py`

**Interfaces:**
- Consumes: final behavior from Tasks 1-3.
- Produces: operator-facing fallback contract and durable agent invariants for future changes.

- [ ] **Step 1: Add the operator contract to `docs/SETUP.md`**

Insert this section immediately before `## Security notes`:

```markdown
## Sigenergy native fallback

`input_boolean.emhass_auto_control_master` controls whether EMHASS may use
Sigenergy command modes. Turning it off does not require an operator to select an
inverter mode manually: `script.sigen_restore_native_control` selects
`Maximum Self-consumption (Default)` automatically.

Restoration is successful only when Home Assistant confirms all of the following:

1. The Remote EMS control mode is `Maximum Self-consumption (Default)`.
2. The charging and discharging limit entities are available.
3. Each restored limit equals the smaller of its operator cap and the device's
   MQTT-advertised `max` attribute, within 0.02 kW.
4. `input_datetime.emhass_last_limit_write` advances after those confirmations.

If mode or limit confirmation times out, keep the master off and investigate the
`EMHASS D2` notification. Home Assistant start or a Sigenergy mode-entity reconnect
while the master is off reruns the same restoration transaction, including when
the inverter reports native mode immediately.
```

- [ ] **Step 2: Add the implementation invariant to `memory-bank/techContext.md`**

Add these bullets under `## EMHASS invariants`:

```markdown
- Master-off or control-failure fallback selects Sigenergy native
  `Maximum Self-consumption (Default)` automatically; operators do not select it
  during healthy MPC trading.
- Every Sigenergy charge/discharge limit is clamped to the MQTT number entity's
  advertised `max`; native restoration is successful only after mode and both
  limits are confirmed, then `emhass_last_limit_write` advances.
- Home Assistant start or a Sigenergy mode reconnect while the master is off
  reruns native restoration even when the returned mode is already native.
```

- [ ] **Step 3: Run the complete local verification set**

Run:

```bash
python3 -m unittest discover -s scripts/tests -v
python3 -m unittest discover -s emhass-cron/tests -v
./scripts/render-ha-packages.sh
rg -n '__[A-Z0-9_]+__' .rendered/home-assistant/ha_sigen_ems.yaml
```

Expected: both unit suites pass; rendering succeeds; the final `rg` prints nothing and exits with status 1 because the rendered package contains no unresolved token.

- [ ] **Step 4: Review the exact diff for scope containment**

Run:

```bash
git diff --check
git diff -- home-assistant/ha_sigen_ems.yaml scripts/tests/test_sigen_ems_package.py docs/SETUP.md memory-bank/techContext.md
```

Expected: `git diff --check` prints nothing. The diff contains only actuator clamping, verified native restoration, HA-start/reconnect reconciliation, tests, and the documented contract; the existing MPC-status recovery gate remains intact.

- [ ] **Step 5: Commit documentation and invariants**

```bash
git add docs/SETUP.md memory-bank/techContext.md
git commit -m "docs(sigen): define native fallback contract"
```

---

### Task 5: Deploy in a Maintenance Window and Prove Every Restoration Path

**Files:**
- Modify after successful live verification: `memory-bank/activeContext.md`
- Deploy source: `home-assistant/ha_sigen_ems.yaml`
- Verify live: rendered HA package, Sigenergy mode/limit entities, HA automation traces, `input_datetime.emhass_last_limit_write`

**Interfaces:**
- Consumes: all committed behavior from Tasks 1-4, live connection values from `config/site.env`, an explicit pre-deploy package backup, and the existing HA deploy/check/restart workflow.
- Produces: deployed and live-proven native fallback for HA start, direct master-off, and Sigenergy reconnect; updated active context records the evidence.

- [ ] **Step 1: Re-run all local tests immediately before deployment**

Run:

```bash
python3 -m unittest discover -s scripts/tests -v
python3 -m unittest discover -s emhass-cron/tests -v
./scripts/render-ha-packages.sh
git diff --check
```

Expected: all tests pass, rendering succeeds, and `git diff --check` prints nothing.

- [ ] **Step 2: Put the plant into the maintenance precondition**

In Home Assistant, turn off `input_boolean.emhass_auto_control_master`. Confirm that `binary_sensor.emhass_control_allowed` is `off` with `block_reason: master_off`. Do not manually select native mode; the currently deployed fallback automation owns that transition.

- [ ] **Step 3: Back up the live package that the deploy script will replace**

The current `scripts/deploy-home-assistant.sh` backup contains only
`configuration.yaml`, so create an explicit rollback copy of both files before
uploading the package:

```bash
source scripts/lib/source-site-env.sh
HA="/mnt/data/supervisor/homeassistant"
HA_SSH="${HA_SSH_USER}@${HA_SSH_HOST}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="${HA}/backups/native-fallback-${STAMP}"
ssh -p "${HA_SSH_PORT}" "${HA_SSH}" "mkdir -p '${BACKUP_DIR}'"
ssh -p "${HA_SSH_PORT}" "${HA_SSH}" "cp '${HA}/configuration.yaml' '${HA}/ha_sigen_ems.yaml' '${BACKUP_DIR}/'"
ssh -p "${HA_SSH_PORT}" "${HA_SSH}" "ls -l '${BACKUP_DIR}/configuration.yaml' '${BACKUP_DIR}/ha_sigen_ems.yaml'"
```

Expected: both files are listed with non-zero sizes under the newly timestamped
`native-fallback-` backup directory. Stop before deployment if either copy is
absent.

- [ ] **Step 4: Deploy the HA package through the repository script**

Run:

```bash
./scripts/deploy-home-assistant.sh
```

Expected: the script renders packages, creates its normal `configuration.yaml`
backup, uploads the additive package set, reports a successful `ha core check`,
and restarts Home Assistant Core. The explicit Step 3 backup provides the package
rollback copy. The master remains off after restart.

- [ ] **Step 5: Verify automatic restoration after the HA restart**

Run this read-only state snapshot from the laptop:

```bash
source config/site.env
ssh -o ConnectTimeout=10 "$ENERGY_REMOTE_HOST" 'docker exec emhass-cron python3 -c '\''import json, os; from pathlib import Path; from fetch_prices_emhass import fetch_ha_state; p=Path("/secrets/secrets_emhass.yaml"); slug=os.environ["SIGEN_PLANT_SLUG"]; es=["input_boolean.emhass_auto_control_master","sensor.emhass_mpc_status","binary_sensor.emhass_control_allowed","sensor.p_batt_forecast","input_datetime.emhass_last_limit_write",f"select.{slug}_plant_remote_ems_control_mode",f"number.{slug}_plant_max_charging_limit",f"number.{slug}_plant_max_discharging_limit"]; print(json.dumps({e:fetch_ha_state(p,e) for e in es},indent=2))'\'''
```

Expected on the current site: master `off`, mode `Maximum Self-consumption (Default)`, charge limit `8.8` kW, discharge limit `9.0` kW, and `emhass_last_limit_write` newer than the deployment start.

- [ ] **Step 6: Prove the direct master-off path**

In Home Assistant, briefly turn the master on only after `sensor.emhass_mpc_status` is `ok`, its `plan_generated_at` is younger than 40 minutes, and `binary_sensor.emhass_control_allowed` becomes `on`. Then turn the master off again.

Expected within 120 seconds: mode returns to `Maximum Self-consumption (Default)`, charge/discharge limits return to the clamped operator targets, and `emhass_last_limit_write` advances only after both values are reported.

- [ ] **Step 7: Prove the Sigenergy reconnect path while the master is off**

Run:

```bash
source config/site.env
ssh -o ConnectTimeout=10 "$ENERGY_REMOTE_HOST" 'docker restart sigenergy2mqtt'
```

Wait until the mode entity has transitioned through `unknown` or `unavailable`, then rerun the read-only snapshot from Step 5.

Expected within 120 seconds of entity recovery: master remains `off`, mode is native, charge is `8.8` kW, discharge is `9.0` kW, and `emhass_last_limit_write` is newer than the container restart. Home Assistant has no `EMHASS D2: Native fallback` failure notification.

- [ ] **Step 8: Resume MPC trading through the master switch**

Turn on `input_boolean.emhass_auto_control_master`. At the next `:00` or `:30`
boundary, rerun the Step 5 snapshot and verify:

```text
sensor.emhass_mpc_status = ok
binary_sensor.emhass_control_allowed = on
binary_sensor.emhass_control_allowed.block_reason = ok
input_datetime.emhass_last_limit_write = current MPC boundary
Sigenergy Remote EMS mode = command charge, command discharge, or standby matching sensor.p_batt_forecast
```

Expected: normal EMHASS trading resumes; native mode is no longer selected while the healthy plan is being applied.

- [ ] **Step 9: Record deployment evidence in `memory-bank/activeContext.md`**

After every expectation in Steps 5-8 is observed, add this exact dated section:

```markdown
## Sigenergy native fallback verified (2026-07-16)

- HA package deploy: successful `ha core check` and Core restart.
- HA-start reconciliation with master off: native mode confirmed; charge limit
  `8.8` kW and discharge limit `9.0` kW confirmed before
  `emhass_last_limit_write` advanced.
- Direct master-off: native mode and the same `8.8`/`9.0` kW limits confirmed
  before `emhass_last_limit_write` advanced.
- `sigenergy2mqtt` reconnect with master off: native restoration reran and
  confirmed the same `8.8`/`9.0` kW targets.
- Trading resume: MPC status `ok`, control gate `ok`, and the next scheduled
  plan applied successfully.
```

- [ ] **Step 10: Run final verification and commit the deployment record**

Run:

```bash
python3 -m unittest discover -s scripts/tests -v
python3 -m unittest discover -s emhass-cron/tests -v
git diff --check
git status --short
git add memory-bank/activeContext.md
git commit -m "docs(sigen): record fallback deployment verification"
```

Expected: all tests pass, `git diff --check` prints nothing, and `git status --short` shows no uncommitted files owned by this plan. Pre-existing unrelated user/agent changes may remain and must not be staged.

## Rollback Conditions

Keep the master off and stop live testing if any of these occur:

- Native mode is not confirmed within 75 seconds after a fallback request.
- Either number entity remains `unknown`/`unavailable` or differs from its target by more than 0.02 kW after 30 seconds.
- `emhass_last_limit_write` advances despite an unconfirmed mode or limit.
- Reconnect leaves the inverter in a command mode while the master is off.
- Home Assistant reports a script configuration error or `ha core check` failure.

Restore the explicit Step 3 backup with these commands:

```bash
source scripts/lib/source-site-env.sh
HA="/mnt/data/supervisor/homeassistant"
HA_SSH="${HA_SSH_USER}@${HA_SSH_HOST}"
BACKUP_DIR="$(ssh -p "${HA_SSH_PORT}" "${HA_SSH}" "ls -dt '${HA}'/backups/native-fallback-* | head -n 1")"
ssh -p "${HA_SSH_PORT}" "${HA_SSH}" "cp '${BACKUP_DIR}/configuration.yaml' '${HA}/configuration.yaml'"
ssh -p "${HA_SSH_PORT}" "${HA_SSH}" "cp '${BACKUP_DIR}/ha_sigen_ems.yaml' '${HA}/ha_sigen_ems.yaml'"
ssh -p "${HA_SSH_PORT}" "${HA_SSH}" "ha core check"
ssh -p "${HA_SSH_PORT}" "${HA_SSH}" "ha core restart"
```

Expected: `ha core check` succeeds before the restart. Do not re-enable the
master until native self-consumption and both restored limits are visible again.
