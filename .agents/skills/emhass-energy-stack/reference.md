# EMHASS energy stack — file reference

| Path | Role |
|------|------|
| `emhass-cron/fetch_prices_emhass.py` | HA Zonneplan prices, MPC chain, weather cache |
| `emhass-cron/crontab` | supercronic schedule |
| `emhass-cron/entrypoint.sh` | Boot: `--write-only` after 90s |
| `energy-stack/docker-compose.yml` | `emhass`, `emhass-cron`, `energy-monitor` |
| `monitor/health_notify.py` | Alerts → HA `notify.notify` |
| `docs/SETUP.md` | First-time setup, deploy order |
| `docs/ENTITY_CONTRACT.md` | `site.env` → HA entity mapping |
| `memory-bank/notebook/` | Operator scratch notes (gitignored) |

## Deploy

```bash
./scripts/deploy-energy-stack.sh
```

## Pull from host

```bash
source scripts/lib/source-site-env.sh
rsync -avz "${ENERGY_REMOTE_HOST}:${ENERGY_REMOTE_DIR}/emhass-cron/" ./emhass-cron/
```

## Price entity

Read `ZONNEPLAN_PRICE_SENSOR` from `config/site.env` (default in `site.env.example`: `sensor.zonneplan_current_electricity_tariff`). HA attribute: `forecast[].electricity_price / 1e7` (€/kWh).
