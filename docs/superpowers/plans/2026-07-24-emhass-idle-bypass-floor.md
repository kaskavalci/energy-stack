# EMHASS Idle Bypass Floor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every true EMHASS idle/deadband command select Sigenergy native maximum self-consumption with restored limits, including when SOC is at or below the configured floor.

**Architecture:** Keep the existing explicit deadband branch and native-restoration transaction unchanged. Narrow the SOC-floor cutout to the two command-discharge modes, then reconcile after the hard stop: if the current plan has become true idle and recovery is off, immediately restore native control. Command-discharge safety, charge, and recovery remain unchanged.

**Tech Stack:** Home Assistant package YAML, Jinja templates, Python `unittest` structural regression tests.

## Global Constraints

- True idle is `-threshold <= p_batt_forecast <= threshold`.
- True idle always calls `script.sigen_restore_native_control`, regardless of SOC.
- Native restoration keeps its confirmed operator/device-valid charge and discharge limits.
- The SOC floor still forces Standby and zero limits for explicit command-discharge.
- Charge, recovery, guarded fallthrough, and master-off behavior remain unchanged.
- Deploy only to the configured Odroid Home Assistant target from `config/site.env`.
- Preserve all unrelated working-tree changes.
- Do not commit unless explicitly requested.

---

### Task 1: Exclude native idle from the SOC-floor cutout

**Files:**
- Modify: `scripts/tests/test_sigen_ems_package.py`
- Modify: `home-assistant/ha_sigen_ems.yaml:598-639`

**Interfaces:**
- Consumes: `select.__SIGEN_PLANT_SLUG___plant_remote_ems_control_mode` and the existing `script.sigen_restore_native_control`.
- Produces: a floor cutout that matches command-discharge modes only.

- [ ] **Step 1: Replace the native-floor regression with a failing exclusion regression**

Replace `test_floor_cutout_covers_native_mode` with:

```python
    def test_floor_cutout_excludes_native_mode(self) -> None:
        floor = self.floor_cutout()
        trigger = floor.split("    trigger:\n", 1)[1].split(
            "    condition:\n", 1
        )[0]
        condition = floor.split("    condition:\n", 1)[1].split(
            "    action:\n", 1
        )[0]
        native_mode = "Maximum Self-consumption (Default)"
        discharge_modes = [
            "Command Discharging (Output power from PV first)",
            "Command Discharging (Output power from the battery first)",
        ]
        self.assertNotIn(native_mode, trigger)
        self.assertNotIn(native_mode, condition)
        for discharge_mode in discharge_modes:
            self.assertIn(discharge_mode, trigger)
            self.assertIn(discharge_mode, condition)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
.venv/bin/python -m unittest \
  scripts.tests.test_sigen_ems_package.SigenIdleSelfConsumptionTests.test_floor_cutout_excludes_native_mode \
  -v
```

Expected: FAIL because native self-consumption is still present in the floor trigger and condition.

- [ ] **Step 3: Remove native self-consumption from the floor cutout**

In `emhass_soc_floor_cutout`, remove:

```yaml
          - "Maximum Self-consumption (Default)"
```

from the mode state trigger, and remove:

```yaml
                 'Maximum Self-consumption (Default)'
```

from the allowed-mode condition. Keep both command-discharge strings and the existing hard-stop action.

- [ ] **Step 4: Add a failing concurrent-idle reconciliation regression**

Add:

```python
    def test_floor_cutout_restores_concurrent_idle_after_hard_stop(self) -> None:
        action = self.floor_cutout().split("    action:\n", 1)[1]
        reconcile = action.split("      - if:\n", 1)[1]
        self.assertIn("p >= (0 - threshold)", action)
        self.assertIn("p <= threshold", action)
        self.assertIn("entity_id: input_boolean.emhass_recovery_active", reconcile)
        self.assertIn('state: "off"', reconcile)
        self.assertIn("service: script.sigen_restore_native_control", action)
        self.assertLess(
            action.index("service: script.sigen_apply_idle_limits"),
            action.index("service: script.sigen_restore_native_control"),
        )
        self.assertLess(
            action.index("service: logbook.log"),
            action.index("service: script.sigen_restore_native_control"),
        )
```

Run:

```bash
.venv/bin/python -m unittest \
  scripts.tests.test_sigen_ems_package.SigenIdleSelfConsumptionTests.test_floor_cutout_restores_concurrent_idle_after_hard_stop \
  -v
```

Expected: FAIL because the cutout does not yet reconcile a concurrent idle plan.

- [ ] **Step 5: Reconcile current idle after the hard stop**

Append this action after the floor log entry:

```yaml
      - if:
          - condition: template
            value_template: >-
              {% set p = states('sensor.p_batt_forecast') | float(none) %}
              {% set threshold = states('input_number.emhass_power_threshold_w') | float(500) %}
              {{ p is not none
                 and p >= (0 - threshold)
                 and p <= threshold }}
          - condition: state
            entity_id: input_boolean.emhass_recovery_active
            state: "off"
        then:
          - service: script.sigen_restore_native_control
```

- [ ] **Step 6: Run focused and package tests and verify GREEN**

Run:

```bash
.venv/bin/python -m unittest \
  scripts.tests.test_sigen_ems_package.SigenIdleSelfConsumptionTests \
  -v
.venv/bin/python -m unittest scripts.tests.test_sigen_ems_package -v
```

Expected: all focused and package tests pass.

- [ ] **Step 7: Render and run the complete repository tests**

Run:

```bash
./scripts/render-ha-packages.sh
.venv/bin/python -m unittest discover -s scripts/tests -p 'test_*.py' -v
git diff --check
```

Expected: rendering succeeds, all discovered tests pass, and `git diff --check` reports no errors.

---

### Task 2: Deploy and verify native idle at the floor

**Files:**
- Verify only: `config/site.env`
- Deploy: current rendered Home Assistant package through `scripts/deploy-home-assistant.sh`
- Modify: `memory-bank/activeContext.md`

**Interfaces:**
- Consumes: the configured Odroid HA Container target and the normal `script.sigen_apply_emhass_plan` service.
- Produces: live native self-consumption during a natural idle/deadband step at any SOC.

- [ ] **Step 1: Capture the pre-deploy control state**

Read `config/site.env` privately and capture:

```text
input_boolean.emhass_auto_control_master
binary_sensor.emhass_control_allowed
sensor.p_batt_forecast
input_number.emhass_power_threshold_w
sensor.<SIGEN_PLANT_SLUG>_plant_battery_soc
select.<SIGEN_PLANT_SLUG>_plant_remote_ems_control_mode
number.<SIGEN_PLANT_SLUG>_plant_max_charging_limit
number.<SIGEN_PLANT_SLUG>_plant_max_discharging_limit
```

Expected: target and control state are known without changing helpers.

- [ ] **Step 2: Deploy through the repository script**

Run:

```bash
./scripts/deploy-home-assistant.sh
```

Expected: render, HA Container configuration check, and restart succeed without changing the master helper.

- [ ] **Step 3: Exercise only the normal current-plan path**

If the current published `p_batt_forecast` remains inside the configured deadband, call:

```yaml
service: script.sigen_apply_emhass_plan
```

If it is outside the deadband, do not manufacture idle; defer live idle verification.

- [ ] **Step 4: Verify live behavior**

For an active deadband command, require:

```text
mode = Maximum Self-consumption (Default)
charge limit = min(operator cap, advertised device maximum)
discharge limit = min(operator cap, advertised device maximum)
```

Also inspect the deployed floor automation and confirm native mode is absent from its trigger and condition while both command-discharge modes and the hard Standby/zero action remain.

- [ ] **Step 5: Record and verify the deployment**

Append a concise entry to `memory-bank/activeContext.md`, then run:

```bash
git diff --check
```

Expected: memory records config-check, master preservation, live idle result or deferral, and command-discharge-only floor behavior.
