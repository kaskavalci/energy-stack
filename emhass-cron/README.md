# emhass-cron

Cron container on the energy host: Zonneplan prices from Home Assistant, EMHASS MPC, Open-Meteo cache.

Requires `config/site.env` (see [docs/SETUP.md](../docs/SETUP.md)) and:

- `emhass/secrets_emhass.yaml` with `hass_url` and `long_lived_token` (mounted at `/secrets/secrets_emhass.yaml`).
- `emhass/share/config.json` from `config.json.example`.

Sensor entity overrides via `site.env`: `ZONNEPLAN_PRICE_SENSOR`, `EMHASS_SOC_SENSOR`, `EMHASS_SOC_MIN_SENSOR`, `EMHASS_SOC_MAX_SENSOR`.

## MPC success marker

After prices, weather validation, `naive-mpc-optim`, and `publish-data` all
succeed, the cron writes the same completion timestamp to:

- `emhass/share/last_enever_run.json` (`mpc.plan_generated_at`)
- Home Assistant `input_datetime.emhass_last_successful_mpc`

Home Assistant uses the helper—not `sensor.p_batt_forecast.last_updated`—for
the 40-minute actuator freshness gate. The five-minute publish-only automation
does not update it. If the HA helper update fails after three attempts, the cron
records `mpc.ok=false` with `failure_kind=status_publish`.
