# Tech Context

## Stack

| Layer | Stack |
|-------|-------|
| Energy automation | Python 3 (stdlib in cron/monitor containers) |
| Home Assistant | YAML packages, templates, REST, automations on **HA Container** (Docker Compose, Odroid `.75`) |
| Orchestration | Docker Compose on energy host (`ENERGY_REMOTE_HOST`); supercronic in custom images |

## Containers (`docker-compose.yml`)

| Service | Role |
|---------|------|
| `emhass` | LP optimizer, Open-Meteo, publish-data `:5000` |
| `emhass-cron` | `fetch_prices_emhass.py` |
| `energy-monitor` | `health_notify.py` every 10 min |
| `mosquitto`, `mqtt4dsmr`, `sigenergy2mqtt` | MQTT bridges (`sigenergy2mqtt` pinned tag+digest like EMHASS; MQTT broker via env for early persistence) |

HA itself runs on the **HA host** via [`home-assistant/docker-compose.yml`](../home-assistant/docker-compose.yml) (`ghcr.io/home-assistant/home-assistant:stable`, `network_mode: host`), not on the energy host.

## EMHASS invariants

- `optimization_time_step`: **30** min; `prediction_horizon`: **32** max
- Price methods: **csv**; weather: **open-meteo**
- `open_meteo_cache_max_age`: **120** min — must stay **>** MPC cadence
- Price fetch failure → **abort MPC**
- Native load history is healthy with complete coverage and degraded only for
  one fully bracketed interior gap of at most six consecutive 30-minute bins
  after the composite P1/PV/battery-derived sample is fresh (<=5 min). Longer,
  multiple, edge, unbracketed, or stale gaps fail closed.
- Degraded mode still uses native `naive`; cron never sends a load list,
  `alpha`, or `beta`, and never writes synthetic HA recorder states.
- EMHASS is pinned by tag+digest. Every upgrade must pass
  `scripts/verify-emhass-load-gap-contract.sh` and record evidence before the
  Compose image changes.
- Weather retry → `clear_weather_cache_files()` before `weather-forecast-cache`
- Shared stale logic: `lib/weather_cache_stale.py`
- EMHASS site location comes from validated `LAT`, `LON`, `ALT`, and
  `TIME_ZONE` in gitignored `config/site.env`; never rely on image defaults.
- `TZ` and `TIME_ZONE` must match.
- The trading dashboard uses native hourly statistics plus a today-only PV
  forecast table. Import price forecast uses **ApexCharts** wrapped in
  **config-template-card**, fed from Zonneplan
  `sensor.zonneplan_current_electricity_tariff` `forecast` (full day-ahead;
  `electricity_price / 1e7`). Span tracks the last hourly slot (`extend_to:
  false`). This is intentionally **not** bounded by EMHASS
  `prediction_horizon` / `unit_load_cost_forecasts`. Install frontend cards via
  `scripts/install-ha-apexcharts.sh` (also run from `deploy-home-assistant.sh`).
  Avoid additional custom chart cards unless needed.
- Master-off or control-failure fallback selects Sigenergy native
  `Maximum Self-consumption (Default)` automatically; operators do not select it
  during healthy MPC trading.
- A healthy true idle/deadband command also selects native maximum
  self-consumption at any SOC. The SOC-floor cutout applies only to explicit
  command-discharge modes; charge and recovery behavior is unchanged.
- Every Sigenergy charge/discharge limit is clamped to the MQTT number entity's
  advertised `max`; native restoration is successful only after mode and both
  limits are confirmed, then `emhass_last_limit_write` advances.
- Home Assistant start or a Sigenergy mode reconnect while the master is off
  reruns native restoration even when the returned mode is already native.

## Deploy

- **Energy host:** `./scripts/deploy-energy-stack.sh` → `ENERGY_REMOTE_HOST`:`ENERGY_REMOTE_DIR`
- **Home Assistant:** `./scripts/deploy-home-assistant.sh` → `HA_SSH_HOST` (additive merge via `scripts/lib/ha_config_merge.py`)
  - Config path: `${HA_CONFIG_DIR}` (preferred `/opt/homeassistant/config`)
  - Check: `docker exec ${HA_CONTAINER_NAME} hass --script check_config`
  - Restart: `docker compose -f … restart` (deploy user in `docker` group; no sudo)
- HA merge requires `ruamel.yaml` (`scripts/lib/requirements.txt`)

Hosts and paths from `config/site.env` — see [docs/SETUP.md](../docs/SETUP.md).
Migration: [docs/ha-odroid-container-migration.md](../docs/ha-odroid-container-migration.md).

## Secrets

Never commit `config/site.env`, `emhass/secrets_emhass.yaml`, `emhass/share/config.json`, live CSV snapshots.

HA packages are rendered from `config/site.env` at deploy (`scripts/render-ha-packages.sh`).

**Home Assistant `${HA_CONFIG_DIR}/secrets.yaml` (one-time on HA host):** `emhass_publish_url` — REST URL for `rest_command.emhass_publish_data` in `ha_emhass.yaml` (`http://<energy-host>:5000/action/publish-data`). Not in this repo; `deploy-home-assistant.sh` greps live HA secrets before deploy. Energy host IP comes from `ENERGY_REMOTE_HOST` / `emhass_publish_url` in HA secrets.

**Energy host `emhass/secrets_emhass.yaml`:** `hass_url` + `long_lived_token` for cron/monitor → HA API (separate from HA `secrets.yaml`). After Odroid cutover: `hass_url: http://192.168.0.75:8123`.

## Anti-patterns

| Don't | Why |
|-------|-----|
| `open_meteo_cache_max_age` = 30 | False B5/B4 every MPC boundary |
| Rely on `weather-forecast-cache` alone | No-op while cache files exist |
| Stale-CSV fallback on price fail | Violates Policy A |
| Deploy HA packages before P1 aliases | EMHASS templates unavailable |
| Master on while Pi + Odroid HA both up | Two energy managers |
| Interactive sudo in HA deploy | Blocks non-interactive SSH deploys |
| HA Container compose without `TZ` matching `time_zone` | UTC container + naive `as_timestamp(input_datetime)` → false `plan_stale` / control blocked |
