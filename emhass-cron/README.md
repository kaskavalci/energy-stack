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
then advances. Every required bin needs a finite, non-negative sample; a valid
`0 W` sample passes. Validation submits no load forecast and has no retry or
fallback. Query bounds, timestamps, freshness, flooring, and coverage are
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
records `mpc.ok=false` with `failure_kind=load_history`, returns nonzero, and
makes no EMHASS optimization, publish, or successful-finalization call.
The live config and shared HA credential file are strictly preflighted before
any HA request. Normal MPC reads credentials once and reuses the same immutable
URL/token snapshot for SOC, prices, history, and the successful-MPC marker;
preflight performs no network probe. HA state responses require an object root
and object `attributes`; malformed optional-SOC responses remain optional and
malformed price responses take the price-abort path. Expected filesystem,
request, protocol, decoding, JSON, and response-shape failures enter their
operation's narrow failure path; unexpected programming errors still
propagate.
