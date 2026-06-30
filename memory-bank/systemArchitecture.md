# System Architecture

Live hosts and entity IDs: **`config/site.env`** (gitignored). Public key list: [docs/ENTITY_CONTRACT.md](../docs/ENTITY_CONTRACT.md).

## Topology

```text
Home Assistant (HA_SSH_HOST)  ←REST→  Energy host (ENERGY_REMOTE_HOST)
  Zonneplan HACS                  emhass, emhass-cron, energy-monitor
  EMHASS packages                 mqtt4dsmr, sigenergy2mqtt
  Sigen EMS scripts               Mosquitto
                                        │
                                        └── Modbus → Sigenergy (SIGENERGY_MODBUS_HOST)
```

| Role | `site.env` key(s) | Purpose |
|------|-------------------|---------|
| Home Assistant | `HA_SSH_HOST`, `HA_SSH_USER`, `HA_SSH_PORT` | Integrations, EMS, dashboards |
| Energy stack | `ENERGY_REMOTE_HOST`, `ENERGY_REMOTE_DIR` | Docker: MPC, prices CSV, alerts, MQTT |
| Sigenergy inverter | `SIGENERGY_MODBUS_HOST`, `SIGENERGY_MODBUS_SLAVE` | Modbus (sigenergy2mqtt) |

## Repo layout (flat)

```text
energy-stack/           # git root
├── docker-compose.yml
├── emhass-cron/
├── monitor/
├── lib/
├── home-assistant/     # HA packages + dashboards/ (rendered from site.env)
├── scripts/
└── docs/
```

## MPC data flow

```text
Zonneplan → HA HACS → emhass-cron → CSV → naive-mpc-optim → publish-data → HA sensors
Open-Meteo → cache → P_PV
Optional: script.sigen_apply_emhass_plan → Remote EMS
```

## Health monitoring

`energy-monitor` every 10 min → `last_enever_run.json`, cache geometry, HA notify on failure.

## Cross-repo

P1 aliases from **reterminal** repo → `P1_POWER_*_ENTITY` in `site.env` → EMHASS house-load templates.

See [docs/ENTITY_CONTRACT.md](../docs/ENTITY_CONTRACT.md).
