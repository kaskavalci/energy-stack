# AGENTS.md

Instructions for AI coding agents working in the **energy-stack** repository.  
**Last updated:** 2026-06-30

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
| `HA_SSH_HOST` / `HA_SSH_PORT` | Home Assistant: integrations, EMS, dashboards |
| `SIGENERGY_MODBUS_HOST` | Sigenergy inverter (Modbus) |

## Deploy and verify

```bash
# Deploy order: reterminal HA first (P1 aliases), then energy-stack below

./scripts/setup-local.sh              # first clone only
./scripts/deploy-energy-stack.sh      # energy host rsync + rebuild cron/monitor
./scripts/deploy-home-assistant.sh    # render HA packages + deploy to HA

ssh "${ENERGY_REMOTE_HOST}" 'docker ps --filter name=emhass'
```

HA deploy uses **additive merge** (`scripts/lib/ha_config_merge.py`) — only upserts energy-stack package keys. Renders `home-assistant/*.yaml` from `config/site.env` before upload.

## EMHASS invariants (do not break)

- `optimization_time_step`: **30** min; `prediction_horizon`: **32** max
- Price methods: **csv**; weather: **open-meteo**
- `open_meteo_cache_max_age`: **120** min — must stay **>** MPC cadence (30 min)
- Price fetch failure → **abort MPC** (no stale-CSV fallback)
- Weather retry → `clear_weather_cache_files()` before EMHASS `weather-forecast-cache`
- Shared stale logic: `lib/weather_cache_stale.py` (cron + monitor)

## Key docs

| Topic | Path |
|-------|------|
| Setup / site.env | `docs/SETUP.md` |
| Entity contract (cross-repo) | `docs/ENTITY_CONTRACT.md` |

Operator scratch notes (gitignored): `memory-bank/notebook/`

## Coding conventions

- Minimal, focused diffs; match existing YAML/Python style
- Never commit secrets: `config/site.env`, `secrets_emhass.yaml`, `emhass/share/config.json`, live CSV snapshots
- Only create git commits when explicitly requested
