# Open-Meteo proactive weather cache refresh

**Date:** 2026-08-01

**Author:** Auto

**Status:** Approved design

**Scope:** Soft early refresh of Open-Meteo JSON/pkl on the energy host; raise
hard stale/B5 age limit; shared stale helpers for cron and monitor

## Problem

`weather_cache_stale_reason` treats Open-Meteo JSON mtime age `>= open_meteo_cache_max_age`
(currently 120 min) as stale. MPC only clears and refetches when that hard gate
fires. `energy-monitor` uses the same hard gate every 10 minutes for B5.

In practice the JSON is rewritten about every two hours. At the half-hour MPC
boundary, age is often still slightly under 120, so cron logs cache OK and skips
clear. Monitor then sees 130–150 min for two ticks and pushes B5 (“Open-Meteo
JSON is 140 min old”), while the next MPC refreshes and succeeds. The alerts are
boundary noise, not an Open-Meteo outage.

## Goals

- Rewrite Open-Meteo cache **earlier on purpose** (fresher JSON/pkl).
- Keep a **hard** age limit for B5 and post-refresh MPC failure.
- Soft refresh threshold derived from config (`max_age` and step), not a second
  operator knobs file.
- Preserve shared stale logic in `lib/weather_cache_stale.py` for cron + monitor.
- Keep `max_age > optimization_time_step` (and soft age `> step`).

## Non-goals

- Monitor heuristics that suppress B5 when the last MPC/weather run succeeded.
- Changing MPC cadence (30 min), prediction horizon, price CSV policy, or
  Open-Meteo as the weather method.
- Changing the EMHASS image pin / floating `:latest` policy in this work.
- Adding a second config key for soft age (derive it in code).

## Decision

Use two thresholds:

| Role | Rule | With `max_age=150`, `step=30` |
|------|------|------------------------------|
| Soft refresh | JSON age ≥ `max_age − 2×optimization_time_step` | **90 min** → clear + `weather-forecast-cache` |
| Hard stale | JSON age ≥ `open_meteo_cache_max_age` | **150 min** → B5 + post-refresh failure |

Raise `open_meteo_cache_max_age` from 120 to **150** in example/live config and
docs/invariants.

Approach: soft gate in `ensure_weather_forecast_cache` / `refresh-weather` only;
monitor B5 stays on the hard gate.

## Architecture

### Shared library (`lib/weather_cache_stale.py`)

- Keep `weather_cache_stale_reason()` as the **hard** gate: pkl coverage bounds
  unchanged; JSON age uses `>= open_meteo_cache_max_age` (150 after config
  change).
- Add `weather_cache_refresh_reason()` (name may vary): same non-age checks as
  hard, plus soft JSON age ≥ `max_age − 2×step`. Message should distinguish soft
  vs hard (e.g. age, refresh-at, hard limit).
- Soft age is derived in code from `read_open_meteo_cache_max_age_min` and
  `read_optimization_time_step_min`.
- Update `DEFAULT_OPEN_METEO_CACHE_MAX_AGE_MIN` from 120 to **150** so a missing
  config key matches the new hard limit.
- Extend validation so misconfig is visible: existing `max_age > step`; soft
  threshold must also be `> step`. Do not silently paper over bad config.

### Cron (`emhass-cron/fetch_prices_emhass.py`)

- `ensure_weather_forecast_cache`: decide **clear** using soft refresh reason;
  always call `weather-forecast-cache` as today; **post-check / retry / raise**
  using hard stale only. Crossing soft after a no-op must not fail MPC.
- `--action refresh-weather`: skip EMHASS only when soft reason is None (daily
  05:05 refreshes from 90+, not only at 150).

### Monitor (`monitor/health_notify.py`)

- B5 continues to call hard `weather_cache_stale_reason` only. No soft alerts.

### Config / docs

- `emhass/share/config.json.example`: `open_meteo_cache_max_age: 150`.
- Live host `config.json` updated at deploy time (operator/deploy path; do not
  commit secrets or live share snapshots).
- Update AGENTS.md / skill / techContext invariants that hard-code 120 for this
  key to 150, and document soft = `max_age − 2×step`.

## Error handling

- Soft-triggered refresh that still leaves age `< hard max_age`: log as needed;
  MPC continues.
- Hard stale after clear + `weather-forecast-cache` (+ one retry): existing
  `RuntimeError`, `weather.ok=false`, abort MPC; existing B2/B4/B5 interactions
  unchanged.
- Config validation failures: log/error via extended
  `validate_cache_max_age_config` (or adjacent helper); do not invent a silent
  default that hides bad `max_age` / step pairs.

## Testing

- Unit tests on shared lib: soft triggers at `max_age − 2×step`, not before;
  hard at `max_age`; soft message distinct from hard.
- Cron wiring: soft → clear; post-validate uses hard.
- Example config asserts 150; `max_age > step` (and soft `> step`) still pass.
- No requirement to change HA packages for this work.

## Success criteria

- Under normal operation, Open-Meteo JSON is rewritten around the soft age
  (~90 min), not only when hard age is crossed.
- B5 “JSON is N min old” stops firing from the old 120/30/10 boundary race
  unless age truly reaches the hard limit (refresh failed or skipped long
  enough).
- MPC still fail-closes only on hard cache invalidity after retry.

## Implementation notes

- Minimal diff: prefer one new helper + call-site swaps in ensure/refresh-weather;
  avoid rewriting pkl bound logic.
- Preserve clear-before-`weather-forecast-cache` on refresh (EMHASS no-op while
  files exist).
- Memory bank / skill docs updated when the change ships.
