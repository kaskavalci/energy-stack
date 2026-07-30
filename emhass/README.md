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

**PV units and history:** EMHASS expects **watts**. Envoy reports **kW** — on
HA use `sensor.emhass_pv_power_w`
([ha_emhass_sensors.yaml](../home-assistant/ha_emhass_sensors.yaml)). Both
`sensor_power_photovoltaics` and `sensor_power_photovoltaics_forecast` use this
actual-PV sensor, and `sensor_linear_interp` and `sensor_replace_zero` include
it.

**Load forecast:** EMHASS uses its configured native `naive` method with
`sensor.emhass_house_load_sample_w` as load history. The HA trigger sensor
records the calculated house load once per minute in watts. Do not pass a
runtime `load_power_forecast` list and do not substitute a price or power-flow
entity. Allow at least 16 hours of sampled history before switching a 32-step,
30-minute horizon. After changing sensor keys, delete `/data/params.pkl` or
restart `emhass`.

Before each real MPC run, cron reads the load entity and 30-minute step from
the live `share/config.json`, then validates HA history for the effective
prediction horizon. The query includes one extra preceding half-hour. After
the newest finite sample passes the five-minute freshness limit, its floored
UTC bin anchors the required consecutive bins. This lets a run at exactly
`:00` or `:30` use the last completed bin until the first sample in the new bin
arrives; once that sample exists, the window advances.

Load history is classified into three runtime modes:
- `healthy`: all required half-hour bins are covered;
- `degraded`: exactly one fully bracketed interior gap contains at most six consecutive bins (3 hours) and the newest sample is fresh (<=5 min);
- `unsafe`: longer, multiple, edge, unbracketed, stale, or malformed history.

Both healthy and degraded runs use the native `naive-mpc-optim` payload without explicit load forecast, `alpha`, or `beta`. EMHASS performs its configured in-memory linear interpolation over bounded degraded gaps. Genuine `0 W` samples are valid; keep `set_zero_min: false` so EMHASS does not replace them. Query bounds, freshness, flooring, and bin coverage are calculated as real UTC instants; local time is used only in diagnostics. HA timestamps must include a UTC offset.

Unsafe load-history failure is fail-closed: malformed config, an HA history error, invalid samples, unbracketed or excessive missing bins, or stale history records `mpc.ok=false` with `failure_kind=load_history` before any EMHASS action. The run does not optimize, publish, or advance `input_datetime.emhass_last_successful_mpc`; HA's 40-minute freshness gate restores Sigenergy native control. Cron never constructs or submits a replacement load forecast list or synthetic HA recorder state. Expected config, filesystem, request, protocol, decoding, JSON, and response-shape failures in the history gate are normalized without masking unexpected programming errors.

EMHASS image upgrades must satisfy this native interpolation contract before updating image pins in `docker-compose.yml`. See [docs/EMHASS_UPGRADES.md](../docs/EMHASS_UPGRADES.md) for pin requirements and verification procedures. Normal MPC strictly
reads the HA credential file once before any HA request and reuses that
immutable URL/token snapshot for SOC, prices, history, and the successful-MPC
marker. The local preflight does not probe HA; subsequent endpoint failures
remain owned by optional SOC, required prices, required history, or marker
publication.

This per-run gate does not replace the cutover gate: before the first energy
deployment, independently verify at least 16 hours of usable recorder history
and all 32 half-hour bins.

**Home Assistant URL:** `hass_url` must end with `/` in `secrets_emhass.yaml`
or `publish-data` posts fail (`8123api/states/...`).

**MPC SOC:** `input_number.emhass_soc_min` / `emhass_soc_max` → `battery_minimum_state_of_charge` / `battery_maximum_state_of_charge` every slot. **`soc_final`** (horizon endpoint) follows **UI min** (`EMHASS_SOC_MIN_SENSOR`), not `battery_target_state_of_charge` in `config.json`. Override: `fetch_prices_emhass.py --soc-final` or `--omit-soc-final`.

See [docs/SETUP.md](../docs/SETUP.md).

## Deploy from laptop (source of truth)

```bash
./scripts/deploy-home-assistant.sh   # HA
./scripts/deploy-energy-stack.sh     # energy host
```
