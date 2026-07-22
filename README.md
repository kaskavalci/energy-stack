# energy-stack

![Proudly Vibe Coded - Neon Flame](https://vibecoded.fyi/badges/flat/main/proudly-vibe-coded-neon-flame.svg)

Docker + Home Assistant automation that runs **EMHASS** model-predictive control on a home battery using **Zonneplan** dynamic electricity prices, with optional **Sigenergy Remote EMS** execution.

Licensed under the [MIT License](LICENSE).

## What this does

Every 30 minutes the stack:

1. Reads day-ahead prices from the Zonneplan integration on Home Assistant
2. Refreshes Open-Meteo PV forecasts when the weather cache is stale
3. Runs EMHASS `naive-mpc-optim` with live battery SOC from HA
4. Publishes the plan back to HA (`sensor.p_batt_forecast`, prices, SOC steps, etc.)

Home Assistant can then apply charge/discharge limits to the Sigenergy inverter via Remote EMS scripts. A kill-switch, plan-validity gates, and split health monitoring (energy host + HA) keep automation from running on stale data.

### Expected Sigenergy behavior

`input_boolean.emhass_auto_control_master` is the operator kill-switch.

| Master | Expected inverter behavior |
| ------ | -------------------------- |
| **On** (healthy plan) | HA applies the current MPC plan: command charge, command discharge, or standby, with power limits clamped to each MQTT number entity's advertised `max` (and never above the operator caps). |
| **Off** | HA restores native `Maximum Self-consumption (Default)` automatically. Operators do not pick that mode by hand. Success is recorded only after mode and both limits are confirmed; then `input_datetime.emhass_last_limit_write` advances. |

While the master is off, the same native restoration also runs after a Home Assistant Core start or when the Sigenergy mode entity returns from `unknown`/`unavailable` (including when the inverter already reports native mode but limits are stale).

If mode or limit confirmation times out, the master stays off and HA raises an `EMHASS D2` notification — treat that as “do not re-enable trading until fixed.” Details: [Sigenergy native fallback](docs/SETUP.md#sigenergy-native-fallback).

```text
Zonneplan (HA) → emhass-cron → CSV + MPC → EMHASS → publish-data → HA sensors
Open-Meteo → PV forecast cache ────────────────────────┘
P1 meter + Envoy PV (HA) → house load / PV templates ───→ EMHASS inputs
Sigenergy (Modbus/MQTT) ← optional Remote EMS ←────────── HA scripts
```

| Component                              | Role                                                                                      |
| -------------------------------------- | ----------------------------------------------------------------------------------------- |
| **Energy host** (`ENERGY_REMOTE_HOST`) | Docker: EMHASS, price/MPC cron, health monitor, Mosquitto, P1 MQTT bridge, sigenergy2mqtt |
| **Home Assistant** (`HA_SSH_HOST`)     | Zonneplan HACS, EMHASS REST sensors, trading dashboard, EMS scripts, entity-level alerts  |

Site-specific hosts, entity IDs, and Modbus targets live in gitignored `config/site.env`. HA package YAML is rendered from those values at deploy time.

## Prerequisites

- Docker Compose on a dedicated **energy host**
- Home Assistant **Container** (Docker Compose) with **Zonneplan** HACS — see [HA Odroid migration](docs/ha-odroid-container-migration.md)
- **[reterminal](https://github.com/)** P1 MQTT aliases on HA first — see [docs/ENTITY_CONTRACT.md](docs/ENTITY_CONTRACT.md)
- Sigenergy inverter + `sigenergy2mqtt` (optional; required for battery MPC + Remote EMS)

## First-time setup

```bash
./scripts/setup-local.sh   # copy examples → gitignored local config
# Edit config/site.env and emhass/secrets_emhass.yaml
./scripts/render-ha-packages.sh
```

See [docs/SETUP.md](docs/SETUP.md) for the full file list, entity placeholders, and deploy order.

Add to Home Assistant `${HA_CONFIG_DIR}/secrets.yaml` (one-time):

```yaml
emhass_publish_url: http://<ENERGY_HOST_IP>:5000/action/publish-data
```

Use the energy host **LAN IP** as seen from HA. `deploy-home-assistant.sh` preflights this key before upload.

## Deploy

```bash
./scripts/deploy-energy-stack.sh      # rsync + rebuild cron/monitor on energy host
./scripts/deploy-home-assistant.sh    # render packages + merge into HA /config
```

HA Container deploy knobs in `config/site.env`: `HA_CONFIG_DIR`, `HA_COMPOSE_DIR` / `HA_COMPOSE_FILE`, `HA_CONTAINER_NAME`. Config check and restart use Docker (no Supervisor `ha` CLI).

HA config merge requires `ruamel.yaml`:

```bash
python3 -m venv .venv && .venv/bin/pip install -r scripts/lib/requirements.txt
```

## Documentation

- [Setup guide](docs/SETUP.md) (includes [Sigenergy native fallback](docs/SETUP.md#sigenergy-native-fallback))
- [Entity contract (cross-repo)](docs/ENTITY_CONTRACT.md)
- [HA Odroid Container migration](docs/ha-odroid-container-migration.md)

AI agents: [AGENTS.md](AGENTS.md) and [.agents/skills/emhass-energy-stack/SKILL.md](.agents/skills/emhass-energy-stack/SKILL.md).
