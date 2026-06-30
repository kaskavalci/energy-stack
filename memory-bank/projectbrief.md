# Project Brief

## What this is

**energy-stack** runs home battery arbitrage: **EMHASS** MPC on the **energy host**, **Zonneplan** dynamic prices via Home Assistant, optional **Sigenergy Remote EMS** on the **HA host**.

Hosts: `config/site.env` (`ENERGY_REMOTE_HOST`, `HA_SSH_HOST`).

## Intended users

- **Maintainer** — Deploy docker stack, tune MPC/alert thresholds, respond to health alerts
- **Household** — Monitor via HA "EMHASS & Sigen" dashboard (Lovelace YAML in this repo)

## Core requirements

| Area | Requirement |
|------|-------------|
| Energy host (`ENERGY_REMOTE_HOST`) | Docker: EMHASS, emhass-cron, energy-monitor, Mosquitto, mqtt4dsmr, sigenergy2mqtt |
| Home Assistant (`HA_SSH_HOST`) | EMHASS publish, Sigen EMS scripts, monitoring sensors, trading dashboard |
| Prices | Zonneplan HACS → REST → CSV on `/share` → EMHASS (no stale-CSV fallback) |
| PV | Open-Meteo + pvlib; cache refreshed before MPC when stale |
| Safety | EMS kill-switch; fail-safe when optim/publish stale |

## Related repos

- **reterminal** — ESPHome dashboard + P1 MQTT aliases required on HA ([ENTITY_CONTRACT.md](../docs/ENTITY_CONTRACT.md))

## Repository role

Laptop git is source of truth. Deploy via `scripts/deploy-energy-stack.sh` and `scripts/deploy-home-assistant.sh`.
