# EMHASS Idle Self-Consumption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run Sigenergy native maximum self-consumption only for a true EMHASS idle/deadband command, while retaining Standby/zero limits for guarded fallthroughs and at the SOC floor.

**Architecture:** Extend `script.sigen_apply_emhass_plan` with an explicit third `choose` branch for `-threshold <= p_batt_forecast <= threshold`; leave the final fallback unchanged. Extend the existing SOC-floor cutout so native self-consumption is treated as a discharge-capable mode and is stopped at the configured floor. Recovery charging remains unchanged.

**Tech Stack:** Home Assistant package YAML, Jinja templates, Python `unittest` structural regression tests.

## Global Constraints

- `optimization_time_step` remains 30 minutes; this change touches only HA actuator policy.
- Charge and valid command-discharge behavior remain unchanged.
- Only a true deadband command selects native self-consumption.
- Guard-rejected and unexpected fallthroughs remain Standby with zero charge/discharge limits.
- SOC at or below `input_number.emhass_soc_min` has priority and selects Standby with zero limits, including from native mode.
- Existing recovery charging behavior remains unchanged.
- Preserve all unrelated working-tree changes.
- Do not create commits unless the user explicitly requests them.

---

### Task 1: Route only true deadband commands to native self-consumption

**Files:**
- Modify: `scripts/tests/test_sigen_ems_package.py`
- Modify: `home-assistant/ha_sigen_ems.yaml:412-438`

**Interfaces:**
- Consumes: `threshold` and numeric `p` variables already defined by `script.sigen_apply_emhass_plan`.
- Produces: an explicit true-deadband `choose` branch; the final default remains the hard Standby fallback.

- [ ] **Step 1: Add failing structural regression tests**

Add this test class before `EmhassRecoveryStateMachineTests`:

```python
class SigenIdleSelfConsumptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.apply = package_text().split(
            "  sigen_apply_emhass_plan:\n", 1
        )[1].split("\nautomation:\n", 1)[0]

    def test_true_deadband_uses_confirmed_native_restoration(self) -> None:
        marker = (
            'value_template: "{{ p >= (0 - threshold) '
            'and p <= threshold }}"'
        )
        self.assertIn(marker, self.apply)
        branch = self.apply.split(marker, 1)[1].split("        default:\n", 1)[0]
        self.assertIn("service: script.sigen_restore_native_control", branch)
        self.assertNotIn("service: script.sigen_apply_idle_limits", branch)

    def test_final_fallback_remains_standby_with_zero_limits(self) -> None:
        fallback = self.apply.split("        default:\n", 1)[1]
        self.assertIn("service: script.sigen_set_ems_mode", fallback)
        self.assertIn("mode: idle", fallback)
        self.assertIn("service: script.sigen_apply_idle_limits", fallback)
        self.assertNotIn("service: script.sigen_restore_native_control", fallback)
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
python3 -m unittest scripts.tests.test_sigen_ems_package.SigenIdleSelfConsumptionTests -v
```

Expected: the deadband test fails because the explicit marker/native branch does not exist yet; the fallback test already passes.

- [ ] **Step 3: Add the explicit true-deadband branch**

Insert this branch after the existing command-discharge branch and before `default:`:

```yaml
          - conditions:
              - condition: template
                value_template: "{{ p >= (0 - threshold) and p <= threshold }}"
            sequence:
              - service: script.sigen_restore_native_control
```

Do not change the existing final default:

```yaml
        default:
          - service: script.sigen_set_ems_mode
            data:
              mode: idle
          - service: script.sigen_apply_idle_limits
```

- [ ] **Step 4: Run the focused tests and verify they pass**

Run:

```bash
python3 -m unittest scripts.tests.test_sigen_ems_package.SigenIdleSelfConsumptionTests -v
```

Expected: 2 tests pass.

---

### Task 2: Enforce the SOC floor from native self-consumption

**Files:**
- Modify: `scripts/tests/test_sigen_ems_package.py`
- Modify: `home-assistant/ha_sigen_ems.yaml:593-630`

**Interfaces:**
- Consumes: `sensor.__SIGEN_PLANT_SLUG___plant_battery_soc`, `input_number.emhass_soc_min`, and the rendered Sigenergy mode select.
- Produces: floor cutout coverage for command-discharge and native self-consumption, including native mode selected while SOC is already at/below the floor.

- [ ] **Step 1: Add failing floor-policy tests**

Add these methods to `SigenIdleSelfConsumptionTests`:

```python
    def floor_cutout(self) -> str:
        return automation_block(
            "emhass_soc_floor_cutout", "emhass_recovery_start"
        )

    def test_floor_cutout_covers_native_mode(self) -> None:
        floor = self.floor_cutout()
        trigger = floor.split("    trigger:\n", 1)[1].split(
            "    condition:\n", 1
        )[0]
        conditions = floor.split("    condition:\n", 1)[1].split(
            "    action:\n", 1
        )[0]
        self.assertIn('"Maximum Self-consumption (Default)"', trigger)
        self.assertIn("'Maximum Self-consumption (Default)'", conditions)

    def test_floor_cutout_is_inclusive_and_keeps_hard_stop(self) -> None:
        floor = self.floor_cutout()
        self.assertGreaterEqual(floor.count("0 <= soc <= min_soc"), 2)
        self.assertIn("service: script.sigen_set_ems_mode", floor)
        self.assertIn("mode: idle", floor)
        self.assertIn("service: script.sigen_apply_idle_limits", floor)
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
python3 -m unittest scripts.tests.test_sigen_ems_package.SigenIdleSelfConsumptionTests -v
```

Expected: native-mode and inclusive-floor tests fail against the current command-discharge-only numeric cutout.

- [ ] **Step 3: Make the floor trigger inclusive and native-aware**

Replace the floor cutout trigger and conditions with:

```yaml
    trigger:
      - platform: template
        value_template: >-
          {% set soc = states('sensor.__SIGEN_PLANT_SLUG___plant_battery_soc') | float(none) %}
          {% set min_soc = states('input_number.emhass_soc_min') | float(5) %}
          {{ soc is not none and 0 <= soc <= min_soc }}
      - platform: state
        entity_id: select.__SIGEN_PLANT_SLUG___plant_remote_ems_control_mode
        to:
          - "Command Discharging (Output power from PV first)"
          - "Command Discharging (Output power from the battery first)"
          - "Maximum Self-consumption (Default)"
    condition:
      - condition: template
        value_template: >-
          {% set soc = states('sensor.__SIGEN_PLANT_SLUG___plant_battery_soc') | float(none) %}
          {% set min_soc = states('input_number.emhass_soc_min') | float(5) %}
          {{ soc is not none and 0 <= soc <= min_soc }}
      - condition: template
        value_template: >-
          {{ states('select.__SIGEN_PLANT_SLUG___plant_remote_ems_control_mode')
             in ['Command Discharging (Output power from PV first)',
                 'Command Discharging (Output power from the battery first)',
                 'Maximum Self-consumption (Default)'] }}
```

Leave the existing action unchanged so it selects the configured idle/Standby mode and applies both zero limits.

- [ ] **Step 4: Run focused and package tests**

Run:

```bash
python3 -m unittest scripts.tests.test_sigen_ems_package.SigenIdleSelfConsumptionTests -v
python3 -m unittest scripts.tests.test_sigen_ems_package -v
```

Expected: all tests pass.

- [ ] **Step 5: Render and run repository verification**

Run:

```bash
./scripts/render-ha-packages.sh
python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v
git diff --check
```

Expected: rendering succeeds, all discovered tests pass, and `git diff --check` prints no errors.

---

### Task 3: Deploy and verify the live idle and floor contracts

**Files:**
- Verify only: `config/site.env`
- Deploy: rendered Home Assistant package through `scripts/deploy-home-assistant.sh`

**Interfaces:**
- Consumes: the configured Odroid HA Container target from `config/site.env`.
- Produces: live native self-consumption during a true idle plan and retained Standby/zero-limit behavior at the SOC floor.

- [ ] **Step 1: Confirm deploy prerequisites**

Read `config/site.env`, verify the configured HA target is the active Odroid Container host, verify the old rollback HA Core remains stopped, and record the current states of:

```text
input_boolean.emhass_auto_control_master
binary_sensor.emhass_control_allowed
sensor.p_batt_forecast
select.<SIGEN_PLANT_SLUG>_plant_remote_ems_control_mode
number.<SIGEN_PLANT_SLUG>_plant_max_charging_limit
number.<SIGEN_PLANT_SLUG>_plant_max_discharging_limit
```

Expected: only the Odroid HA Core is active; master and control state are known before deployment.

- [ ] **Step 2: Deploy through the repository script**

Run:

```bash
./scripts/deploy-home-assistant.sh
```

Expected: rendering and the HA Container configuration check pass before restart; deployment completes without changing the master helper.

- [ ] **Step 3: Verify true idle behavior without overriding a non-idle plan**

If the published plan is inside the configured deadband, invoke the normal plan-apply path and verify:

```text
Sigenergy mode = Maximum Self-consumption (Default)
charging limit = min(operator cap, device advertised maximum)
discharging limit = min(operator cap, device advertised maximum)
```

If the plan is not idle, record that live idle verification is pending; do not manufacture an idle command.

- [ ] **Step 4: Verify floor configuration without draining the battery**

Inspect the live rendered automation and confirm its floor trigger/condition includes native self-consumption and its action still selects Standby and applies `script.sigen_apply_idle_limits`. Do not lower battery SOC or raise the configured floor solely to manufacture a live cutout.

Expected: the live package matches the tested floor contract.

- [ ] **Step 5: Update operational memory**

Append a concise entry to `memory-bank/activeContext.md` recording the deployment result, HA configuration-check result, master state, whether live idle verification was exercised or deferred, and the retained SOC-floor behavior.
