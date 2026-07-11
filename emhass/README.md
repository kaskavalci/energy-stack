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

**Load forecast:** `emhass-cron` sends a constant **300 W** `load_power_forecast` per MPC slot with `alpha=1.0` / `beta=0.0` so EMHASS does not blend against a stale HA load reading. Do **not** point `sensor_power_load_no_var_loads` / `var_model` at `sensor.emhass_house_load_power_w` (HA history 500) or `sensor.sigen_0_consumed_power` (EMHASS mix NaN as of 2026-07-03). Preferred: `sensor.electricity_meter_power_consumption` when P1 recorder history is stable. **Workaround (2026-07-11):** use `sensor.zonneplan_current_electricity_tariff` as the EMHASS history placeholder when mqtt P1 power entities return HTTP 500 for yesterday's history window — actual load is still the constant 300 W list passed at runtime. After changing load sensor keys, delete EMHASS `/data/params.pkl` or restart the `emhass` container. **`hass_url` must end with `/`** in `secrets_emhass.yaml` or `publish-data` posts fail (`8123api/states/...`).

**MPC SOC:** `input_number.emhass_soc_min` / `emhass_soc_max` → `battery_minimum_state_of_charge` / `battery_maximum_state_of_charge` every slot. **`soc_final`** (horizon endpoint) follows **UI min** (`EMHASS_SOC_MIN_SENSOR`), not `battery_target_state_of_charge` in `config.json`. Override: `fetch_prices_emhass.py --soc-final` or `--omit-soc-final`.

See [docs/SETUP.md](../docs/SETUP.md).

## Deploy from laptop (source of truth)

```bash
./scripts/deploy-home-assistant.sh   # HA
./scripts/deploy-energy-stack.sh     # energy host
```
