# EMHASS on the energy host

Runtime files live under `share/` and `secrets_emhass.yaml` on the energy host. Copy examples on first setup:

```bash
./scripts/setup-local.sh
```

| File | Tracked | Notes |
|------|---------|-------|
| `share/config.json.example` | yes | Copy → `share/config.json` (gitignored) |
| `secrets_emhass.yaml.example` | yes | Copy → `secrets_emhass.yaml` (gitignored) |
| `share/data_*_forecast.csv` | no | Live MPC/price CSVs |

Edit `share/config.json` locally, then push via `./scripts/deploy-energy-stack.sh`.

**PV units:** EMHASS expects **watts**. Envoy reports **kW** — on HA use `sensor.emhass_pv_power_w` ([ha_emhass_sensors.yaml](../home-assistant/ha_emhass_sensors.yaml)) and set `sensor_power_photovoltaics` to that entity in `config.json`.

**Load forecast:** `emhass-cron` sends a constant **300 W** `load_power_forecast` per MPC slot. `sensor.emhass_house_load_power_w` is kept in `config.json` for observability.

See [docs/SETUP.md](../docs/SETUP.md).

## Deploy from laptop (source of truth)

```bash
./scripts/deploy-home-assistant.sh   # HA
./scripts/deploy-energy-stack.sh     # energy host
```
