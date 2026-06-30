# Tech Context

## Stack

| Layer | Stack |
|-------|-------|
| Energy automation | Python 3 (stdlib in cron/monitor containers) |
| Home Assistant | YAML packages, templates, REST, automations |
| Orchestration | Docker Compose on energy host (`ENERGY_REMOTE_HOST`); supercronic in custom images |

## Containers (`docker-compose.yml`)

| Service | Role |
|---------|------|
| `emhass` | LP optimizer, Open-Meteo, publish-data `:5000` |
| `emhass-cron` | `fetch_prices_emhass.py` |
| `energy-monitor` | `health_notify.py` every 10 min |
| `mosquitto`, `mqtt4dsmr`, `sigenergy2mqtt` | MQTT bridges |

## EMHASS invariants

- `optimization_time_step`: **30** min; `prediction_horizon`: **32** max
- Price methods: **csv**; weather: **open-meteo**
- `open_meteo_cache_max_age`: **120** min — must stay **>** MPC cadence
- Price fetch failure → **abort MPC**
- Weather retry → `clear_weather_cache_files()` before `weather-forecast-cache`
- Shared stale logic: `lib/weather_cache_stale.py`

## Deploy

- **Energy host:** `./scripts/deploy-energy-stack.sh` → `ENERGY_REMOTE_HOST`:`ENERGY_REMOTE_DIR`
- **Home Assistant:** `./scripts/deploy-home-assistant.sh` → `HA_SSH_HOST` (additive merge via `scripts/lib/ha_config_merge.py`)
- HA merge requires `ruamel.yaml` (`scripts/lib/requirements.txt`)

Hosts and paths from `config/site.env` — see [docs/SETUP.md](../docs/SETUP.md).

## Secrets

Never commit `config/site.env`, `emhass/secrets_emhass.yaml`, `emhass/share/config.json`, live CSV snapshots.

HA packages are rendered from `config/site.env` at deploy (`scripts/render-ha-packages.sh`).

**Home Assistant `/config/secrets.yaml` (one-time on HA host):** `emhass_publish_url` — REST URL for `rest_command.emhass_publish_data` in `ha_emhass.yaml` (`http://<energy-host>:5000/action/publish-data`). Not in this repo; `deploy-home-assistant.sh` greps live HA secrets before deploy. Energy host IP comes from `ENERGY_REMOTE_HOST` / `emhass_publish_url` in HA secrets.

**Energy host `emhass/secrets_emhass.yaml`:** `hass_url` + `long_lived_token` for cron/monitor → HA API (separate from HA `secrets.yaml`).

## Anti-patterns

| Don't | Why |
|-------|-----|
| `open_meteo_cache_max_age` = 30 | False B5/B4 every MPC boundary |
| Rely on `weather-forecast-cache` alone | No-op while cache files exist |
| Stale-CSV fallback on price fail | Violates Policy A |
| Deploy HA packages before P1 aliases | EMHASS templates unavailable |
