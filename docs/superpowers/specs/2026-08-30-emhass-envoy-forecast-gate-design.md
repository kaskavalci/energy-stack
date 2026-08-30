# EMHASS Envoy forecast-aware PV gate

**Date:** 2026-08-30

**Author:** Composer

**Status:** Approved design (revised after operator review)

**Scope:** Home Assistant templates for EMHASS PV / house-load availability and
C1 PV fail detection — require a fresh Envoy only when PV is expected to be
material.

## Problem

After sunrise, `sensor.emhass_pv_power_w` and `sensor.emhass_house_load_power_w`
require Envoy `last_updated` within 20 minutes. At night, quiet Envoy is allowed
and PV is treated as 0 W.

Envoy often stays at 0 kW with no `last_updated` advance until ~10–30 minutes
after sunrise. On low-PV mornings the EMHASS forecast is also ~0 W, so silence
is expected. Templates still go `unavailable`, the one-minute load sample stops,
and the next MPC aborts fail-closed (`load history … stale`).

Phone path that operators saw on 2026-08-30 was primarily **D3** (SOC floor),
but the same dawn pattern aborted MPC at 07:00 and logged B6. Recurring
unavailable windows appear on most recent dawns in HA recorder history.

## Evidence (recorder ~2026-08-23–30)

- Almost all Envoy age &gt;20 min / PV-unavailable gaps are dawn, not midday.
- Active `sensor.p_pv_forecast` is often 0 W during the gap; on clear dawns it
  can jump to ~100 W at the :45 plan step **before** Envoy wakes (~06:52–07:00).
- Threshold sweep on sun-up stale samples: **50–100 W** still left false flags
  on Aug 25–26; **≥150 W** left **zero** false dawn flags in this window.
- When forecast ≥150 W, Envoy was never &lt;5 W and never age &gt;20 min in the set.

## Goals

- Keep house-load sample **available** (PV = 0) when expected PV is not material.
- Still mark PV / C1 failed when Envoy is bad or stale **and** PV should be
  material.
- **One shared source of truth** for the gate (single binary sensor consumed by
  PV, house-load, and C1).
- Preserve P1 net and Sigen battery freshness checks for house load.
- Fail **open** on missing sun / forecast inputs so templates do not recreate
  load-sample unavailability.

## Non-goals

- Substituting forecast watts into the house-load formula (when Envoy is skipped,
  PV contribution stays **0 W**, same as night).
- Changing MPC cadence, load-history gate, energy-monitor B6 timing, or D3.
- Changing Envoy integration polling.
- Winter retune of thresholds (may revisit later with more recorder data).
- Cross-file YAML anchors as the shared gate (fragile; rejected).

## Decision

**Envoy is required** only when all of the following are true:

1. `sun.sun` is `above_horizon`
2. Dawn grace elapsed (**guarded** `next_rising`; see below)
3. `states('sensor.p_pv_forecast') is is_number` **and** value `≥ 150` (W)
4. `binary_sensor.emhass_mpc_fresh` is `on`  
   (plan is from a successful MPC freshness contract — **not**
   `p_pv_forecast.last_updated`, which five-minute cached `publish-data` can
   refresh without a new optim; see
   [2026-07-19 SOC recovery freshness](2026-07-19-emhass-soc-recovery-freshness-design.md))

If any of sun-up, grace, finite material forecast, or MPC-fresh fails → Envoy
is **not** required (fail open).

**If Envoy is not required:**

- PV state = **0 W** when Envoy is bad/stale; otherwise use Envoy×1000 as today
- PV and house-load **availability** do not fail solely on Envoy
- House load still requires fresh P1 net + Sigen battery power (20 min)
- C1 (`binary_sensor.emhass_fail_pv_sensor`) = **off**

**If Envoy is required:** keep today’s Envoy bad / `last_updated` &gt; 20 min
rules for PV availability, house-load availability, and C1.

**Constants (named in templates / tests):**

| Name | Value |
|------|-------|
| `ENVOY_STALE_S` | 20 × 60 (= 1200) |
| `ENVOY_DAWN_GRACE_S` | 30 × 60 (= 1800) |
| `ENVOY_MATERIAL_FORECAST_W` | 150 |

## Architecture

### Shared entity (source of truth)

Create **`binary_sensor.emhass_envoy_required`** in
`home-assistant/ha_emhass_sensors.yaml` (or monitoring package if entity
ownership fits better — prefer **sensors** package next to PV/house-load).

| Field | Rule |
|-------|------|
| `state` | `on` iff the Decision gate is true; else `off` |
| `gate_reason` attr | Exactly one value from the priority table below (first match wins) |

**`sensor.p_pv_forecast` state** is the **current 30-minute slot PV forecast in
watts** (EMHASS publish-data). Do not use forecast attribute rows for the gate;
do not treat `last_updated` as freshness (use `binary_sensor.emhass_mpc_fresh`).

**`gate_reason` priority (first match wins, exactly one value):**

| Priority | Value | Condition |
|----------|-------|-----------|
| 1 | `skip_night` | `sun.sun` is not `above_horizon` |
| 2 | `skip_sunrise_unavailable` | sun up and `rising_ts <= 0` |
| 3 | `skip_dawn_grace` | sun up, `rising_ts > 0`, grace not elapsed |
| 4 | `skip_forecast_unavailable` | `p_pv_forecast` is not `is_number` |
| 5 | `skip_low_forecast` | `is_number` and value `< 150` |
| 6 | `skip_mpc_stale` | forecast material and `emhass_mpc_fresh` is not `on` |
| 7 | `require_fresh` | all gate conditions true (`binary_sensor` state `on`) |

Consumers (read `is_state('binary_sensor.emhass_envoy_required', 'on')` only;
do **not** re-implement the gate):

- `sensor.emhass_pv_power_w` — state + availability
- `sensor.emhass_house_load_power_w` — availability (and PV term in state when
  Envoy skipped → 0)
- `binary_sensor.emhass_fail_pv_sensor` (C1) — **replace** `elevation <= 8`
  heuristic; C1 `on` only when Envoy required **and** Envoy bad/stale

### Forecast check

Use Home Assistant’s `is_number` test so NaN / infinity are rejected:

```jinja
{% set fc = states('sensor.p_pv_forecast') %}
{% set forecast_material =
     fc is is_number
     and (fc | float(0)) >= ENVOY_MATERIAL_FORECAST_W %}
```

Material for the gate also requires `is_state('binary_sensor.emhass_mpc_fresh', 'on')`.
A stale numeric forecast with `emhass_mpc_fresh=off` → `gate_reason=skip_mpc_stale`,
Envoy not required.

### Sunrise grace measurement (fail open)

Do not use recorder history. `as_timestamp()` without a default raises on
invalid / missing values and can break dependent templates
([`as_timestamp` docs](https://www.home-assistant.io/template-functions/as_timestamp/)).

```jinja
{% set rising_ts = as_timestamp(
     state_attr('sun.sun', 'next_rising'),
     default=0
   ) | float(0) %}
{% set dawn_grace_elapsed =
     rising_ts > 0
     and (as_timestamp(now()) - (rising_ts - 86400))
         > ENVOY_DAWN_GRACE_S %}
```

If `next_rising` is missing (`rising_ts == 0`) while sun is above horizon →
`dawn_grace_elapsed` is false → Envoy **not** required
(`gate_reason=skip_sunrise_unavailable`). Prefer load continuity over a hard
template failure during HA startup.

When below horizon, `envoy_required` is false (`skip_night`) regardless of grace.

**DST:** `next_rising` from the Sun integration is an aware UTC datetime
([Sun integration](https://www.home-assistant.io/integrations/sun/)). Converting
with `as_timestamp` then subtracting **86400** seconds is elapsed-time based,
not wall-clock local midnight. No separate DST skew correction is required for
this grace.

### Envoy stale check (unchanged threshold)

When `emhass_envoy_required` is `on`:

```text
envoy_bad OR (now - envoy.last_updated) > ENVOY_STALE_S   # 1200 s
```

Boundary: age **1200** s still OK; **1201** s stale.

### Alerts

- C1 detection / 30-minute phone escalation unchanged once
  `binary_sensor.emhass_fail_pv_sensor` turns `on`.
- No new phone alert IDs. B6 remains monitor-side for load-history aborts.
- `emhass_envoy_required` is diagnostic (Logbook / attributes); not a phone path.

### Tests

Replace structural-only assertions in
`scripts/tests/test_ha_emhass_sensors.py` (e.g. the
`sun_up or raw_pv_w > 50` string check) with **template behavior tests** that
evaluate the gate (and consumers) under controlled inputs. Keep render smoke
coverage. Target: existing suite green (baseline **68/68**) plus new cases.

**Exact boundaries** (grace and Envoy stale both use strict `>`):

| Input | Expect |
|-------|--------|
| Seconds since last rising **1799** | grace not elapsed |
| **1800** | grace not elapsed |
| **1801** | grace elapsed |
| Forecast **149** W | not material |
| **150** / **151** W | material (`>= 150`, `is_number`) |
| Envoy age **1200** s | not stale |
| **1201** s | stale when required |

**Scenarios:**

| Case | Expected |
|------|----------|
| Night | `envoy_required=off`, `skip_night`; PV 0 if Envoy quiet; available |
| Sun up, &lt; grace, any forecast | `skip_dawn_grace` |
| Sun up, grace ok, forecast &lt;150 or not `is_number` | `skip_low_forecast` / `skip_forecast_unavailable` |
| Sun up, grace ok, forecast ≥150, `emhass_mpc_fresh=off` | `skip_mpc_stale`; not required |
| Sun up, grace ok, forecast ≥150, MPC fresh, Envoy stale | `require_fresh`; PV/house-load unavailable; C1 on |
| `next_rising` None / invalid, sun above horizon | `skip_sunrise_unavailable`; fail open |
| HA restart near sunrise (missing attrs briefly) | fail open; load sample stays available if P1/batt ok |
| DST spring-forward and fall-back calendar days | grace still uses UTC unix delta; no false hard fail |
| Midday high forecast, MPC fresh, Envoy fresh | available as today |

Consumers must assert they reference `binary_sensor.emhass_envoy_required` and
do not duplicate `next_rising` / forecast threshold logic.

### Deploy

- `./scripts/render-ha-packages.sh` then `./scripts/deploy-home-assistant.sh`
- Energy host / cron unchanged

## Risks

- **Forecast jump after grace with MPC fresh:** if forecast ≥150 W and Envoy
  still silent, availability fails (intended when expectation is material).
- **MPC not fresh:** Envoy not required even with a large numeric forecast —
  prefers continuity; midday Envoy death while plan is `plan_stale` will not
  raise C1 until MPC is fresh again (aligned with existing freshness contract).
- **Winter:** 150 W may be high relative to peak; revisit with more history.

## Acceptance

1. On a low-forecast or MPC-stale dawn, house-load sample keeps updating through
   sunrise+30 min with quiet Envoy; natural MPC does not abort for load-sample
   staleness solely from Envoy silence.
2. With `emhass_envoy_required=on` and Envoy `last_updated` aged past 20 min,
   PV/house-load go unavailable and C1 goes on.
3. Missing `next_rising` does not throw in templates; gate fails open.
4. Template behavior tests cover boundaries and scenarios above; full targeted
   HA package test suite passes; deploy notes in memory bank after live verify.
