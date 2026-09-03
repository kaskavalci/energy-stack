# EMHASS Load Quality and Freshness Design

**Date:** 2026-09-02  
**Status:** Draft approved with critical amendments; residual hold-window risk noted  
**Scope:** Home Assistant load sampling and `emhass-cron` native load validation  
**Change type:** Reliability and observability improvement  
**No deployment is included in this document.**

## 1. Summary

The current EMHASS design is safe but not robust enough for this site. It
calculates house load from three sources with different update rates:

| Source | Site update behavior |
|---|---|
| P1 net grid power | About 1 second |
| Sigen battery power | About 5 seconds |
| Envoy PV power | About 5–10 minutes during microinverter updates |

```text
load = max(grid + PV - battery_power, 0)
```

During the 2026-09-01 incident, Envoy remained at about `1.661 kW` while
the battery charged at about `2.630 kW` and the P1 meter exported about
`0.200 kW`. The calculation produced about `-1.169 kW` and changed it to
`0 W`. This negative value was a normal temporary effect of the known Envoy
delay, not proof of bad wiring or an invalid plant state.

Home Assistant Recorder stored the triggered sample every minute. The
default Home Assistant history API request returned only significant changes
and removed unchanged `0 W` records. The current cron request does not set
`significant_changes_only=0`, so cron saw a false history gap. Requesting
attributes alone does not prevent this filtering.

The revised design has five parts:

1. Request all triggered load samples with
   `significant_changes_only=0`.
2. Use explicit `sampled_at` timestamps instead of `last_changed` for sample
   freshness.
3. Model the source update rates instead of using one `last_updated` spread
   rule.
4. Use a bounded `held_estimate` during normal Envoy lag; do not turn it into
   zero and do not fail closed for every large negative balance.
5. Retry one transient load-data failure before the normal fail-closed action.

There is no confirmed direct whole-home load sensor on this site. The P1
sensor measures the utility boundary, and the Sigen consumed-power sensor
does not see the AC-coupled Envoy PV. The composite estimator is therefore
required for now.

The numeric load state and its quality are separate. An invalid estimate must
not be represented by the string `unavailable`, because that pollutes the
numeric history used by EMHASS. Cron must reject a numeric record when its
`load_quality` is `invalid`.

## 2. Incident evidence

The 2026-09-01 event followed this path:

| Time (CEST) | Evidence | Result |
|---|---|---|
| 12:20–12:33 | Envoy stayed near `1.661 kW`; P1 exported about `200 W`; Sigen charged at about `2.630 kW` | Composite balance became about `-1.169 kW` and was clamped to zero |
| 12:15–12:35 | Recorder contained one triggered sample per minute; default history response omitted unchanged zero states | Cron received a false history gap |
| 12:30 | Prices fetched; load history newest finite sample was 10 minutes old | MPC aborted before weather or optimisation |
| 12:41 | Last successful MPC became older than 40 minutes | Control gate turned off |
| 13:00 | Prices fetched; load history newest finite sample was 6 minutes old | MPC aborted again |
| 13:30 | Fresh non-zero load sample; 21/21 half-hour bins valid | MPC and publish succeeded |

The energy containers stayed running. The logs show no price failure,
weather failure, solver failure, container restart, host reboot, OOM event, or
network timeout in the failed runs.

The direct cause of the MPC outage was the history API filtering combined with
the five-minute freshness rule. The physical cause of the zero balance was
the known Envoy reporting delay compared with the P1 and Sigen update rates.
The history API query fix removes the false stale condition. The estimator
change prevents the slow Envoy value from becoming a fake zero.

The same zero-clamp and stale-history pattern was recorded on 2026-08-05.
This shows that the problem is a repeatable design weakness, not only a single
external incident.

## 3. Goals

- Return every one-minute triggered sample to cron, including unchanged
  numeric values.
- Accept a real zero load as fresh when the sampling job ran recently.
- Model Envoy's slower update rate and its report timestamps.
- Avoid treating normal Envoy lag as an invalid electrical state.
- Avoid turning a lagged negative balance into a fake zero.
- Detect source failure, excessive Envoy age, and missing fallback data.
- Keep MPC fail-closed when the load quality is invalid.
- Give the operator the reason for a rejected sample.
- Reduce short outages caused by temporary source delay.
- Keep the current 30-minute MPC cadence and 32-step maximum horizon.
- Preserve the current no-stale-data policy for unsafe load history.

## 4. Non-goals

- No change to price-fetch policy. Price failure still aborts MPC.
- No stale CSV fallback.
- No automatic battery control while the MPC plan is stale.
- No synthetic Home Assistant recorder states written by cron.
- No automatic update of Docker images.
- No change to the EMHASS optimisation model or solver.
- No blind switch to the P1 grid-consumption sensor.
- No claim that a composite estimate is equal to a direct load measurement.

## 5. Current design and failure mode

The current calculation is in
[`home-assistant/ha_emhass_sensors.yaml`](../../home-assistant/ha_emhass_sensors.yaml).
It reads Envoy PV, P1 net grid power, and Sigen battery power. It then applies
`max(..., 0)`.

The sources do not have the same update rate. A slow Envoy update can make the
current PV value much lower than the real PV output while P1 and Sigen show
current values. A negative composite result is then expected during the
Envoy lag window.

The one-minute triggered sensor already writes a `sampled_at` attribute, but
`emhass-cron` currently requests history with `no_attributes=true` and does
not set `significant_changes_only=0`.

The Home Assistant history endpoint therefore returns only significant state
changes by default. It can omit all intermediate unchanged `0 W` samples even
when Recorder stored them and their `last_updated` and `sampled_at` values
advanced.

The current validator then sees the last returned record, falls back to
`last_changed` when needed, and rejects the history as stale.

This creates two separate errors:

```text
source mismatch → negative balance → hidden as zero
repeated zero → last_changed does not advance → sample judged stale
```

The current `emhass_envoy_required` gate also depends on
`emhass_mpc_fresh`. This couples input collection to the freshness of the
output that the input helps produce. After MPC becomes stale, a stale Envoy
value can be treated as optional and PV can become zero. That can make the
composite load calculation less reliable during recovery.

## 6. Target architecture

```text
P1, Sigen, and Envoy source states
                  │
                  ├── source-specific age and cadence checks
                  ├── raw composite balance
                  ├── lag-aware estimate
                  └── quality + reason + sampled_at
                              │
                   one-minute HA sample history
                              │
          history API: all samples, including unchanged states
                              │
            cron validates timestamp, quality, and 30-minute bins
                              │
                     one short retry if needed
                              │
                       MPC or safe fallback
```

The load value, estimate quality, and sample time are separate fields.

- A timestamp proves that a sample was made.
- Quality describes whether the estimate is direct, calculated, or held.
- A fresh timestamp does not make an invalid estimate valid.

## 7. Proposed design

### 7.1 Direct load source assessment

The preferred long-term source is one sensor that measures total site demand,
including demand supplied by PV and the battery.

**Current-site decision:** A direct whole-home source was investigated and
rejected for the current topology. The composite estimator is required.

Live investigation found no suitable source on this site:

- `sensor.electricity_meter_power_net` measures utility-boundary exchange,
  not total behind-the-meter demand.
- `sensor.sigen_0_consumed_power` does not include the AC-coupled Envoy PV and
  can remain at `0 W` while solar charging occurs.

Do not use `sensor.electricity_meter_power_consumption` as whole-home load
without verifying its meaning. It may be grid import only.

If a future source is proposed, test it during:

1. Grid import with no PV.
2. PV supplying the house.
3. Battery discharge.
4. Battery charge.
5. PV export.
6. Grid and battery idle.

Until a source passes these tests, the composite estimator remains the
authoritative load source.

### 7.2 Source cadence contract

The estimator must not require all `last_updated` timestamps to be close. The
source update rates are different by design, and Envoy can have a slow value
change while still reporting regularly.

| Source | Normal report behavior | Failure rule |
|---|---|---|
| P1 net power | About 1-second reports | Missing or clearly stale `last_reported` |
| Sigen battery power | About 5-second reports | Missing or clearly stale `last_reported` |
| Envoy PV | About 5–10-minute value changes; report may arrive every 60–120 seconds | No `last_reported` message for more than 180 seconds during daylight |

Use `last_reported` for transport freshness and source spread. A 180-second
maximum `last_reported` age and spread is the initial proposal. Use the Envoy
value age separately to detect a slow PV value change. Envoy value age can be
up to about 15 minutes by `last_updated` during normal operation. `last_updated`
must remain a diagnostic field; it must not be used as a common
source-freshness threshold.

The estimator records the age of every source, the `last_reported` spread,
and the Envoy value age. It does not reject a sample only because
`last_updated` values have a large spread.

### 7.3 Lag-aware composite estimator

The raw balance remains visible:

```text
raw_balance_w = grid_w + pv_w - battery_charge_w
```

The estimator has four reported quality states. Quality is a separate attribute or
entity. The load state remains numeric for all four states so that EMHASS
history and its numeric joins remain stable.

#### `valid`

Use the calculated balance when required sources are available and the value
is non-negative, apart from a small measurement tolerance.

For a small negative value, from `-200 W` to `0 W`, use zero and record
`load_quality=valid_noise_clamp`.

#### `held_estimate`

Use this state when:

- P1 and Sigen are fresh;
- Envoy has a numeric value and a recent `last_reported` message;
- Envoy value age is within the observed 5–10-minute update behavior;
- the negative balance is consistent with Envoy reporting behind current
  PV; and
- a recent non-degraded valid load estimate exists.

The output value is the most recent non-degraded valid load estimate, held for
no more than 5 minutes. Its quality is `held_estimate`, and the raw balance is
retained for diagnosis.

This is a bounded estimate, not a claim that the current load is known
exactly. It prevents a known Envoy delay from becoming a fake zero. The
90-second retry is not expected to cover the full Envoy lag window.

#### `invalid`

Use this state when:

- P1 or Sigen is missing or clearly stale;
- Envoy has no recent `last_reported` message during daylight;
- the 5-minute held-estimate limit has expired; or
- the source state does not match any known operating condition.

An invalid sample remains numeric, normally retaining the last estimator value,
but has `load_quality=invalid`. Cron excludes it from the usable MPC history
and uses its existing fail-closed path after retry. The numeric state must
never be used without checking quality.

The estimator exposes:

```text
sensor.emhass_house_load_power_w
  state: selected numeric estimate when usable
  attributes:
    raw_balance_w
    load_quality: valid | valid_noise_clamp | held_estimate | invalid
    invalid_reason
    estimate_reason
    sampled_at
    hold_age_s
    source_ages_s
    source_spread_s
    grid_w
    pv_w
    battery_w
```

The current `max(..., 0)` operation is allowed only for a documented small
noise range. It is not a general error handler.

The last-valid estimate must be held by a stateful trigger-based sensor or an
explicit restored helper. A stateless template must not pretend to hold a
value without a persisted prior value.

#### Residual availability risk

The observed Envoy value lag reached about 9 minutes, while the approved
held-estimate limit is 5 minutes. Therefore this design prevents the false
zero and the history-API false stale result, but it does not guarantee that
MPC will stay available for the full worst-case Envoy lag. If the negative
balance continues after 5 minutes, the quality becomes `invalid` and cron
fails closed. Extending the hold beyond 5 minutes requires a separate control
safety decision.

### 7.4 Explicit sample freshness and history API

The triggered sample sensor continues to run every minute and keeps its
`sampled_at` attribute.

The cron history request must set `significant_changes_only=0` and omit
`no_attributes` (Home Assistant REST treats the presence of `no_attributes` with
any value, including `false`, as a flag to strip attributes):

```text
significant_changes_only=0
(omit no_attributes)
```

This is required. Requesting attributes alone without `significant_changes_only=0`
does not return unchanged records. Omission of `no_attributes` ensures attributes
such as `sampled_at` and `load_quality` are returned.

The validator uses timestamps in this order:

1. `attributes.sampled_at` for the triggered load sample;
2. `last_updated` for compatible records without `sampled_at`;
3. `last_changed` only for old compatible records.

`last_changed` must not be used to declare a repeated numeric zero fresh when
the explicit sample timestamp is absent.

The validator also checks:

- the state is finite and non-negative;
- the quality is `valid`, `valid_noise_clamp`, or `held_estimate`;
- a degraded hold is not older than its maximum age;
- `sampled_at` is timezone-aware and not in the future;
- the newest sample is no older than five minutes;
- all required 30-minute bins are covered.

If a history record has no usable explicit timestamp or quality information,
the validator must fail closed with a clear compatibility error. It must not
assume that an unchanged zero is fresh.

### 7.5 Decouple Envoy input gating from MPC output freshness

Envoy freshness must depend on whether PV may be present, not on whether the
last MPC plan is fresh.

Recommended behavior:

- During the dawn grace period and at night, stale Envoy may be treated as
  zero PV.
- During daylight after dawn grace, require a numeric Envoy value.
- During normal Envoy lag, use `held_estimate` and the bounded last-valid
  estimate.
- After the 5-minute held-estimate limit, mark the estimate invalid.
- Do not use `emhass_mpc_fresh` to decide whether Envoy data is required.
- Keep MPC freshness as an independent actuator-safety gate.

This prevents a stale MPC plan from changing the input data used to recover
the next plan.

The PV forecast can remain an advisory signal. It must not make a stale
daylight Envoy value look current.

### 7.6 One short retry

When load retrieval or validation fails because of an HTTP error, stale source,
or invalid estimate:

1. Wait 90 seconds.
2. Fetch the load history again.
3. Re-run the complete validation.
4. Run MPC only after validation passes.

The retry count is two total attempts. The retry is useful for a short HA
request failure or recovery at the end of the Envoy lag limit. It is not the
main solution for the normal 5–10-minute Envoy delay; the held estimate
handles that case.

The retry does not permit stale load data, zero-fill, or old-plan publication.
If both attempts fail, cron records the failure and does not call EMHASS
optimisation or publish-data. The existing native Sigenergy fallback remains
responsible for safe control.

### 7.7 Observability

Each load validation result must record structured data in
`last_enever_run.json`, for example:

```json
{
  "load_history": {
    "mode": "degraded",
    "quality": "held_estimate",
    "reason": "envoy_update_lag",
    "estimate_w": 860,
    "raw_balance_w": -1169,
    "grid_w": -200,
    "pv_w": 1661,
    "battery_w": 2630,
    "envoy_age_s": 540,
    "hold_age_s": 60,
    "sampled_at": "2026-09-01T10:29:00Z"
  },
  "retry": {
    "attempts": 1,
    "recovered": true
  }
}
```

Diagnostic values must never include tokens, passwords, or other secrets.

Add a Home Assistant diagnostic entity:

```text
binary_sensor.emhass_house_load_valid
```

It is `on` for `valid`, `valid_noise_clamp`, and `held_estimate`, and `off` for
`invalid`. Attributes include `quality`, `reason`, `sampled_at`, `hold_age_s`,
and source ages. The dashboard and monitor must show this reason before
showing only `emhass_mpc_fresh=off`.

## 8. Failure and safety policy

| Situation | Load result | MPC action | Battery action |
|---|---|---|---|
| Fresh coherent composite load | `valid` | Run | Normal MPC control |
| Small negative rounding error | `valid`, zero after noise clamp | Run | Normal MPC control |
| Envoy within normal lag and negative balance | `held_estimate`, bounded last-valid estimate | Run and record degraded quality | Normal MPC control, subject to normal plan gate |
| Envoy report stale or held estimate expired | `invalid` | Retry, then abort | Existing safe fallback |
| P1 or Sigen stale/unavailable | `invalid` | Retry, then abort | Existing safe fallback |
| Repeated true zero | `valid`, fresh by `sampled_at` | Run | Normal MPC control |
| Old successful plan | N/A | No new plan | Control gate off after 40 minutes |

The design does not loosen the five-minute sample freshness rule. It changes
the history query and adds a bounded, clearly labelled degraded estimate for
the known Envoy lag.

## 9. Alternatives considered

### A. Increase the five-minute freshness limit

Rejected. This hides stale data and may allow an old load profile to guide a
new battery plan.

### B. Request attributes but keep the history API default

Rejected. Live evidence shows that attributes alone do not return unchanged
states. `significant_changes_only=0` is required.

### C. Reject every large negative balance

Rejected. Envoy's 5–10-minute update cycle makes large negative transient
balances normal during fast PV changes. This would create an MPC outage for
5–9 minutes around some cloud and schedule transitions.

### D. Treat every negative balance as zero

Rejected. This hides the Envoy lag and underestimates load.

### E. Add a small changing value to force state changes

Rejected. This corrupts the numeric load history and hides the real query
problem.

### F. Use a bounded last-valid estimate during Envoy lag

Preferred for the current site. It models the known source behavior, avoids a
fake zero, and remains bounded by an explicit maximum age. It must be marked
`held_estimate` and must not run indefinitely.

### G. Use a direct whole-home load sensor

Preferred long term, but no suitable source is currently available. The P1
and Sigen candidates found during live investigation do not satisfy the site
contract.

## 10. Repository changes expected after approval

The implementation should stay focused on these files:

- `home-assistant/ha_emhass_sensors.yaml`
  - model source-specific update rates and `last_reported`;
  - add the lag-aware composite estimate;
  - keep the numeric state and separate load quality;
  - use a stateful bounded last-valid estimate;
  - decouple Envoy requirement from MPC freshness;
  - publish the load validity entity.
- `emhass-cron/fetch_prices_emhass.py`
  - request `no_attributes=false`;
  - request `significant_changes_only=0`;
  - parse `sampled_at` and load quality;
  - retain the five-minute explicit sample freshness limit;
  - add one bounded load-history retry;
  - record structured quality details.
- `emhass-cron/README.md`
  - document the history API parameters;
  - document source cadence and degraded Envoy lag;
  - document retry and fail-closed behavior.
- `emhass-cron/tests/test_fetch_prices_emhass.py`
  - add history query, timestamp, quality, and retry tests.
- `scripts/tests/test_emhass_envoy_gate_templates.py`
  - verify Envoy requirement is independent of MPC freshness;
  - verify normal Envoy lag produces `held_estimate` quality.
- `scripts/tests/test_ha_emhass_sensors.py`
  - verify raw balance, noise clamp, held estimate, and invalid quality.
- `docs/ENTITY_CONTRACT.md`
  - document a direct load entity only after its meaning is verified.

No deployment or image change is required for this design.

## 11. Test plan

### Unit tests

- History URL includes `no_attributes=false`.
- History URL includes `significant_changes_only=0`.
- All unchanged zero records are retained by the parser.
- `sampled_at` is the primary freshness timestamp.
- A repeated numeric zero with fresh `sampled_at` passes freshness.
- A missing explicit timestamp fails with a clear compatibility reason.
- Source cadence rules do not reject normal 5–10-minute Envoy lag.
- Small negative balance becomes valid zero with a noise reason.
- Normal Envoy lag produces `held_estimate`, not zero and not invalid.
- The bounded last-valid estimate expires after five minutes.
- Missing or stale P1/Sigen data becomes invalid.
- Envoy with no `last_reported` message within 180 seconds during daylight
  becomes invalid.
- A fresh timestamp cannot make an invalid quality state valid.
- One failed attempt followed by valid data succeeds.
- Two failed attempts abort without optimisation or publish.
- Existing price and weather failure policies remain unchanged.

### Incident replay

Replay the 2026-09-01 source values and history response:

- the history parser returns all 21 one-minute records;
- the `-1,169 W` raw balance is visible as Envoy lag;
- the load estimate uses the recent valid value for the bounded held
  period;
- the load sample remains fresh because `sampled_at` advances;
- no false stale-history abort occurs only because the numeric state is zero;
- no held estimate is used after five minutes;
- no unsafe MPC publish occurs when the estimate is invalid.

### Home Assistant acceptance

- `sensor.emhass_house_load_power_w` exposes raw balance and quality.
- The held estimate remains numeric and has a current `sampled_at`.
- `binary_sensor.emhass_house_load_valid` shows `held_estimate` clearly.
- Daylight Envoy silence remains detectable when MPC is stale.
- Dawn grace still prevents a false failure during the known Envoy startup gap.

The stateful last-valid estimate must survive the intended Home Assistant
restart behavior, or become invalid until a new valid sample is available.

### Live rollout checks

Before enabling the new quality limits:

1. Record one full day of raw source values, source ages, and Envoy update
   intervals.
2. Confirm the proposed 180-second `last_reported` spread limit and
   five-minute hold behavior.
3. Query the incident window with `significant_changes_only=0` and confirm
   all minute records are returned.
4. Run the validator in dry-run mode.
5. Compare the new estimate with the existing composite value.
6. Deploy during a period when manual fallback is available.
7. Observe at least two natural MPC cycles, including one PV transition if
   available.

## 12. Rollout and rollback

Rollout order:

1. Add unit tests for history filtering and source cadence.
2. Deploy Home Assistant templates.
3. Verify config and live entity states.
4. Deploy cron history query, quality validation, and retry logic.
5. Run a dry-run and observe two natural MPC cycles.
6. Enable the lag-aware held estimate.

Rollback must restore the previous HA package and cron image or source state
from the normal deployment backup. It must not enable a second Home Assistant
controller or change the EMHASS master switch as part of rollback.

## 13. Open review questions for Gemini

1. Is a 180-second `last_reported` spread limit correct for the observed
   source update behavior?
2. What source-age limits should apply to P1 and Sigen after one full day of
   live measurements?
3. Is holding the last valid load for five minutes acceptable for the
   native naive load forecast, or should degraded quality reduce control
   authority?
4. Does the chosen Home Assistant trigger-based stateful sensor restore its
   last valid value after restart as required?
5. Should the degraded estimate use the last valid value, a bounded moving
   average, or another estimator?
6. Should degraded load quality create a warning only, or also block a new
   MPC plan after a configurable duration?
7. Is there a future Sigenergy or other meter source that can measure total
   whole-home load, including AC-coupled PV?

## 14. Decision requested

Approve the reliability direction before implementation:

- `significant_changes_only=0` in the HA history request;
- explicit `sampled_at` freshness;
- source-specific cadence and `last_reported` rules;
- bounded `held_estimate` instead of unconditional negative rejection;
- no silent large-negative-to-zero conversion;
- Envoy input gate independent of MPC output freshness;
- one bounded retry;
- structured load-quality observability;
- direct whole-home load as the preferred future source.
