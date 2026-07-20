# EMHASS SOC Recovery Freshness — TDD Remediation Review

**Review status:** Request changes

**Reviewed branch:** `feature/emhass-soc-recovery-freshness`

**Reviewed base:** `2d40da265d819dc03f194f681651782d04cd0ce7`

**Worktree:** `/Users/halil/workspace/misc/energy-stack/.worktrees/feature/emhass-soc-recovery-freshness`

The implementation is close, and the completed live checks validate the new
freshness marker and safe master-off behavior. It is not merge-ready until the
five Important findings below are resolved with test-first changes.

## Governing behavior

- A completed MPC plan, rather than the `:00`/`:30` clock edge, is the primary
  event that applies normal EMHASS control.
- Turning control from blocked to allowed applies the current valid plan without
  waiting for another half-hour boundary.
- A periodic fallback may retry missed events, but must not race the MPC job at
  `:00` or `:30` and suppress the just-completed plan through the five-minute
  write limiter.
- Recovery may start only with a valid SOC in the inclusive range `0..100` and
  below the configured floor.
- Once recovery starts, it owns the actuator until master-off, sustained invalid
  SOC, or a valid SOC at or above the target clears it.
- Active recovery always writes the effective clamped charge limit, including
  zero. A cap change or power-threshold change must not clear recovery.
- Publishing the successful-MPC marker retries both request failures and
  response-read I/O/protocol failures. Exhaustion becomes `RuntimeError`, so the
  existing finalizer records `failure_kind=status_publish`.

## Mandatory TDD protocol

Work only in the feature worktree above. Do not deploy, toggle the Home
Assistant master, stage files, or create commits.

For each cycle below:

1. Add only the named test or smallest coherent group of tests.
2. Run the focused command and capture the expected failure. A test that passes
   before the production edit is not a valid RED; strengthen or correct it.
3. Make the minimum production change that satisfies that test.
4. Run the focused test again and confirm GREEN.
5. Run both affected focused suites before moving to the next cycle.
6. Refactor only while green. Do not weaken an assertion to accommodate the
   implementation.

The Home Assistant tests are structural by necessity. Scope every assertion to
the exact automation trigger or action branch. Do not create a Python copy of
the Jinja decision logic: that would test the copy rather than the deployed YAML.
For the HTTP tests, mock only the `urlopen` boundary and exercise the real
`publish_ha_input_datetime()` retry loop.

## TDD cycle 1 — apply the plan when it becomes actionable

**Finding:** `emhass_sigen_apply_plan_scheduled` runs only at `:00` and `:30`.
That can apply the previous plan while `emhass-cron` is still running. The
successful marker then advances without triggering another apply. It also
leaves a newly enabled master idle until the next boundary.

**Files:**

- Test: `scripts/tests/test_sigen_ems_package.py`
- Production: `home-assistant/ha_sigen_ems.yaml`

### RED

Add a focused test such as
`test_normal_apply_uses_plan_completion_control_enable_and_safe_retry`.
Extract only the block between `emhass_sigen_apply_plan_scheduled` and
`emhass_sigen_disable_on_stale_plan`, then require all of the following:

- A state trigger with a distinct ID for
  `input_datetime.emhass_last_successful_mpc`.
- A state trigger with a distinct ID for
  `binary_sensor.emhass_control_allowed` changing to `"on"`.
- A five-minute periodic retry trigger.
- The periodic retry is explicitly suppressed at minute `0` and minute `30`.
  Direct marker/control triggers must remain allowed at those minutes.
- The rate limit accepts an elapsed interval of exactly 300 seconds
  (`>= 300`, not `> 300`).
- The old `minutes: "/30"` trigger is absent.
- The existing `binary_sensor.emhass_control_allowed == on` and
  `input_boolean.emhass_recovery_active == off` gates remain present.

Prefer assertions against complete trigger fragments and the complete retry
guard. A bare assertion that the file contains the marker entity is too weak.

Run:

```bash
python3 -m unittest \
  scripts.tests.test_sigen_ems_package.EmhassRecoveryStateMachineTests.test_normal_apply_uses_plan_completion_control_enable_and_safe_retry \
  -v
```

Expected RED: the marker/control triggers and safe retry guard are absent, and
the old `/30` trigger is still present.

### GREEN

Change the existing normal-apply automation rather than adding a second writer:

- Trigger on the successful-MPC marker changing.
- Trigger when `binary_sensor.emhass_control_allowed` changes to `on`.
- Retain a five-minute retry trigger, but add a trigger-ID-aware condition that
  rejects only retry executions at `:00` and `:30`.
- Keep one shared 300-second write limiter and change it to inclusive `>= 300`.
- Keep the active-recovery exclusion unchanged.
- Update the alias/description so they no longer claim this is only a scheduled
  half-hour action.

The safe-retry condition should express the actual race-prevention behavior,
for example: non-retry events are always eligible for this condition; retry
events are eligible only when `now().minute` is not `0` or `30`. The shared
write limiter still decides whether an eligible event may write.

Re-run the focused test, then:

```bash
python3 -m unittest scripts/tests/test_sigen_ems_package.py -v
```

## TDD cycle 2 — reject invalid SOC before recovery starts

**Finding:** the recovery-start `numeric_state` condition checks only that SOC
is below the floor. A numeric negative SOC therefore qualifies as a start.

**Files:**

- Test: `scripts/tests/test_sigen_ems_package.py`
- Production: `home-assistant/ha_sigen_ems.yaml`

### RED

Extend `test_recovery_start_requires_floor_freshness_and_charge`, or add
`test_recovery_start_requires_soc_in_valid_range`. In the exact
`emhass_recovery_start` block, require a template condition equivalent to:

```jinja2
{% set soc = states('sensor.__SIGEN_PLANT_SLUG___plant_battery_soc') | float(none) %}
{{ soc is not none and 0 <= soc <= 100 }}
```

Also assert that this valid-SOC condition appears before
`service: script.sigen_start_recovery`. Keep the existing below-floor,
freshness, charge-command, device-max, and rate-limit assertions.

Run the new test directly. Expected RED: the recovery-start block has no lower
or upper SOC validity bound.

### GREEN

Add the valid-SOC template condition to `emhass_recovery_start` before the
below-floor condition. Do not change the start threshold or add a new recovery
cancellation reason.

Re-run the focused test and the complete Sigenergy package suite.

## TDD cycle 3 — stop immediately at the exact valid target

**Finding:** the `numeric_state` trigger uses strict `above`, so an SOC change
to exactly the target does not trigger reconciliation. The periodic trigger can
leave charging active for up to another five minutes.

**Files:**

- Test: `scripts/tests/test_sigen_ems_package.py`
- Production: `home-assistant/ha_sigen_ems.yaml`

### RED

Add `test_recovery_target_trigger_is_inclusive_and_soc_valid`. Scope the
assertions to the trigger section of `emhass_recovery_reconcile` and require:

- `target_reached` is a template trigger, not a numeric-state `above` trigger.
- The trigger parses SOC with `float(none)`.
- The trigger parses the recovery target.
- The expression requires a valid SOC in `0..100` and uses `soc >= target`.
- `above: sensor.emhass_recovery_target_soc` is absent from the trigger section.

Run the test directly. Expected RED: `target_reached` still uses strict
`numeric_state`/`above` semantics.

### GREEN

Replace only the `target_reached` trigger with an inclusive template trigger.
It must become true only when both values parse and SOC is in `0..100` and at or
above the target. Retain the separate `target_changed`, invalid-SOC, periodic,
reconnect, master, and HA-start triggers.

Re-run the focused test and the complete Sigenergy package suite.

## TDD cycle 4 — do not treat SOC above 100 as completion

**Finding:** the completion action currently accepts any non-null
`soc >= target`. SOC `101` can therefore clear recovery through the completion
branch before the sustained-invalid-SOC cancellation path runs.

**Files:**

- Test: `scripts/tests/test_sigen_ems_package.py`
- Production: `home-assistant/ha_sigen_ems.yaml`

### RED

Add `test_recovery_completion_requires_soc_in_valid_range`. Extract the
completion `choose` branch after the invalid-SOC branch and before the reconcile
default. Require the action condition itself—not only the trigger—to include:

```jinja2
soc is not none and 0 <= soc <= 100 and soc >= target
```

This is a defense-in-depth assertion: periodic, reconnect, HA-start, and target
change triggers can all enter the action independently of `target_reached`.

Run the test directly. Expected RED: the completion action has no `soc <= 100`
guard.

### GREEN

Add the inclusive `0..100` bound to the completion branch. Leave the sustained
invalid-SOC trigger and its master/native fallback behavior unchanged. Do not
clear recovery merely because a setpoint is below the power threshold or clamps
to zero.

Re-run the focused test and the complete Sigenergy package suite.

## TDD cycle 5 — retry response-read failures from Home Assistant

**Finding:** `publish_ha_input_datetime()` retries failures raised by
`urlopen()`, but a connection reset or HTTP protocol error raised by
`response.read()` escapes immediately. That bypasses retry exhaustion and can
bypass the finalizer's `status_publish` failure handling.

**Files:**

- Test: `emhass-cron/tests/test_fetch_prices_emhass.py`
- Production: `emhass-cron/fetch_prices_emhass.py`

### RED A — response I/O failure can recover

Add a test to `PublishHaInputDatetimeTests` that supplies two realistic context
manager responses:

1. The first response's `read()` raises `ConnectionResetError`.
2. The second response's `read()` returns the normal empty JSON body.

Patch `time.sleep`, invoke the real publisher, and assert two `urlopen` calls,
one retry delay, and the same timestamp payload on both attempts. Do not assert
only that a mock method was called; the important behavior is that the real
retry loop survives the read failure and completes.

Expected RED: `ConnectionResetError` escapes after the first attempt.

### RED B — exhausted protocol failures normalize to RuntimeError

Add a second test whose response `read()` raises a standard-library HTTP
protocol exception such as `http.client.IncompleteRead` on every attempt.
Require three attempts, two sleeps, and a final `RuntimeError` whose cause is
the protocol exception.

Expected RED: the protocol exception escapes unnormalized on the first attempt.

Run both new tests directly, then make the production edit.

### GREEN

Extend the retryable exception boundary around both `urlopen()` and
`response.read()` to cover relevant standard-library I/O and HTTP protocol
exceptions (for example `OSError` and `http.client.HTTPException`, while
retaining the explicit existing URL/timeout cases). Preserve:

- The bounded `max_attempts` loop.
- One idempotent request body and completion timestamp across attempts.
- Sleeping only between attempts.
- `RuntimeError` with the final exception as its cause after exhaustion.
- Success only after `response.read()` completes.

Do not catch `BaseException` or all `Exception`; programmer errors must remain
visible.

Run:

```bash
python3 -m unittest \
  emhass-cron/tests/test_fetch_prices_emhass.py \
  -v
```

The existing
`FinalizeSuccessfulMpcRunTests.test_marker_failure_records_pipeline_failure`
must remain green; together with the new normalization test it proves the
failure reaches `failure_kind=status_publish`.

## Documentation consistency after GREEN

Only after all production tests are green, update the existing design/plan so a
future implementation agent cannot reintroduce reviewed defects:

- Document marker/control-enabled normal apply plus the non-boundary retry.
- Replace the plan's stale `minutes: "/30"` normal-apply instruction.
- Replace the plan's strict target `above` trigger with the inclusive valid-SOC
  trigger.
- Add the valid-SOC recovery-start and completion bounds.
- Add response-read I/O/protocol errors to the publisher retry contract and test
  examples.

Keep the selected global recovery rule intact: threshold/cap changes may pause
active charging by applying a clamped zero, but they do not cancel the recovery
latch.

## Final verification gate

Run from the feature worktree after all five cycles:

```bash
python3 -m unittest discover -s emhass-cron/tests -p 'test_*.py' -v
python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v
./scripts/render-ha-packages.sh
git diff --check
```

Then inspect the final scoped diff and verify:

- Normal application has marker, control-enabled, and safe periodic triggers;
  it has no direct `/30` trigger.
- Recovery start and completion both reject SOC outside `0..100`.
- Exact-target SOC triggers immediate completion.
- `sigen_apply_active_recovery` still applies the clamped limit including zero.
- Recovery is still cleared only for master-off, sustained invalid SOC, or a
  valid SOC reaching the target.
- The publisher retries response-read I/O/protocol failures and normalizes final
  exhaustion to `RuntimeError`.
- `sensor.emhass_mpc_status` remains absent from actuator decisions.
- No live deployment or master-control change was made during remediation.

Return the RED and GREEN command outputs, the full-suite counts, render result,
and `git diff --check` result for re-review. Do not claim merge-ready until a
fresh code review confirms these five findings are closed.
