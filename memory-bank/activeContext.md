# Active Context

**Last updated:** 2026-07-22 ~20:36 CEST
**Author:** Composer

## CoreELEC weather.ha → Odroid HA (2026-07-22) — FIXED

CoreELEC (`root@192.168.0.70`) `weather.ha` already had `ha_server=http://192.168.0.75:8123/` + valid token. Failure was `weather.forecast_home` restored ghost (`state=unavailable`, `restored=true`) after Odroid cutover — Met.no Forecast Home never came back.

**Fix:** set `ha_weather_forecast_entity_id` → `weather.buienradar` (live on Odroid). Verified: `[weather.ha] Weather updated successfully.` Optional later: remove/recreate `weather.forecast_home` on HA if Met.no is wanted again.

## Current focus — HA Odroid Container migration (2026-07-21)

Migrate HA from Pi 3 HAOS (`.109`) → Armbian Odroid N2 Container (`.75`, SSH `ha`). Energy stack stays on `.135`.

### Master ON + control diagnose (2026-07-21 ~23:03–23:10 CEST) — FIXED

Operator enabled `input_boolean.emhass_auto_control_master` on Odroid. Control appeared idle.

**Root cause:** Odroid HA container had no `TZ` (system UTC) while HA `time_zone` is `Europe/Amsterdam`. Freshness templates used `as_timestamp(states('input_datetime…'))` on naive wall-clock strings → age ≈ **−116 min** → false `plan_stale` → `emhass_control_allowed=off`. MPC itself was fine (`mpc.ok=true` @ 23:00:42; publish OK).

**Fix applied:**
- `home-assistant/docker-compose.yml`: `environment.TZ=Europe/Amsterdam`; live compose recreated (`date` now CEST).
- Templates: freshness + apply cooldown use `state_attr(..., 'timestamp')` instead of parsing state strings (`ha_emhass_dashboard.yaml`, `ha_sigen_ems.yaml`).
- Deployed HA packages; after MQTT/EMHASS entities restored, control applied.

**Verified working (~23:10 CEST):**
- master **on**; `emhass_mpc_fresh=on` (`reason=ok`, age ≈ +9 min); `emhass_control_allowed=on` (`block_reason=ok`)
- Sigen: Remote EMS on; mode `Command Discharging (Output power from the battery first)`; charge `0.0` / discharge `2.7` kW (matches `p_batt_forecast=2700`); `emhass_last_limit_write=2026-07-21 23:10:00`
- Pi HA Core still **Exited** on `.109` (OS up for rollback)

### Cutover wiring + verify (2026-07-21) — DONE

**Wiring:**
- `config/site.env` → Odroid: `HA_SSH_HOST=ha`, `HA_SSH_PORT=22`, `HA_SSH_USER=halil`, `HA_CONFIG_DIR=/home/halil/homeassistant/config`, `HA_COMPOSE_DIR=/home/halil/homeassistant`, `HA_CONTAINER_NAME=homeassistant`
- **Pi rollback (site.env):** `HA_SSH_USER=root`, `HA_SSH_HOST=192.168.0.109`, `HA_SSH_PORT=22222`
- Energy-host + local `emhass/secrets_emhass.yaml` `hass_url` → `http://192.168.0.75:8123/`; restarted `emhass`, `emhass-cron`, `energy-monitor`
- **Rollback hass_url:** `http://192.168.0.109:8123/`

**Verified on Odroid (earlier, master off):** MQTT, P1, SOC, Envoy, EMHASS entities, MPC publish to `.75` OK.

### Pi Core stop (2026-07-21 ~22:44 CEST) — DONE

- Ran `ha core stop` on Pi HAOS — **not** `ha host shutdown`. Container `Exited (0)`; OS still up.
- Master enabled on Odroid only after Pi Core stop (2026-07-21 ~22:50+).

**Todo / next (operator):**
1. ~~Stop Pi HA Core~~ — done
2. ~~Master on Odroid + control verify~~ — done ~23:10 CEST
3. Optional later: full Pi HAOS host shutdown when rollback window closes

Hard rule: never enable master while a second HA could control Sigenergy. See migration doc.

**Earlier phases (bringup / restore / sanitize) — complete.** Summary: interim layout `/home/halil/homeassistant/`; backup `pre-odroid-migrate-20260721`; Container sanitization removed Supervisor hassio cascade; preferred `/opt/homeassistant` still optional after one-time sudo.

## Memory pressure check (2026-07-21)

Both boxes are ~900 MiB Pi-class hosts; neither has comfortable headroom. Odroid migration targets HA OOM.

| Host | RAM | Available | Swap used | Verdict |
|------|-----|-----------|-----------|---------|
| Energy (`192.168.0.135`) | 905 MiB | ~275 MiB | ~490 MiB | Tight but no recent OOM in journal; surviving on swap |
| HA (`raspberrypi3-64`, pre-cutover) | 903 MiB | ~185–350 MiB | ~500–600 MiB | Confirmed OOM Jul 19 19:40 — Core `python3` killed at ~519 MiB anon RSS |

Energy-host footprint (idle): EMHASS gunicorn worker alone **~268 MiB RSS** (~30% of RAM) + master ~17 MiB; dockerd/containerd ~80 MiB; sigenergy2mqtt ~18 MiB. No compose memory limits. Uptime 60d, no `Out of memory` in current kernel journal.

HA: Core container ~334–393 MiB; host free often ~11–15 MiB. Previous boot OOM-killed HA despite `oom_score_adj=-300`. Current uptime ~9h after restart today.



## EMHASS native PV panel deployment verified (2026-07-17)

- EMHASS cached parameters and the fresh Open-Meteo cache match the configured
  Dutch site and time zone; no Alpine image defaults remain in use.
- Home Assistant passed configuration checks before and after its single checked
  restart. The live panel contains the native hourly statistics graph and
  today-only PV forecast table, with no custom chart or ApexCharts resource.
- One normal post-restart MPC exited `0`; prices, weather cache, naive MPC, and
  publish stages completed. The current run artifact reports `mpc.ok=true`, HA
  reports `optim_status=Optimal`, and all 26 published forecast rows are current.
- EMHASS is running and healthy; `emhass-cron` and `energy-monitor` are running.
  The EMHASS master was `on` before and after the MPC and was never toggled.
- Preserve the verified Task 7 Home Assistant backup at
  `/mnt/data/supervisor/homeassistant/backups/energy-stack-20260717-102948/`;
  it contains `configuration.yaml`.

## PV actual + forecast panel (deployed + verified — 2026-07-17)

Restores both PV actual and forecast context in one low-memory native Home
Assistant panel, without ApexCharts or any custom frontend resource.

**Deploy result (2026-07-17):** `render-ha-packages.sh` + `deploy-home-assistant.sh`
ran clean. `ha core check` passed **before and after** the Core restart. The
EMHASS master (`input_boolean.emhass_auto_control_master`) was `on` before and
remained `on` after the restart (never toggled). Live
`dashboards/emhass_trading.yaml` on the HA host confirms the `statistics-graph`
card lists `sensor.emhass_pv_power_w` then `sensor.p_pv_forecast` (that order),
titled `PV actual vs forecast — yesterday and today`, with the today-only
markdown forecast table still below it and no ApexCharts/custom resource.
Backup: `/mnt/data/supervisor/homeassistant/backups/energy-stack-20260717-124355/`.

**Done in repo (code + tests):**

- `home-assistant/dashboards/emhass_trading.yaml`: the PV `vertical-stack`
  `statistics-graph` now lists two entities — `sensor.emhass_pv_power_w`
  (`Actual PV (hourly mean)`) first and `sensor.p_pv_forecast`
  (`Forecast PV (hourly mean)`) second. Title renamed to
  `PV actual vs forecast — yesterday and today`. `days_to_show: 2`,
  `period: hour`, `chart_type: line`, `stat_types: [mean]` unchanged. The
  today-only markdown forecast table below it is unchanged.
- No new helper entity, backend job, custom card, or frontend resource. Both
  sensors have `state_class: measurement`, so existing hourly statistics cover
  the historical series with negligible incremental memory use.
- `scripts/tests/test_emhass_trading_dashboard.py`: asserts the two ordered
  statistics entities and their names, the renamed graph title, the retained
  today-only forecast table, and the continued absence of ApexCharts/custom
  resources. 6 tests pass; `./scripts/render-ha-packages.sh` renders cleanly.

**Native-card limitation (by design):** the graph's forecast line is the
recorded history of the active-step `sensor.p_pv_forecast` state, not the future
timestamped values in its `forecast` attribute. A true actual-plus-future
overlay would require a custom chart/data transform, intentionally out of scope
for the low-memory HA host; the table remains the future view.

**Deploy verified (2026-07-17):** live YAML contains both sensors in order and
the EMHASS master remained `on` throughout the deploy/restart.

## Sigenergy native fallback verified (2026-07-16)

- HA package deploy: successful `ha core check` and Core restart.
- HA-start reconciliation with master off: native mode confirmed; charge limit
  `8.8` kW and discharge limit `9.0` kW confirmed before
  `emhass_last_limit_write` advanced.
- Direct master-off: native mode and the same `8.8`/`9.0` kW limits confirmed
  before `emhass_last_limit_write` advanced.
- `sigenergy2mqtt` reconnect with master off: native restoration reran and
  confirmed the same `8.8`/`9.0` kW targets.
- Trading resume: at the 10:30 boundary, control gate `ok`, mode `Standby`
  matching `p_batt_forecast=0`, and `emhass_last_limit_write=2026-07-16 10:30:00`.
- Follow-up: the 10:30 EMHASS MPC HTTP call returned 500 after that apply;
  `sensor.emhass_mpc_status` is `failed` while master remains on with standby
  idle limits. Investigate EMHASS separately; do not treat this as a native-
  fallback regression. `sigenergy2mqtt` config was migrated to the 2026.4 list
  schema (`modbus: [{host, inverters}]`, `mqtt.broker`).

## Pre-migration notes (still relevant)

**MPC recovered (2026-07-11 ~13:28 CEST)** after PV history placeholder fix on energy host.

| Window (CEST) | Failure mode | Evidence |
|---|---|---|
| 07:30 – 09:30 | `ValueError: cannot convert float NaN to integer` in `get_mix_forecast` | emhass traceback; weather-cache gap |
| 10:00 – 13:23 | HA history HTTP **500** on `sensor.emhass_pv_power_w` | bad recorder hour **07:00 UTC** Jul 11 |
| 13:23 | Zonneplan placeholder for PV **rejected** by EMHASS | `sensor.power_photovoltaics and sensor.power_load_no_var_loads should not be the same` |
| 13:28 | **Recovery** with `sensor.p_pv_forecast` PV placeholder | `last_enever_run.json` → `mpc.ok: true` |

**Live config state (energy host):**
- Load keys → `sensor.zonneplan_current_electricity_tariff`
- PV keys → `sensor.p_pv_forecast` (`sensor_power_photovoltaics`, `sensor_linear_interp`, `sensor_replace_zero`)
- `hass_url` trailing slash retained

**P1 / recorder:** Jul 11 full-day history still HTTP 500 for P1 and `sensor.emhass_pv_power_w` (bad hour 07 UTC). Jul 10 still bad at hour 14 UTC. Zonneplan tariff and `sensor.p_pv_forecast` Jul 11 full-day → HTTP 200.

**Proper fix:** repair HA recorder Jul 11 07 UTC + Jul 10 14 UTC. Revert placeholders only after full-day history HTTP 200 for affected sensors.

## Repo work queue (before deploy)

**P0 (this session):** `config.json.example` placeholders, alpha/beta doc reconciliation, `fetch_current_load` unit tests — landed in repo commit pending.

**P1 at deploy:** `emhass_recovery_complete` guard, SOC floor migration notes (10%→5%), `block_reason` vs policy alignment, `render-ha-packages.sh` + merge test.

Full schedule: `memory-bank/progress.md` § "What needs to be done, when".

## Other open items

- **Stale SOC sensor** — `sensor.sigen_0_plant_battery_soc` stuck at 10.7% since Jul 10 23:00 UTC (sigenergy2mqtt); investigate separately from MPC
- **SOC execution headroom** — `memory-bank/notebook/emhass-soc-min-trading.md` (proposed, not implemented)

**Actionable Alerts Policy (HA)** — Deployed (2026-07-01). See `memory-bank/notebook/emhass-actionable-alerts-policy.md`.

**Load forecast** — constant 300 W per MPC slot; `sensor.emhass_house_load_power_w` for observability only (not EMHASS config).

## Verification

```bash
./scripts/setup-local.sh
./scripts/render-ha-packages.sh
./scripts/deploy-energy-stack.sh
./scripts/deploy-home-assistant.sh
# On energy host after config/sensor changes:
docker compose exec -T emhass rm -f /data/params.pkl && docker compose restart emhass
docker compose exec -T emhass-cron python3 /app/fetch_prices_emhass.py --action mpc -v
```

## Assumptions

- Live `config.json` on energy host may differ from git — verify via `ENERGY_REMOTE_HOST` / `ENERGY_REMOTE_DIR` in `config/site.env`.
- EMHASS requires load and PV history sensor keys to differ; Zonneplan tariff cannot serve both roles.
- Deploy reterminal HA packages before energy-stack HA packages (P1 aliases).
- Placeholder revert only after HA recorder repair and full-day history HTTP 200 for affected sensors.
- Odroid HA: compose must keep `TZ=Europe/Amsterdam`; freshness templates must keep `state_attr(..., 'timestamp')` — deploy without either regresses to false `plan_stale`.
- Pi HAOS host OS still up for rollback; Core stopped 2026-07-21. Master on Odroid only.
