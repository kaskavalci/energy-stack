# EMHASS idle self-consumption design

**Date:** 2026-07-22
**Amended:** 2026-07-24, 2026-09-05

## Goal

When a healthy, actionable EMHASS plan falls into the existing idle/deadband
branch, Sigenergy must run native `Maximum Self-consumption (Default)` instead
of Standby with zero charge and discharge limits.

The trading dashboard continues to label these plan rows `idle`. This label
describes the MPC deadband, while the inverter behavior during that deadband is
native self-consumption.

## Control behavior

| Condition | Sigenergy behavior |
|---|---|
| Healthy plan, charge command outside deadband | Existing command-charge path |
| Healthy plan, discharge command outside deadband | Existing command-discharge path |
| Healthy plan, idle/deadband | Native maximum self-consumption with restored, confirmed device-valid limits |
| SOC at or below the safety floor during command-discharge | Native maximum self-consumption with restored, confirmed device-valid limits |
| SOC at or below the safety floor during idle/deadband | Native maximum self-consumption with restored, confirmed device-valid limits |
| Master off or control failure | Existing native self-consumption fallback |
| Active SOC recovery | Existing recovery charge path |

True idle/deadband has priority over the SOC-floor cutout. The floor remains a
safety stop for explicit command-discharge only.

## Implementation

`script.sigen_apply_emhass_plan` will keep the existing charge and discharge
branches and add an explicit idle/deadband branch. Only a battery command inside
the configured deadband (`-threshold <= p_batt_forecast <= threshold`) will call
the existing `script.sigen_restore_native_control`. That transaction already:

1. selects `Maximum Self-consumption (Default)`;
2. waits for mode confirmation;
3. restores charge and discharge limits, clamped to the operator and device
   maxima;
4. waits for both limit confirmations; and
5. records `input_datetime.emhass_last_limit_write` only after success.

The final default/fallback branch will also restore native `Maximum Self-consumption (Default)`
with restored, confirmed device-valid limits (reason `idle`). It covers non-deadband cases
rejected by another guard, including a discharge command reduced below the power threshold
by SOC headroom, preventing the inverter from remaining trapped in Standby with 0 kW limits.

The SOC-floor cutout will cover only the two command-discharge modes. Native
self-consumption will not appear in its mode trigger or condition. When SOC hits the safety
floor during command-discharge, the cutout preempts active discharge writers and immediately
calls `script.sigen_restore_native_control` (reason `soc_floor`), restoring native maximum
self-consumption and confirmed limits without waiting for the next apply cycle.
Recovery charging remains unchanged and may subsequently take control when MPC publishes
a qualifying fresh charge command.

No dashboard wording changes are included.

## Tests

Regression tests will assert that:

- the explicit true-idle/deadband branch calls
  `script.sigen_restore_native_control`;
- non-deadband fallthroughs restore native self-consumption with confirmed limits;
- a discharge command rejected by the SOC-headroom guard safely restores native
  self-consumption;
- the SOC-floor safety automation excludes native self-consumption while
  immediately restoring native maximum self-consumption and limits upon cutout;
- the floor cutout is restartable and preempts limit writers before restoring native control; and
- existing native restoration, recovery, and limit-clamp tests continue to
  pass.

## Deployment and live verification

After local rendering and tests pass:

1. deploy the Home Assistant package with the repository deploy script;
2. require the HA Container configuration check to pass before restart;
3. deploy only to the configured Odroid Home Assistant target;
4. verify the Odroid master remains on and control is allowed;
5. during an actionable idle/deadband step, verify the reported Sigenergy mode
   is `Maximum Self-consumption (Default)` and both limits are restored to
   device-valid values; and
6. verify SOC-floor safety configuration excludes native self-consumption and
   restores native maximum self-consumption with confirmed limits upon cutout.

If no idle/deadband step is active during deployment, live mode verification
will be limited to a safe invocation of the normal plan-apply path when the
published plan is idle. The implementation will not override a non-idle MPC
command merely to manufacture an idle test.

## Change scope

Modify only this behavior, its tests, and this documentation. Preserve all
unrelated working-tree changes.
