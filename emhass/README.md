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

**PV units:** EMHASS expects **watts**. Envoy reports **kW** — on HA use `sensor.emhass_pv_power_w` ([ha_emhass_sensors.yaml](../home-assistant/ha_emhass_sensors.yaml)) and set `sensor_power_photovoltaics` to that entity in `config.json` when recorder history is healthy.

**PV history placeholder (2026-07-11):** When `sensor.emhass_pv_power_w` full-day history returns HTTP 500 (recorder Jul 11 07 UTC), point `sensor_power_photovoltaics`, `sensor_linear_interp`, and `sensor_replace_zero` at `sensor.p_pv_forecast` — a sensor whose history API returns 200 for the same window. **EMHASS rejects identical load and PV sensors**, so do not reuse the Zonneplan tariff placeholder used for load keys. Revert PV keys to `sensor.emhass_pv_power_w` after HA recorder repair. Delete `/data/params.pkl` or restart `emhass` after any sensor key change.

**Load forecast:** `emhass-cron` sends a constant **300 W** `load_power_forecast` per MPC slot. The first slot may use a live HA reading via `fetch_current_load` (falls back to 300 W on error). Do **not** point `sensor_power_load_no_var_loads` / `var_model` at `sensor.emhass_house_load_power_w` (HA history 500) or `sensor.sigen_0_consumed_power` (EMHASS mix NaN as of 2026-07-03). Preferred: `sensor.electricity_meter_power_consumption` when P1 recorder history is stable. **Workaround (2026-07-11):** use `sensor.zonneplan_current_electricity_tariff` as the EMHASS load history placeholder when mqtt P1 power entities return HTTP 500 — actual load is still the constant 300 W list passed at runtime. Revert load keys to `sensor.electricity_meter_power_consumption` after HA recorder repair (Jul 11 07 UTC, Jul 10 14 UTC). After changing load sensor keys, delete EMHASS `/data/params.pkl` or restart the `emhass` container. **`hass_url` must end with `/`** in `secrets_emhass.yaml` or `publish-data` posts fail (`8123api/states/...`).

**Alpha / beta:** The MPC payload still passes `alpha=1.0` and `beta=0.0`. These control EMHASS internal blending of the runtime `load_power_forecast` against its own history fetch — they do **not** skip the history API call. The Jul 2026 MPC failures were caused by HA history HTTP 500 on configured sensors, not by alpha/beta values. Sensor placeholders in `config.json` are the deploy-time fix; removing alpha/beta from the payload remains a future optimization per [2026-07-05 safe-blending design](../docs/superpowers/specs/2026-07-05-emhass-safe-blending-design.md) (not yet implemented).

**MPC SOC:** `input_number.emhass_soc_min` / `emhass_soc_max` → `battery_minimum_state_of_charge` / `battery_maximum_state_of_charge` every slot. **`soc_final`** (horizon endpoint) follows **UI min** (`EMHASS_SOC_MIN_SENSOR`), not `battery_target_state_of_charge` in `config.json`. Override: `fetch_prices_emhass.py --soc-final` or `--omit-soc-final`.

See [docs/SETUP.md](../docs/SETUP.md).

## Deploy from laptop (source of truth)

```bash
./scripts/deploy-home-assistant.sh   # HA
./scripts/deploy-energy-stack.sh     # energy host
```
