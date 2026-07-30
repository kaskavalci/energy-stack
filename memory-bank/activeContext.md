# Active Context

**Last updated:** 2026-07-30 ~21:50 CEST
**Author:** Auto

## Floating `:latest` for EMHASS + sigenergy2mqtt — DEPLOYED (2026-07-30 ~21:49 CEST)

Operator override of the usual tag+digest pin policy. Compose now uses floating
`:latest` (comment in `docker-compose.yml` notes intentional).

| Service | Before | After (`org.opencontainers.image.version`) | Health |
|---------|--------|--------------------------------------------|--------|
| `emhass` | `v0.17.5@sha256:0aa7c906…` | **v0.17.9** (`:latest`, rev `65a5f628…`, created 2026-07-12) | healthy |
| `sigenergy2mqtt` | `2026.7.4@sha256:5e632aa3…` | **2026.7.4** (`:latest`, same digest/rev `b2cd5eda…`) | healthy |

- MQTT env vars kept (`TZ`, `SIGENERGY2MQTT_MQTT_BROKER=mosquitto`,
  `SIGENERGY2MQTT_MQTT_ANONYMOUS=true`).
- Remote backup: `/home/halil/workspace/energy-stack-backups/floating-latest-20260730-214101/`.
- Pull + `docker compose up -d --force-recreate emhass sigenergy2mqtt`; cron/monitor
  waited on emhass healthy (not rebuilt). HA untouched.
- **Caveat:** EMHASS jumped 0.17.5 → 0.17.9 without
  `verify-emhass-load-gap-contract.sh` evidence (pin policy waived for this change).
  Watch next MPC for load-gap / native-naive regressions.

## sigenergy2mqtt upgraded to 2026.7.4 — DEPLOYED (2026-07-30 ~21:19 CEST)

- **Before:** live image was still `ghcr.io/seud0nym/sigenergy2mqtt:latest` →
  **2026.4.16** (Apr 16; PyModbus 3.13.0). Continuous Modbus retry / wrong-ID
  noise after the Jul 29 energy-host reboot; Jul 30 arbitrage execution failed
  because command-mode dwell collapsed.
- Was briefly pinned `2026.7.4@sha256:5e632aa3…`, then switched to floating
  `:latest` (still resolves to 2026.7.4) — see section above.
- **Compose extras:** `TZ`, `SIGENERGY2MQTT_MQTT_BROKER=mosquitto`,
  `SIGENERGY2MQTT_MQTT_ANONYMOUS=true` (persistence MQTT connects before YAML
  load; without the env broker it hits `127.0.0.1` and marks health degraded).
- **Config:** `modbus[].log-skipped: true` for transport verification.
- **Remote backup:** `/home/halil/workspace/energy-stack-backups/sigenergy2mqtt-20260730-211503/`.
- Still a **partial** mitigation of Sigenergy stale-frame behavior (#207 can
  still occur). Watch overnight command-mode dwell; set `log-skipped: false`
  once quiet if log noise is unwanted. Do not change `soc_final` first.

## Jul 30 low peak SOC / no arbitrage — execution failure diagnosed, transport cause narrowed

- Jul 30 peak SOC was **39.4% at 13:45 CEST**, versus roughly 94–97% on
  Jul 23–28. The MPC was healthy and planned multi-kW grid charging; HA master,
  freshness, control, optimization, and recovery gates were all correct.
- Execution failed below HA policy: all 105 command-mode intervals lasted at
  most about two seconds, and the plant spent 99.1% of the day in native
  `Maximum Self-consumption`. At 12:00:43, for example, HA requested Command
  Charging and 5.4/0.0 kW limits, but the mode returned to native in 128 ms and
  limits returned to 8.8/9.0 kW.
- Live `ha_sigen_ems.yaml` matches the rendered repository package. Remote EMS
  register 40029 remained enabled, `optim_status` stayed `Optimal`, and no
  charge-branch HA path calls `sigen_restore_native_control`. This excludes
  `soc_final`, deadband/native policy, package drift, master-off enforcement,
  recovery, and bad/stale-plan fallback as the cause of today's miss.
- Root transport: Sigenergy can resend the preceding Modbus response (issue
  #100). Old bridge on PyModbus 3.13.0 could discard the valid frame when a
  stale/wrong-ID reply shared the TCP buffer (fixed in 3.13.1 / PR #2934).
- Upgraded to 2026.7.4 as the planned controlled pin (see above).

## Bounded Load-Gap Degraded Mode — DEPLOYED (2026-07-29); image later floated

- Originally pinned `emhass:v0.17.5@sha256:0aa7c906…` with contract probe
  `EMHASS_LOAD_GAP_CONTRACT_OK version=0.17.5`. Live image is now floating
  `emhass:latest` → **v0.17.9** (2026-07-30); see floating-latest section.
- **Local Verification (at 0.17.5 pin):** 156 unit tests, 8/8 matrix OK.
- **Deployment (0.17.5):** `./scripts/deploy-energy-stack.sh` at
  `2026-07-29 22:44 CEST`. Backup:
  `/home/halil/energy-stack-backups/load-gap-20260729-224155`.
- **Synthetic Geometry:** Missing 1-6 bin degraded geometry and 7+/multiple/edge/stale unsafe fail-closed rules verified via synthetic test fixtures.

## MPC notifications storm (2026-07-29) — root cause identified (read-only)

Energy host **rebooted 04:04 CEST**. `sensor.emhass_house_load_sample_w`
recorder gap ~**04:03–05:48 CEST** (~105 min). Fail-closed native-load gate
has aborted every MPC since with missing bins **04:30** and **05:00**.

- Last success marker: `2026-07-29 03:30:42`
- `mpc.ok=false`, `failure_kind=load_history` (still at 19:00)
- Alerts: **B2_mpc** (2h cooldown), **B3**, **B5_cache**, HA **A5/D1**
- Master **on**; control blocked `plan_stale`; Sigen already native
  self-consumption `8.8`/`9.0` kW; SOC ~80%
- Natural recovery when those bins leave the 32-bin window (~**21:00–21:30
  CEST**). No code change required for self-heal; fabricating load is
  forbidden.

## Native naive load cutover — deployed; `:00` + `:30` verified; 24h open (2026-07-27)

- HA `sensor.emhass_house_load_sample_w` is deployed and healthy at one-minute
  cadence with `W`, `power`, and `measurement` metadata. Configuration-only HA
  backup:
  `/home/halil/homeassistant/config/backups/energy-stack-20260726-230722/`.
- Energy cutover deployed native `naive` load history from that sampled sensor.
  Cron no longer sends a load list, `alpha`, `beta`, or a 300 W fill.
  `set_zero_min` is false, so genuine `0 W` samples remain valid.
- The fail-closed history gate uses UTC instants, one immutable HA credential
  snapshot, narrow external-boundary errors, a five-minute freshness limit,
  and all 32 consecutive real half-hour bins. It makes no history retry and
  has no fixed, dynamic, or cached load fallback.
- The first natural `22:00` run exposed recorder visibility lag: its
  `22:00:00.129` sample was not yet visible at `22:00:02`, so the gate safely
  aborted before optimization and left the successful marker unchanged.
  The reviewed fix queries one extra bin and anchors required coverage to the
  newest fresh UTC sample. This preserves all 32 bins while avoiding a
  brand-new-bin race.
- Manual MPC after the boundary fix was `Optimal` and completed at `22:18:44`.
  Untouched natural `22:30` passed (32/32 via `22:29` sample; marker
  `22:30:44`). Untouched natural `23:00` also passed: started `23:00:00`,
  newest sample `22:59:00.128`, history `32/32`, `Optimal`, publish OK,
  `job succeeded` `23:00:43`, `mpc.ok=true` at `23:00:42`, HA successful
  marker `2026-07-27 23:00:42`, master/freshness/control `on`/`ok`. No NaN
  or `get_mix_forecast` traceback.
- Original pre-cutover rollback backup:
  `/home/halil/workspace/energy-stack/backups/native-load-20260727-215554/`.
  Pre-boundary-fix native-load backup:
  `/home/halil/workspace/energy-stack/backups/native-load-boundary-20260727-221538/`.
- Still pending before marking the cutover fully verified: one retrospective
  24-hour audit after **2026-07-28 22:31 CEST** (success/failure count, NaN
  tracebacks, history coverage, stale-plan/native-control transitions). Do
  not wait interactively. Handoff:
  `memory-bank/notebook/native-load-cutover-handoff-2026-07-27.md`.

## B2_mpc NaN failures (2026-07-26) — SUPERSEDED by native-load cutover

Root cause was the tariff-placeholder + alpha/beta mix path. Native
`sensor.emhass_house_load_sample_w` + no alpha/beta payload removes that
crash mechanism. Keep the analysis for history; do not re-apply the old
placeholder/mix mitigations unless the cutover is rolled back.

## Net electricity profit + ApexCharts import forecast (2026-07-23) — DEPLOYED

- Profit sensors on Prices card (production − delivery).
- Import price forecast chart reads **Zonneplan**
  `sensor.zonneplan_current_electricity_tariff` `forecast` (full day-ahead),
  not EMHASS `unit_load_cost_forecasts` (MPC-capped). Span tracks the last
  Zonneplan hourly slot; `extend_to: false`.

## EMHASS idle self-consumption (2026-07-24) — DEPLOYED + VERIFIED

- True idle/deadband now always selects Sigenergy native
  `Maximum Self-consumption (Default)` with confirmed operator/device-valid
  limits, including at or below the configured SOC floor.
- The floor cutout covers only the two explicit command-discharge modes. It
  still preempts stale discharge writers and applies Standby/zero limits; if a
  simultaneous plan update has become idle, it restores native control after
  the hard stop. Recovery charging remains unchanged.
- Deployed to the configured Odroid HA Container with a successful render,
  container config check, and restart. Backup:
  `/home/halil/homeassistant/config/backups/energy-stack-20260724-100444/`.
- Live verification at `p_batt_forecast=0 W`, threshold `500 W`, and SOC
  `5.1%`: master/control remained **on**, recovery **off**, mode confirmed
  native, charge/discharge limits restored to `8.8/9.0 kW`, battery absorbed
  about `695 W`, and P1 grid flow was approximately `−6 W` (near-zero export).

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

**Load forecast** — native `naive` from `sensor.emhass_house_load_sample_w`
(cutover 2026-07-27). Old 300 W fill / tariff placeholder retired.

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
