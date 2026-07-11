# Actionable EMHASS Alert Plan

## Summary

Reduce HA-side alert fatigue by changing notifications from "every safety/state transition pushes" to "one root cause, one actionable push." Keep all safety automations active, but stop phone notifications for expected self-healing states like SOC floor cutout and recovery progress.

## Key Changes

- In `home-assistant/ha_emhass_dashboard.yaml`, add a stable `block_reason`/`block_action` diagnostic to `binary_sensor.emhass_control_allowed`, with safe guards for missing `sensor.p_batt_forecast` to avoid reload template errors.
- In `home-assistant/ha_sigen_ems.yaml`, keep D2/D4/D5 safety behavior unchanged, but remove phone pushes from normal SOC floor, recovery charge, and recovery complete paths. Log those as non-actionable HA status events instead.
- In `home-assistant/ha_emhass_monitoring.yaml`, collapse D1/D2/D3/D4 into root-cause alerts:
  - No D1/D2/D3 phone push when `block_reason=soc_below_floor`.
  - Add one actionable recovery-stalled alert if SOC remains below reserve for 6 hours, or SOC data is stale for 2 hours while master is on.
  - Keep real faults actionable: stale plan, bad optim, missing prices, P1/Sigen/MQTT/SOC sensor failures.
- Adjust C1 PV alert to avoid dawn noise: only evaluate PV staleness when sun elevation is high enough, default `> 8°`, and require 30 minutes of sustained failure.
- Remove repeated HA pushes where a state remains bad, especially A2's `/30` repeat behavior. Alert once per failing transition; rely on state recovery and the dashboard for ongoing status.
- Update alert message text so every phone push contains an explicit "Action:" sentence and avoids vague messages like "Control gates failed."

## Public Interfaces

- Keep `script.emhass_push_alert(title, message)` unchanged.
- Keep `binary_sensor.emhass_control_allowed` semantics unchanged.
- Add diagnostic attributes to `binary_sensor.emhass_control_allowed`:
  - `block_reason`: `ok`, `master_off`, `optim_not_optimal`, `plan_missing`, `plan_stale`, `soc_below_floor`, `soc_invalid`, `prices_stale`
  - `block_action`: short operator-facing action text
- Add one HA-side problem sensor for stalled recovery, e.g. `binary_sensor.emhass_fail_recovery_stalled`.

## Test Plan

- Render HA packages with `./scripts/render-ha-packages.sh` and verify no unresolved `__PLACEHOLDER__` tokens in `.rendered/home-assistant`.
- Run existing config merge tests: `PYTHONPATH=scripts/lib python3 -m unittest scripts.tests.test_ha_config_merge`.
- Deploy with `./scripts/deploy-home-assistant.sh`; rely on its HA `ha core check`.
- After deploy, verify:
  - SOC floor state does not produce D1/D2/D3/D4 phone cascade.
  - A stale plan or bad optim still produces one actionable push.
  - C1 PV does not fire immediately at dawn unless PV remains stale after the elevation and duration gates.
  - Recovery charge/complete are visible in HA status/logbook but do not push to phone.

## Assumptions

- Chosen policy: "Actionable only." Phone pushes are for user intervention; normal self-healing state changes are dashboard/logbook status.
- No changes to EMHASS MPC, price fetching, weather cache, or `energy-monitor` host alerts are needed for this alert-fatigue fix.
- After implementation, update `memory-bank/activeContext.md` and add/extend a notebook note summarizing the new alert policy.
