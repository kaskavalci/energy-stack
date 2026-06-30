# emhass-cron

Cron container on the energy host: Zonneplan prices from Home Assistant, EMHASS MPC, Open-Meteo cache.

Requires `config/site.env` (see [docs/SETUP.md](../docs/SETUP.md)) and:

- `emhass/secrets_emhass.yaml` with `hass_url` and `long_lived_token` (mounted at `/secrets/secrets_emhass.yaml`).
- `emhass/share/config.json` from `config.json.example`.

Sensor entity overrides via `site.env`: `ZONNEPLAN_PRICE_SENSOR`, `EMHASS_SOC_SENSOR`, `EMHASS_SOC_MIN_SENSOR`, `EMHASS_SOC_MAX_SENSOR`.
