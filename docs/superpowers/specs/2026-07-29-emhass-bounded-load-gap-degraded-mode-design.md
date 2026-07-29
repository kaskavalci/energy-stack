# EMHASS bounded load-gap degraded mode

**Date:** 2026-07-29

**Status:** Approved design

**Scope:** MPC native-load history recovery, observability, alert deduplication,
and EMHASS upgrade compatibility

## Problem

The energy and Home Assistant hosts lost network connectivity on 2026-07-29.
Home Assistant could not see the MQTT-backed P1 and Sigenergy measurements for
about 105 minutes, leaving the `04:30` and `05:00` CEST half-hour bins absent
from `sensor.emhass_house_load_sample_w` recorder history.

Fresh measurements resumed, but the MPC remained fail-closed for roughly 16
hours. The current native-load gate requires every bin in the effective
32-step, 30-minute forecast horizon. A missing bin therefore blocks every run
until it leaves the sliding history window.

Sigenergy correctly returned to native maximum self-consumption, so the failure
was safe. The operational cost was avoidable trading downtime and repeated
secondary alerts after live measurements had recovered.

## Goals

- Resume MPC automatically after live load inputs recover from an outage of up
  to three hours.
- Preserve fail-closed behavior while P1, PV, or battery inputs are stale.
- Keep EMHASS's native `naive` load-forecast path; do not restore the retired
  runtime load-list, `alpha`, or `beta` path.
- Make degraded operation explicit and observable.
- Keep gaps longer than three hours and ambiguous gap geometry fail-closed.
- Prevent silent EMHASS upgrades and require an agent to prove that native
  interpolation behavior remains compatible before changing the pinned image.
- Reduce derivative alert noise without weakening actuator safety gates.

## Non-goals

- Do not write synthetic states into Home Assistant recorder history.
- Do not use Zonneplan statistics as a required runtime fallback.
- Do not redesign price acquisition or its current fail-closed policy.
- Do not change the 30-minute optimization step, the 32-step configured maximum
  horizon, the 12-slot price minimum, weather policy, or Sigenergy actuator
  clamps.
- Do not add a general-purpose load forecasting model or multi-day historical
  profile in this change.

## Decision

Keep the native EMHASS `naive` forecast and allow its configured linear
interpolation only for one bounded, fully bracketed interior history gap.

Every MPC load-history preflight classifies the effective history window as one
of three modes:

| Mode | Required behavior |
|------|-------------------|
| `healthy` | Every required 30-minute bin contains at least one finite, nonnegative sample. Run native MPC unchanged. |
| `degraded` | Exactly one interior run of one through six consecutive required bins is missing, valid bins immediately bracket it, and current composite load data is fresh. Run native MPC and record the degraded gap. |
| `unsafe` | Any degraded eligibility rule fails. Abort before optimization or publication and retain native Sigenergy self-consumption. |

The six-bin limit is an absolute three-hour limit within the effective
prediction horizon. It is not increased for a longer configured horizon or
reduced as a percentage when the effective horizon is shorter.

## Live-input recovery gate

Degraded MPC is allowed only after the newest finite
`sensor.emhass_house_load_sample_w` recorder sample is no more than five
minutes old.

That sampled sensor remains the canonical combined live-input gate. Its Home
Assistant availability already requires:

- numeric, non-stale P1 net power;
- numeric, non-stale Sigenergy battery power;
- numeric, non-stale Envoy PV while PV is required;
- the existing safe nighttime exception when the sun is down and PV is
  effectively zero.

The source-template freshness threshold remains 20 minutes. The sampled
history's stricter five-minute newest-sample requirement proves that the
combined sensor has resumed producing data. The implementation must retain
tests that bind this availability behavior to the P1, PV, and battery sources;
it must not silently reduce the recovery gate to P1 alone.

## Gap eligibility

History remains based on timezone-aware UTC instants and aligned 30-minute UTC
bins. Local time is used only in diagnostics.

A degraded gap is eligible only when all of the following are true:

1. The newest finite, nonnegative sample passes the five-minute freshness
   check.
2. All missing required bins form exactly one consecutive sequence.
3. The sequence contains between one and six bins inclusive.
4. The sequence is interior to the required window.
5. The required bin immediately before the sequence is covered.
6. The required bin immediately after the sequence is covered.
7. No accepted record is offset-less, future-dated, negative, or non-finite.

The validator rejects:

- seven or more consecutive missing bins;
- multiple separate gaps, even if they total six or fewer bins;
- a gap touching the oldest or newest required bin;
- missing bracketing bins;
- stale current data;
- malformed history or configuration;
- unsupported optimization steps.

Valid `0 W` measurements remain real data. `set_zero_min` remains false.

## Data flow

The normal MPC chain remains:

```text
existing validated price pipeline
  -> native-load history retrieval
  -> history classification
  -> weather validation
  -> native naive-mpc-optim
  -> publish-data
  -> successful-MPC marker
```

For each real MPC:

1. Run the existing price pipeline and resolve the effective horizon.
2. Fetch one extra preceding load-history bin as today.
3. Validate sample freshness and classify the required window.
4. Abort on `unsafe`.
5. On `healthy`, call native EMHASS exactly as today.
6. On `degraded`, log the gap and call the same native EMHASS path. Do not
   construct or submit `load_power_forecast`, `alpha`, or `beta`.
7. Publish and advance the successful-MPC marker only after optimization and
   publication both succeed.
8. Return automatically to `healthy` when the gap leaves the required window.

No synthetic records are persisted. Interpolation exists only in EMHASS's
in-memory preparation of the load series.

## Status and diagnostics

The MPC status in `last_enever_run.json` must expose enough data to distinguish
safe degraded operation from strict history:

```json
{
  "load_history": {
    "mode": "degraded",
    "required_bins": 32,
    "covered_bins": 26,
    "missing_bin_count": 6,
    "missing_bins": [
      "2026-07-29T02:30:00+00:00"
    ],
    "gap_start": "2026-07-29T02:30:00+00:00",
    "gap_end_exclusive": "2026-07-29T05:30:00+00:00",
    "gap_duration_minutes": 180,
    "interpolation_method": "emhass_native_linear",
    "newest_sample_at": "2026-07-29T05:49:00+00:00",
    "newest_sample_age_minutes": 1.0
  }
}
```

The example abbreviates `missing_bins`; the real status contains every missing
UTC bin. `gap_start` is the start of the first missing bin and
`gap_end_exclusive` is the end of the last missing bin, so their difference
equals `gap_duration_minutes`. Human-facing logs may additionally render local
timestamps.

Healthy successful runs record `mode=healthy`, zero missing bins, and no gap
range. Unsafe runs retain `failure_kind=load_history` with the rejected
geometry. A failure proven to originate in native load interpolation or the
resulting load forecast uses `failure_kind=load_interpolation`. Weather,
optimization, publication, and successful-marker failures retain their
existing causal classifications rather than being relabeled merely because
the run was degraded.

## Alert behavior

The monitor treats the gap signature as the incident identity. The signature
contains the failure kind plus the ordered missing UTC bins.

- Notify once when an eligible gap first enters `degraded` operation.
- Notify once when that incident returns to `healthy`.
- Do not repeat the same degraded notice on the normal two-hour cooldown.
- For an `unsafe` load-history incident, make the load-history notification the
  primary energy-monitor alert.
- Suppress derivative energy-monitor B3 and B5-cache pushes while the same
  load-history incident remains primary. An independently recorded weather
  refresh failure remains alertable.
- Reset incident deduplication only after a healthy load-history result or a
  genuinely different gap signature.

Home Assistant stale-plan and control gates remain unchanged. They continue to
restore or retain native self-consumption whenever there is no fresh successful
MPC plan.

## Why Zonneplan is not the fill source

The installed Zonneplan integration provides hourly grid-delivery and
grid-return external statistics. During the incident, the `04:00` and `05:00`
hourly provider rows were also absent.

Even when present, net grid energy cannot reconstruct gross house load behind
the meter. The current house-load equation also requires Envoy PV and
Sigenergy battery power:

```text
house load = grid net power + PV power - battery power
```

Zonneplan statistics may be used later for retrospective checks, but degraded
MPC must not wait for them or treat them as equivalent to house-load history.

## EMHASS image pinning

The Compose service currently tracks `ghcr.io/davidusb-geek/emhass:latest`.
This permits upstream behavior to change during an unrelated pull or deploy.

Implementation must replace `latest` with:

- a human-readable release tag; and
- the immutable image digest for the verified artifact.

A repository test must reject:

- `:latest`;
- an image without an explicit release tag;
- an image without `@sha256:...`.

Deployment continues to use the pinned image until an intentional, reviewed
upgrade changes both the release and digest.

## EMHASS upgrade compatibility contract

Add a repeatable, secret-free compatibility command that accepts a candidate
EMHASS image reference and runs a probe inside that image. The probe uses
synthetic, nonzero, timezone-aware data and exercises the installed native
resampling, interpolation, and `naive` forecast implementation.

The candidate passes only when:

1. Complete history produces the expected finite forecast length.
2. A six-bin interior gap is linearly interpolated.
3. Every interpolated test value is finite.
4. Interpolated values stay within the two nonzero bracketing values.
5. No missing value reaches a generic NaN-to-zero fill.
6. The resulting native naive forecast has the expected ordering and horizon.
7. The probe leaves the host, live HA recorder, and live EMHASS data untouched.

Any internal API change that prevents the probe from exercising these stages
is a failed compatibility check until the probe and this design are reviewed.
The agent must not infer compatibility from release notes alone.

Repository agent instructions must state:

1. Do not change the EMHASS tag or digest before running the compatibility
   command against the candidate image.
2. Run the cron policy tests and the Compose pin test in the same upgrade.
3. Record the candidate tag, digest, command, and passing result in a committed
   upgrade-verification note.
4. Deploy only after the committed verification is reviewed.

This check is mandatory for every EMHASS upgrade, including changes made while
fixing unrelated energy-stack behavior.

## Test matrix

### Validator unit tests

- complete 32-bin history is `healthy`;
- each interior gap length from one through six is `degraded`;
- a seven-bin gap is `unsafe`;
- two separate gaps are `unsafe`;
- oldest-edge and newest-edge gaps are `unsafe`;
- missing preceding or following bracket is `unsafe`;
- stale newest sample is `unsafe`;
- negative, non-finite, future, and offset-less records do not cover a bin;
- genuine zero values remain valid;
- exact `:00` and `:30` recorder-visibility behavior remains valid;
- UTC spring gap and autumn fold behavior remains valid;
- effective horizons below 32 apply the same absolute six-bin policy.

### Main-chain tests

- healthy history calls the existing native MPC payload;
- degraded history calls the identical native payload without a load list,
  `alpha`, or `beta`;
- unsafe history calls no EMHASS optimization or finalizer;
- degraded optimization plus publication advances the successful marker;
- an interpolation-attributable failure does not publish or advance freshness;
- unrelated failure stages retain their existing classifications;
- dry-run remains non-mutating and reports the classification.

### Source freshness tests

- fresh P1, battery, and required PV allow the sampled sensor to resume;
- stale P1 blocks availability;
- stale battery blocks availability;
- stale daylight PV blocks availability;
- nighttime zero-PV behavior remains available;
- a new sampled state is no more than five minutes old before degraded MPC.

### Monitor tests

- the first degraded gap sends one notice;
- repeated runs with the same signature send no duplicate;
- return to healthy sends one recovery notice;
- a different gap creates a new incident;
- unsafe load history suppresses derivative B3/B5-cache noise;
- an independent weather refresh failure remains alertable.

### Upgrade tests

- Compose rejects `latest` and unpinned image references;
- the pinned current image passes the compatibility probe;
- a deliberately incompatible fixture fails the probe;
- the probe does not require site secrets or network access to Home Assistant.

## Rollout and rollback

1. Run all local unit, monitor, render, Compose-pin, and compatibility checks.
2. Deploy the pinned current EMHASS image and cron/monitor changes using the
   normal energy-stack deploy script.
3. With naturally complete live history, verify untouched scheduled `:00` and
   `:30` runs remain `healthy`, `Optimal`, published, and fresh.
4. Exercise degraded and unsafe geometry only with isolated synthetic fixtures.
   Do not delete or modify live recorder data to manufacture an outage.
5. Confirm incident-deduplicated notices in a non-delivery test path or mocked
   notification test.
6. Update the memory bank with the pinned image, verification result, deploy
   evidence, and rollback reference.

Rollback restores the previous pinned image and strict history validator.
Because the design never mutates HA recorder history, no history repair is
required. Before any live rollback that can affect actuator control, use the
existing master-off/native-control verification procedure.

## Price pipeline

Price behavior is explicitly unchanged by this design. The current MPC fetches
the rolling Zonneplan forecast and rewrites the CSVs on each run; a fetch
failure aborts MPC rather than using a stale CSV. Removing the duplicate fetch
or adopting a coverage-aware daily cache requires a separate design because it
changes an existing fail-closed invariant.
