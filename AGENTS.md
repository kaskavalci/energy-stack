# AGENTS.md

Instructions for AI coding agents working in the **energy-stack** repository.  
**Last updated:** 2026-07-21

## Project overview

Home energy automation: **EMHASS MPC** on the energy host, **Zonneplan** prices via HA, **Sigenergy Remote EMS** on Home Assistant.

Laptop git repo is **source of truth**. Deploy with scripts; live host configs may differ slightly from git.

**Local config:** `config/site.env` (gitignored) — hosts, entity IDs, deploy targets. See [docs/SETUP.md](docs/SETUP.md).

**Before SSH, deploy checks, or live debugging:** read `config/site.env` for current values. Do not hardcode IPs, usernames, or device serials in tracked files — use role names and `site.env` keys (see `config/site.env.example`, [docs/ENTITY_CONTRACT.md](docs/ENTITY_CONTRACT.md)).

## Related repos

- **reterminal** — ESPHome firmware + P1 MQTT alias package (`ha_p1_mqtt_aliases.yaml`). Required before EMHASS templates work. See [docs/ENTITY_CONTRACT.md](docs/ENTITY_CONTRACT.md).

## Memory bank (read first)

Before coding, read:

- `memory-bank/activeContext.md` — current focus, deploy status
- `memory-bank/techContext.md` — stack, invariants, anti-patterns

Update `memory-bank/` after significant changes.

## Agent skills

For EMHASS, Zonneplan prices, Open-Meteo/PV cache, health alerts, or HA trading packages:

`.agents/skills/emhass-energy-stack/SKILL.md`

## Hosts

Configured in `config/site.env` (copy from `config/site.env.example`):

| Key | Role |
|-----|------|
| `ENERGY_REMOTE_HOST` | Docker: EMHASS, emhass-cron, energy-monitor, MQTT bridges |
| `HA_SSH_HOST` / `HA_SSH_PORT` | Home Assistant Container host (SSH deploy) |
| `HA_CONFIG_DIR` | HA config on host (default `/opt/homeassistant/config`) |
| `HA_COMPOSE_DIR` / `HA_COMPOSE_FILE` | Compose path for restart |
| `HA_CONTAINER_NAME` | HA container name (default `homeassistant`) |
| `SIGENERGY_MODBUS_HOST` | Sigenergy inverter (Modbus) |

WUD runs on `ENERGY_REMOTE_HOST` (UI port **3000**) and `HA_SSH_HOST` (UI port
**3001**). It has no UI authentication and sends notifications only; it must not
update containers automatically. Each service keeps data in a persistent
`/store` volume. WUD excludes local `emhass-cron` and `energy-monitor` builds.
Docker Compose port mappings bind all interfaces by default. Operators must use
host firewall rules to allow trusted LAN access only and verify that an
untrusted network is denied. Do not configure firewall rules automatically.

## Deploy and verify

```bash
# Deploy order: reterminal HA first (P1 aliases), then energy-stack below

./scripts/setup-local.sh              # first clone only
./scripts/deploy-all.sh               # site.env + energy host + Home Assistant
# Or: ./scripts/deploy-energy-stack.sh then ./scripts/deploy-home-assistant.sh

ssh "${ENERGY_REMOTE_HOST}" 'docker ps --filter name=emhass'
```

HA deploy uses **additive merge** (`scripts/lib/ha_config_merge.py`) — only upserts energy-stack package keys. Renders `home-assistant/*.yaml` from `config/site.env` before upload. Config check is `docker exec` (not `ha core check`); restart is `docker compose` (not `ha core restart`). Deploy user must own `${HA_CONFIG_DIR}` and be in the `docker` group (no interactive sudo).

For WUD only, run `./scripts/deploy-home-assistant.sh --wud-only`. It backs up
the HA Compose file and deploys WUD without an HA restart. Roll back by restoring
that Compose backup, then run Compose with its WUD `--env-file` option.

## EMHASS invariants (do not break)

- `optimization_time_step`: **30** min; `prediction_horizon`: **32** max
- Price methods: **csv**; weather: **open-meteo**
- `open_meteo_cache_max_age`: **150** min — must stay **>** MPC cadence (30 min); cron soft refresh at `max_age − 2×step` (90 with defaults)
- Price fetch failure → **abort MPC** (no stale-CSV fallback)
- Weather retry → `clear_weather_cache_files()` before EMHASS `weather-forecast-cache`
- Shared stale logic: `lib/weather_cache_stale.py` (cron + monitor)
- WUD image: `docker.io/getwud/wud:8.3.0@sha256:1cf5ecfdbd2389847427fd9685c4fd337ee8c74a0f584c52288d760d9a58811e`
- WUD secrets stay only in gitignored `config/wud.env`; never print or commit them.
- Run WUD Compose commands with `--env-file config/wud.env` on the energy host
  and the selected HA WUD env-file path on the HA host.
- WUD sends notifications only. Do not add automatic-update triggers.

### EMHASS upgrades

- `docker-compose.yml` must use a release tag plus immutable digest, never
  `latest`.
- Before changing the EMHASS image, run
  `./scripts/verify-emhass-load-gap-contract.sh '<tag@sha256:digest>'`, the
  Compose pin test, and the full cron tests.
- Record passing output in `docs/verification/emhass/`. Do not infer native
  interpolation compatibility from release notes alone.

## Key docs

| Topic | Path |
|-------|------|
| Setup / site.env | `docs/SETUP.md` |
| Entity contract (cross-repo) | `docs/ENTITY_CONTRACT.md` |
| HA Odroid Container migration | `docs/ha-odroid-container-migration.md` |

Operator scratch notes (gitignored): `memory-bank/notebook/`

## Coding conventions

- Minimal, focused diffs; match existing YAML/Python style
- Never commit secrets: `config/site.env`, `config/wud.env`, `secrets_emhass.yaml`, `emhass/share/config.json`, live CSV snapshots
- Only create git commits when explicitly requested
