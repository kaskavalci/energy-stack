# EMHASS SOC Recovery Freshness Design

**Date:** 2026-07-20

**Author:** GPT-5.6 Sol

**Status:** Approved design, awaiting written-spec review

## Problem

The below-floor recovery automation stopped applying valid EMHASS charge
commands because commit `ebd0df6` made it depend on
`sensor.emhass_mpc_status` and its `plan_generated_at` attribute. That entity is
not defined or maintained by the repository. Once its transient live state
became `failed` and stopped updating, every later recovery attempt was blocked
even when EMHASS published a current charge plan.

Using `sensor.p_batt_forecast.last_updated` as the only freshness signal is not
safe either. Home Assistant calls EMHASS `publish-data` every five minutes, so a
cached plan can receive a recent entity update time without a new optimization
having succeeded.

## Goals

- Preserve the previously successful recovery policy: wait below the SOC floor
  until EMHASS issues a fresh charge command, then charge to the configured
  recovery target.
- Make successful MPC generation a source-controlled, restart-safe contract
  between `emhass-cron` and Home Assistant.
- Prevent cached five-minute publications from being mistaken for a new MPC
  result.
- Keep master-off and invalid-data behavior fail-safe.
- Use the configured SOC floor and recovery target; do not hardcode 5%, 10%, or
  15% in the recovery logic.

## Non-goals

- Do not force a grid charge when EMHASS has not issued a fresh charge command.
- Do not change price optimization, forecast methods, MPC cadence, prediction
  horizon, or battery power caps.
- Do not migrate EMHASS's temporary load/PV history placeholders. That remains
  separate work, gated by successful full-day Home Assistant history queries.
- Do not repair or delete Home Assistant recorder data as part of this change.

## Considered approaches

### 1. Durable successful-run marker plus explicit recovery state (selected)

`emhass-cron` records a successful MPC completion in a repository-defined Home
Assistant helper only after optimization and plan publication both succeed.
Home Assistant derives plan freshness from that marker. A recovery-active
helper makes charging to the recovery target explicit and restart-safe.

This retains the intended stale-plan protection without depending on an
unowned runtime entity.

### 2. Restore `p_batt_forecast.last_updated`

This is the smallest rollback, but it treats five-minute cached publications as
new plans. It does not prove a new MPC run succeeded and is therefore rejected.

### 3. Force recovery charging whenever SOC is below the floor

This guarantees SOC recovery but bypasses EMHASS's price decision and changes
the system's operating policy. It is rejected because the requested behavior
is to wait for an EMHASS charge command.

## Architecture

### Successful MPC marker

Home Assistant will define a restart-restored
`input_datetime.emhass_last_successful_mpc`. `emhass-cron` will update that
helper through the authenticated Home Assistant API after this sequence
completes:

1. Price retrieval and CSV generation succeed.
2. Weather-cache validation succeeds.
3. `naive-mpc-optim` succeeds.
4. `publish-data` succeeds.

The timestamp must not advance on dry runs, publish-only actions, price
failures, weather failures, optimizer failures, or publication failures. The
status update is part of the MPC execution contract. Capture one completion
timestamp after `publish-data`, then make at most three idempotent update
attempts using that same timestamp, with two seconds between attempts. If all
three attempts fail, the cron run is recorded as failed with
`failure_kind=status_publish` because Home Assistant cannot safely prove the
new plan's provenance. The retry boundary includes failures opening the request
and I/O or HTTP protocol failures while reading the response. A successful
request is recognized only after the response body is read; final exhaustion is
normalized to `RuntimeError` chained from the last retryable exception.

The local `last_enever_run.json` remains the detailed energy-host health
artifact. The Home Assistant helper is the minimal cross-host actuator
contract, not a replacement for local failure diagnostics.

### Central plan-freshness sensor

Home Assistant will define one source-controlled template binary sensor for MPC
freshness. It is `on` only when all of the following are true:

- `input_datetime.emhass_last_successful_mpc` contains a valid timestamp;
- the timestamp is no more than five minutes in the future, allowing bounded
  host clock skew;
- the timestamp is less than 40 minutes old;
- `sensor.p_batt_forecast` is numeric and available; and
- `sensor.optim_status` is `Optimal`.

Both normal control and below-floor recovery will consume this sensor instead
of maintaining separate freshness rules. The normal-control diagnostic will
continue to expose `plan_missing`, `plan_stale`, and `optim_not_optimal` reasons
with the durable completion timestamp used for `plan_stale`.

`sensor.emhass_mpc_status` will no longer be an actuator dependency. Any
remaining transient instance may be removed during deployment after the new
contract is verified.

### Normal plan application

Normal control applies when the successful-MPC marker changes and when
`binary_sensor.emhass_control_allowed` changes to `on`. A five-minute periodic
trigger retries missed events, but that retry alone is suppressed at minute
`0` and `30` so it cannot race the MPC job and consume the shared five-minute
write limiter before the completed plan arrives. Marker and control-enabled
events remain eligible at those minutes. There is no direct half-hour
normal-apply trigger, and the limiter accepts exactly 300 elapsed seconds.

### Recovery state machine

Home Assistant will define a restart-restored recovery-active helper and a
restart-restored recovery charge setpoint in watts. The stored setpoint is the
minimum of the absolute EMHASS charge request, operator charge cap, and
device-advertised charge maximum. The state transitions are:

#### Waiting

- Master control is on.
- SOC parses and is in the inclusive range `0..100`, and is below the
  configured SOC floor.
- The system remains in the existing safe/native state until a qualifying
  EMHASS charge command appears.
- A command qualifies only when the central MPC-fresh sensor is on and
  `p_batt_forecast` is more negative than the configured power threshold.

#### Recovery active

- On the qualifying command, store the requested charge wattage, mark recovery
  active, select Sigenergy command-charge mode, and apply the charge limit.
- Existing scripts continue to clamp the requested power to both the operator
  cap and the device-advertised maximum.
- Active reconciliation always writes the effective clamped limit, including
  zero. Operator-cap or power-threshold changes may pause charging at zero, but
  do not clear the recovery latch.
- Once started, recovery remains active until SOC reaches the configured
  recovery target. A later MPC failure or non-charge command does not abandon a
  recovery already authorized by a fresh charge command.
- A five-minute reconciliation plus HA-start and Sigenergy-reconnect triggers
  reapply the stored recovery setpoint while recovery is active. This makes the
  state restart-safe rather than relying on the inverter retaining its last
  command.
- Ordinary normal-plan application and ordinary stale-plan fallback must not
  override an active recovery. Master-off and invalid-SOC cancellation retain
  higher priority.

#### Recovery complete

- When valid SOC in the inclusive range `0..100` reaches or exceeds the
  configured recovery target, clear recovery active immediately. The
  target-reached trigger uses inclusive `soc >= target` semantics, including an
  exact-target transition.
- If normal MPC control is currently allowed, immediately apply the current
  EMHASS plan rather than waiting for the next half-hour boundary.
- If normal MPC control is not allowed, restore Sigenergy native
  self-consumption instead of leaving command-charge mode latched.

#### Cancellation and invalid data

- Turning the master off clears recovery state and invokes the existing native
  fallback path.
- Invalid or unavailable SOC while recovery is active cancels recovery and
  invokes native fallback. Charging must not continue indefinitely when Home
  Assistant cannot observe the stop target.
- A configured recovery target that is lowered below current valid SOC
  completes recovery within the five-minute reconciliation interval.

## Data flow

```text
emhass-cron
  -> prices/weather/optimization
  -> publish-data
  -> update input_datetime.emhass_last_successful_mpc
  -> binary_sensor.emhass_mpc_fresh
  -> fresh negative p_batt command while SOC is below floor
  -> recovery active + stored charge setpoint
  -> Sigenergy command charging
  -> SOC reaches configured recovery target
  -> immediate normal-plan reconciliation or native fallback
```

## Error handling and observability

- A failed successful-run-marker update makes the MPC pipeline fail visibly;
  the previous timestamp remains unchanged and naturally becomes stale.
- The durable helper and freshness/recovery entities provide Home Assistant
  observability without requiring a dashboard change in this fix.
- Existing monitor alerts remain responsible for optimizer, weather, price,
  and stale-run failures. Recovery-stalled diagnostics should use the explicit
  recovery-active state instead of inferring recovery only from low SOC.
- Logbook entries will record recovery start, cancellation with reason, target
  completion, and whether completion resumed MPC or native control.

## Testing

### Unit and structural tests

- Verify the Home Assistant API request used to update the successful-run
  helper, including authentication, entity ID, timestamp, three attempts, and
  the two-second retry interval.
- Verify response-read `ConnectionResetError` can recover on retry and exhausted
  `IncompleteRead` failures make three attempts before becoming a chained
  `RuntimeError`.
- Verify the marker advances only after a successful optimize-and-publish chain.
- Verify marker-update failure returns a failed cron result and leaves the
  previous successful timestamp untouched.
- Verify the rendered Home Assistant package defines the durable helpers and
  central freshness sensor.
- Verify neither normal control nor recovery depends on
  `sensor.emhass_mpc_status` or `p_batt_forecast.last_updated`.
- Verify recovery starts only for a fresh charge command below the SOC floor,
  with SOC in `0..100`, persists to the configured recovery target, and stores
  a clamped setpoint.
- Verify normal application uses marker/control-enabled events plus a
  non-boundary five-minute retry, with no direct `/30` trigger.
- Verify completion requires SOC in `0..100`, exact-target SOC completes
  immediately, and active recovery applies a clamped zero without clearing its
  latch.
- Verify master-off, invalid SOC, target completion, HA start, and Sigenergy
  reconnect transitions select the intended safe action.

### Repository verification

- Run the focused cron and Sigenergy package tests.
- Run all repository tests affected by Home Assistant package rendering.
- Render Home Assistant packages and require a clean render.
- Run `git diff --check` and confirm unrelated dirty-worktree changes remain
  untouched.

### Live verification

1. Read `config/site.env`; do not hardcode live hosts or entity IDs.
2. Keep the master off during the partial deployment.
3. Deploy in the repository-prescribed order: energy host, then Home Assistant.
4. Run Home Assistant configuration validation before restart.
5. Run one manual MPC and verify the durable completion timestamp advances only
   after `publish-data` succeeds.
6. Verify the freshness sensor becomes on and expires after 40 minutes without
   another successful run.
7. With master still off, validate the recovery templates against representative
   low-SOC, stale-plan, charge-plan, completion, and invalid-SOC inputs.
8. Enable master control only after the marker, plan, SOC, device availability,
   and native fallback are all healthy.
9. Observe at least two normal cron slots. If a genuine below-floor event is not
   safely available, do not manipulate reported SOC; verify the recovery state
   machine through automation traces and template evaluation.

## Deployment and rollback

The energy-host producer and Home Assistant consumer must be deployed together.
During a partial deployment, the master remains off so the existing native
self-consumption fallback owns the plant.

Rollback consists of turning the master off and restoring the prior package and
cron versions. Native self-consumption remains the safe operating state. The new
helpers are inert if their automations are rolled back and do not need to be
deleted during an emergency rollback.

## Acceptance criteria

- A fresh EMHASS charge command received below the configured SOC floor starts
  recovery charging at the capped EMHASS-requested power.
- Recovery charging continues until the configured recovery target is reached,
  including across HA or Sigenergy reconnects.
- Valid SOC in `0..100` reaching or exactly equaling the target immediately
  exits recovery and reconciles to normal MPC or native control.
- Threshold or cap changes may pause active recovery at a clamped zero but do
  not cancel the recovery latch.
- Cached five-minute `publish-data` calls cannot refresh the successful-MPC
  timestamp.
- A failed or missing MPC marker blocks starting a new recovery but does not
  abandon recovery already authorized by a fresh charge command.
- Master off or invalid SOC cancels recovery and restores native control.
- The actuator path has no dependency on the transient
  `sensor.emhass_mpc_status` entity.
- Load/PV history placeholder migration remains unchanged and out of scope.
