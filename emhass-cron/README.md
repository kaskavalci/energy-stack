# emhass-cron

Cron container on the energy host: Zonneplan prices from Home Assistant, EMHASS MPC, Open-Meteo cache.

Requires `config/site.env` (see [docs/SETUP.md](../docs/SETUP.md)) and:

- `emhass/secrets_emhass.yaml` with `hass_url` and `long_lived_token` (mounted at `/secrets/secrets_emhass.yaml`).
- `emhass/share/config.json` from `config.json.example`.

Sensor entity overrides via `site.env`: `ZONNEPLAN_PRICE_SENSOR`, `EMHASS_SOC_SENSOR`, `EMHASS_SOC_MIN_SENSOR`, `EMHASS_SOC_MAX_SENSOR`.

## MPC success marker

The MPC chain is:

```text
HA prices -> strict native-load history validation -> weather validation ->
naive-mpc-optim -> publish-data -> successful-MPC marker
```

The validation reads `sensor_power_load_no_var_loads` and
`optimization_time_step` from the live `/share/config.json`. For the effective
horizon it queries one extra preceding half-hour. Once the newest finite
sample passes the five-minute freshness limit, its floored UTC bin anchors the
required consecutive bins. At exactly `:00` or `:30`, the window therefore
stays on the last completed bin until the first sample in the new bin arrives,
then advances.

Load history is classified per effective horizon:

- `healthy`: all required half-hour bins are covered;
- `degraded`: exactly one fully bracketed interior gap contains at most six
  consecutive bins and the newest composite sample is no older than five
  minutes;
- `unsafe`: longer, multiple, edge, unbracketed, stale, or malformed history.

Healthy and degraded runs use the same native `naive-mpc-optim` payload. Cron
does not send `load_power_forecast`, `alpha`, or `beta`; EMHASS performs its
configured in-memory linear interpolation. Unsafe history aborts before
optimization or publication. `last_enever_run.json` records the mode, missing
UTC bins, brackets, duration, method, and newest sample.

Query bounds, timestamps, freshness, flooring, and coverage are
evaluated as UTC instants so daylight-saving folds and gaps contain exactly
the real elapsed half-hours; local time is display only. Offset-less HA
timestamps cannot satisfy the gate.

The successful marker changes only after optimization and publish both
succeed. The cron then writes the same completion timestamp to:

- `emhass/share/last_enever_run.json` (`mpc.plan_generated_at`)
- Home Assistant `input_datetime.emhass_last_successful_mpc`

Home Assistant uses the helper—not `sensor.p_batt_forecast.last_updated`—for
the 40-minute actuator freshness gate. The five-minute publish-only automation
does not update it. If the HA helper update fails after three attempts, the cron
records `mpc.ok=false` with `failure_kind=status_publish`.

If load-history config, retrieval, freshness, or bin coverage fails, cron
retries once after a bounded 90-second delay (`LOAD_HISTORY_MAX_ATTEMPTS = 2`,
`LOAD_HISTORY_RETRY_DELAY_S = 90.0`) to allow Envoy update lag or temporary HA
recorder delays to clear. History is requested with `significant_changes_only=0`
and omits `no_attributes` so Home Assistant returns intermediate unchanged states
with their `sampled_at` and `load_quality` attributes. Samples with
`held_estimate` (holding previous valid load during negative balance from Envoy
lag) are valid up to a 10-minute hold limit (`MAX_HELD_LOAD_AGE_S = 600`). If all
retry attempts fail, cron records `mpc.ok=false` with `failure_kind=load_history`,
returns nonzero, and makes no EMHASS optimization, publish, or
successful-finalization call.
The live config and shared HA credential file are strictly preflighted before
any HA request. Normal MPC reads credentials once and reuses the same immutable
URL/token snapshot for SOC, prices, history, and the successful-MPC marker;
preflight performs no network probe. HA state responses require an object root
and object `attributes`; malformed optional-SOC responses remain optional and
malformed price responses take the price-abort path. Expected filesystem,
request, protocol, decoding, JSON, and response-shape failures enter their
operation's narrow failure path; unexpected programming errors still
propagate.

## Planned profit lock

After a successful MPC marker only (not dry-run, write-only, or failed runs):

- Reuses the same Zonneplan price snapshot that wrote the CSV files.
- Reads EMHASS `/data/opt_res_latest.csv` via `docker exec` (stdlib reader in `planned_profit.py`; no pandas in cron).
- Locks covered-slot `cost_profit` for **tomorrow** (`Europe/Amsterdam`) into `/share/daily_planned_profit.json` (write-once per date, atomic replace, file lock, 14-day prune).
- Publishes today's locked record to `input_text.emhass_planned_profit_payload` with `input_text.set_value` (compact JSON ≤255 chars).
- `sensor.emhass_planned_profit_today` is a HA template sensor derived from that helper.
- Metric is partial covered slots (`partial: true`), not a full calendar day.
- Auxiliary lock/read/publish failure is recorded in `last_enever_run.json` under `planned_profit`; it does **not** set `mpc.ok=false`.
- A successful run always writes `planned_profit.ok=true`, including no-op runs (lock already exists, or no tomorrow slots). This clears a prior auxiliary failure.
- If `daily_planned_profit.json` exists but is malformed, cron does **not** replace it. It records `planned_profit.ok=false` and leaves the file for the 22:00 invalid-lock alert.
- After midnight on the target date, `sensor.emhass_planned_profit_today` stays `unknown` until the first successful MPC republishes today's record (normally the next `:00`/`:30` slot).
- `energy-monitor` sends one `notify.notify` after 22:00 local if tomorrow's lock is still missing.

Design: [docs/superpowers/specs/2026-08-16-emhass-daily-planned-profit-design.md](../docs/superpowers/specs/2026-08-16-emhass-daily-planned-profit-design.md).
