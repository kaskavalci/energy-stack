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
| `mosquitto`, `mqtt4dsmr`, `sigenergy2mqtt` | MQTT bridges (version pins in Compose; WUD notify-only for upgrades) |
| `wud` | Image update notifications only; one instance on each host, persistent `/store` volume |

HA itself runs on the **HA host** via [`home-assistant/docker-compose.yml`](../home-assistant/docker-compose.yml) (`ghcr.io/home-assistant/home-assistant:2026.8.3`, `network_mode: host`), not on the energy host.

WUD uses `docker.io/getwud/wud:8.3.0@sha256:1cf5ecfdbd2389847427fd9685c4fd337ee8c74a0f584c52288d760d9a58811e` on both hosts. The energy-host UI uses port **3000**; the HA-host UI uses port **3001**. It is unauthenticated. Host firewall rules must enforce LAN-only access. Compose port mappings bind all interfaces by default, so operators must configure host firewall rules and verify that an untrusted network is denied. This repository does not configure firewall rules. WUD excludes local `emhass-cron` and `energy-monitor` builds. It sends Telegram notifications only and must not update containers automatically.

## EMHASS invariants

- `optimization_time_step`: **30** min; `prediction_horizon`: **32** max
- Price methods: **csv**; weather: **open-meteo**
- `open_meteo_cache_max_age`: **150** min — must stay **>** MPC cadence; cron soft clear at `max_age − 2×step` (90 with defaults)
- Price fetch failure → **abort MPC**
- Native load history is healthy with complete coverage and degraded only for
  one fully bracketed interior gap of at most six consecutive 30-minute bins
  after the composite P1/PV/battery-derived sample is fresh (<=5 min). Longer,
  multiple, edge, unbracketed, or stale gaps fail closed.
- Envoy freshness for PV / house-load / C1 is gated by
  `binary_sensor.emhass_envoy_required` only: sun up, dawn grace **>30 min**,
  current 30-minute `sensor.p_pv_forecast` **≥150 W** (`is_number`), and
  `binary_sensor.emhass_mpc_fresh` on. Otherwise treat quiet Envoy as PV **0 W**
  (fail open). Do not use `p_pv_forecast.last_updated` as freshness.
- Degraded mode still uses native `naive`; cron never sends a load list,
  `alpha`, or `beta`, and never writes synthetic HA recorder states.
- EMHASS compose image is a fixed release version (never `latest`; no digest in
  Compose). Before bumping, run
  `scripts/verify-emhass-load-gap-contract.sh '<version@sha256:digest>'` and
  record evidence under `docs/verification/emhass/`.
- Energy-host pins (2026-08-21): `emhass:v0.18.1`,
  `sigenergy2mqtt:2026.8.8`, `mosquitto:2.1.2-alpine`, `mqtt4dsmr:v0.9.8`.
  WUD `wud.tag.include` filters prerelease/dev tags. Upgrade loop: WUD notify →
  changelog → (EMHASS probe) → bump version → deploy.
- Weather retry → `clear_weather_cache_files()` before `weather-forecast-cache`
- Shared stale logic: `lib/weather_cache_stale.py`
- WUD secrets are only in gitignored `config/wud.env`. Never print or commit
  the file or its values. Run WUD Compose with `--env-file config/wud.env` on
  the energy host and the selected WUD env-file path on the HA host.
- EMHASS site location comes from validated `LAT`, `LON`, `ALT`, and
  `TIME_ZONE` in gitignored `config/site.env`; never rely on image defaults.
- `TZ` and `TIME_ZONE` must match.
- The trading dashboard uses native hourly statistics plus a today-only PV
  forecast table. It also shows a native 7-day daily net profit bar graph from
  the existing continuous `sensor.emhass_net_electricity_profit_today`
  (production − delivery, incl. tax) — **no separate daily-snapshot sensor**.
  That sensor's `state_class` is `total` (not `total_increasing`, since net
  profit can decrease intraday) and the `statistics-graph` card uses
  `stat_types: [state]` so each day's bar is the last recorded value that
  day (closed day ≈ EOD P/L; today = live running P/L). Trading dashboard also
  shows `sensor.emhass_planned_profit_today` (covered-slot lock from cron JSON
  via `input_text.emhass_planned_profit_payload`) and a dual 7-day planned vs
  realized bar graph. Planned lock is write-once per local date in
  `/share/daily_planned_profit.json`; auxiliary failure must not fail MPC.
  Import price forecast
  uses **ApexCharts** wrapped in
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
- Sigen diagnostics is a separate YAML Lovelace dashboard (`sigen-diagnostics`)
  with MQTT health sensors (`expire_after: 180`) from `ha_sigen_diagnostics.yaml`;
  native cards only.

## Deploy

- **Energy host:** `./scripts/deploy-energy-stack.sh` → `ENERGY_REMOTE_HOST`:`ENERGY_REMOTE_DIR`
- **Home Assistant:** `./scripts/deploy-home-assistant.sh` → `HA_SSH_HOST` (additive merge via `scripts/lib/ha_config_merge.py`)
  - Config path: `${HA_CONFIG_DIR}` (preferred `/opt/homeassistant/config`)
  - Check: `docker exec ${HA_CONTAINER_NAME} hass --script check_config`
  - Restart: `docker compose -f … restart` (deploy user in `docker` group; no sudo)
- HA merge requires `ruamel.yaml` (`scripts/lib/requirements.txt`)
- **HA WUD only:** `./scripts/deploy-home-assistant.sh --wud-only` backs up the
  HA Compose file, updates WUD, and does not restart HA. To roll back, restore
  that Compose backup and run Compose with the same `--env-file` option.

Hosts and paths from `config/site.env` — see [docs/SETUP.md](../docs/SETUP.md).
Migration: [docs/ha-odroid-container-migration.md](../docs/ha-odroid-container-migration.md).

## Secrets

Never commit `config/site.env`, `config/wud.env`, `emhass/secrets_emhass.yaml`, `emhass/share/config.json`, live CSV snapshots.

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
