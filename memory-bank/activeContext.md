# Active Context

## EMHASS load-sample template fix — HA DEPLOYED; MPC history backfill pending (2026-09-04)

- Deployed the direct `states[entity_id]` freshness lookup fix to HA.
- HA config check passed and the container restarted cleanly.
- Backup: `/home/halil/homeassistant/config/backups/energy-stack-20260904-225102/`
- Live sensor: `load_quality=valid`, `invalid_reason=None`, `raw_balance_w=402`,
  `last_valid_at` populated.
- Live `/api/template` tests: load-sample 1/1 passed; Envoy gate 12/12 passed.
- Manual MPC fetched 51 prices but failed closed after two load-history checks:
  1/32 bins covered, 31 missing, `failure_kind=load_history`. No stale or
  synthetic history was written.
- Next action: allow the new valid samples to build the required 32-bin native
  history, then run a manual MPC and confirm `mpc.ok=true`.

## Follow-up live check — sample valid; old 13-bin gap still blocks MPC (2026-09-05)

- `sensor.emhass_house_load_sample_w`: `593 W`, `load_quality=valid`,
  `invalid_reason=None`, `sampled_at=2026-09-05T07:41:00+02:00`.
- `binary_sensor.emhass_house_load_valid=on`.
- `binary_sensor.emhass_mpc_fresh=off` (`reason=plan_missing`),
  `binary_sensor.emhass_plan_valid=off`, and
  `binary_sensor.emhass_control_allowed=off`.
- Latest MPC marker: `mpc.ok=false`; 19/32 bins covered, 13 missing from
  2026-09-04 14:00–20:00 UTC. The gap is too large for degraded mode.
- Containers are up and EMHASS is healthy. Do not bypass the gate or write
  synthetic history; the gap should leave the 32-bin window after about 14:00
  CEST if valid samples continue.

## Temporary MPC horizon 12 — MPC RECOVERED; restoration required (2026-09-05)

- Remote `/home/halil/workspace/energy-stack/emhass/share/config.json` was
  changed from `prediction_horizon: 32` to `12` with an atomic update.
- Backup: `/home/halil/workspace/energy-stack/emhass/share/config.json.bak-load-gap-20260905-075445`
- Manual MPC succeeded at `2026-09-05T07:55:43+02:00`: `mpc.ok=true`,
  healthy load history `12/12`, weather refresh, optimization, and publish
  all succeeded. HA now reports `emhass_mpc_fresh=on` and
  `emhass_plan_valid=on`.
- **Follow-up:** restore remote `prediction_horizon` to `32` after the native
  history contains the full 32-bin window. Verify one full-horizon MPC run
  with `mpc.ok=true`; keep the backup until that check passes.
- Follow-up live check: the 08:00 CEST scheduled run succeeded with
  `mpc.ok=true`, `prediction_horizon=12`, and `12/12` healthy bins. HA reports
  `emhass_mpc_fresh=on` and `emhass_plan_valid=on`; the current EMHASS output
  contains 12 plan rows, so the day-ahead view is intentionally incomplete.

## Low-SOC self-consumption restoration fix — HA DEPLOYED & VERIFIED (2026-09-05)

- Root cause diagnosed: `emhass_soc_floor_cutout` previously commanded `mode: idle`
  (`Standby`) and zero limits (`sigen_apply_idle_limits`), and only attempted native
  restoration if `p_batt_forecast` was already inside the deadband. Because the cutout
  only triggers during active command discharge, this check always evaluated false.
  Subsequently, `binary_sensor.emhass_control_allowed=off` (`soc_below_floor`) blocked
  all scheduled plan applications, trapping the battery in `Standby` with 0 kW limits.
- Fix applied to repo and committed (`cb0eb5e`):
  - `emhass_soc_floor_cutout`: preempts active discharge writers then immediately calls
    `script.sigen_restore_native_control` (`reason: soc_floor`), restoring native
    `Maximum Self-consumption (Default)` and limits (8.8/9.0 kW) unconditionally.
  - `sigen_apply_emhass_plan`: default fallback now calls `script.sigen_restore_native_control`
    (`reason: idle`) instead of dropping to Standby with 0 kW limits.
  - `emhass_sigen_disable_on_stale_plan`: removed low-SOC branch forcing Standby;
    calls `script.sigen_restore_native_control` (`reason: stale_plan`) unconditionally.
  - Spec updated: `docs/superpowers/specs/2026-07-22-emhass-idle-self-consumption-design.md`.
  - Regression tests in `scripts/tests/test_sigen_ems_package.py`: 45/45 tests passing.
- Deployed to Home Assistant via `./scripts/deploy-home-assistant.sh`:
  - Backup: `/home/halil/homeassistant/config/backups/energy-stack-20260905-091506/`
  - HA config check: PASS (`Testing configuration at /config`)
  - Container restarted cleanly (`Container homeassistant Started`)
- Live verification:
  - `select.sigen_0_plant_remote_ems_control_mode`: `Maximum Self-consumption (Default)`
  - Limits confirmed: charge `8.8 kW` / discharge `9.0 kW`
  - SOC: `3.0%` (safe in native self-consumption without standby lockout)
  - Master helper remains `on`.

**Last updated:** 2026-09-05
**Author:** Composer


## EMHASS Envoy forecast gate — HA DEPLOYED; sunrise pending (2026-08-30)

Branch `feature/emhass-envoy-forecast-gate` (Tasks 1–3 in working tree, **no
commit**). Shared `binary_sensor.emhass_envoy_required` + consumer PV /
house-load / C1 templates deployed to Odroid HA.

| Item | Value |
|------|-------|
| Deploy time | 2026-08-30 ~22:52 CEST |
| Backup | `/home/halil/homeassistant/config/backups/energy-stack-20260830-225238/` |
| Config check | PASS (`hass --script check_config`); container restart clean |
| Render | `emhass_envoy_required` + live Envoy `sensor.envoy_122043000054_current_power_production` |

**Live smoke (night, elev ≈ −19°):** PASS

- `binary_sensor.emhass_envoy_required` = `off`, `gate_reason=skip_night`
- `sensor.emhass_house_load_sample_w` available; `sampled_at` advanced
  (22:53 → 22:55 CEST)
- `sensor.p_pv_forecast` = `0.00` W (current 30-minute plan step)
- `binary_sensor.emhass_mpc_fresh` = `on` (`reason=ok`)

**Sunrise acceptance:** **PENDING next dawn** (next_rising ≈ 2026-08-31
04:49 UTC / ~06:50 CEST). Do not wait overnight.

Checklist for next clear dawn:

| Check | Pass criteria |
|-------|----------------|
| Through sunrise + 30 min while forecast &lt;150 or MPC not fresh | `gate_reason` in `skip_dawn_grace` / `skip_low_forecast` / `skip_mpc_stale`; house-load `sampled_at` keeps advancing; no MPC abort solely from Envoy silence |
| After grace with forecast ≥150 and `emhass_mpc_fresh=on` | `gate_reason=require_fresh`; if Envoy still age&gt;20 min, PV/house-load unavailable and C1 on |
| Missing `next_rising` | Not required live (covered by template tests) |

Report: `.superpowers/sdd/task-4-report.md`

## HA Core 2026.8.3 — DEPLOYED (2026-08-23)

Live pin `ghcr.io/home-assistant/home-assistant:2026.8.3`. WUD :3001 clear
for homeassistant. Post-check Envoy PV + control gates after recreate.

## Energy WUD cleanup — mqtt4dsmr v0.9.8 + tag filters (2026-08-21)

On `feature/ha-core-version-pin`: bumped `mqtt4dsmr` `v0.9.7` → `v0.9.8`
(graceful exit / no-MBus crash / rate-limit fixes). Added `wud.tag.include` on
energy-host images so alphas (e.g. sigenergy `2026.8.9a3`) do not flag updates.
WUD :3000 reports `updateAvailable=0` after recreate.

## HA Core version pin — DEPLOYED (2026-08-21)

Branch `feature/ha-core-version-pin`. HA Container image
`ghcr.io/home-assistant/home-assistant:2026.8.2` (was floating `:stable` /
running `2026.8.1`). Patch changelog only. Label
`wud.tag.include=^\d+\.\d+\.\d+$` so WUD ignores `.dev` tags. WUD on :3001
reports `updateAvailable=false` for homeassistant. Note:
`deploy-home-assistant.sh` only restarts HA; image bumps need
`compose pull` + `up -d --force-recreate` (done manually this time).

## Docker image version pins — DEPLOYED + merged to main (2026-08-21)

Merged `feature/docker-image-version-pins` → `main` (`5e6c922`) and pushed.
Energy host pins verified; manual MPC after deploy recreate recovered
(`mpc.ok=true` @ 22:03 CEST; 22:00 fail was recreate race on weather clear).

| Service | Pin |
|---------|-----|
| `emhass` | `ghcr.io/davidusb-geek/emhass:v0.18.1` |
| `sigenergy2mqtt` | `ghcr.io/seud0nym/sigenergy2mqtt:2026.8.8` |
| `mosquitto` | `eclipse-mosquitto:2.1.2-alpine` |
| `mqtt4dsmr` | `ghcr.io/antonijn/mqtt4dsmr:v0.9.7` |

## EMHASS daily planned profit — repo-ready, not deployed (2026-08-19)

Branch `feature/emhass-daily-planned-profit` implements covered-slot planned
profit lock after successful MPC:

- One Zonneplan price snapshot per MPC (`PriceSnapshot` in `fetch_prices_emhass.py`).
- Write-once `/share/daily_planned_profit.json`; HA helper
  `input_text.emhass_planned_profit_payload`; template
  `sensor.emhass_planned_profit_today`.
- Trading dashboard: planned today + 7-day planned vs realized graph.
- Monitor: one `notify.notify` after 22:00 if tomorrow lock missing.
- Spec: `docs/superpowers/specs/2026-08-16-emhass-daily-planned-profit-design.md`;
  plan: `docs/superpowers/plans/2026-08-17-emhass-daily-planned-profit.md`.

Deploy needs operator approval (`deploy-energy-stack.sh`, then
`deploy-home-assistant.sh`). Commit only planned-profit feature files; do not
include unrelated dirty-tree items (caveman skills, `tmp_emhass_now.py`,
`deploy-all.sh`).

Corrupt `/share/daily_planned_profit.json` is fail-closed: cron does not
overwrite it. Successful MPC no-op runs write `planned_profit.ok=true` so a
prior auxiliary error does not stick. After midnight the HA sensor stays
`unknown` until the first successful MPC of that date republishes the helper.

## Trading Status — drop plan problem (2026-08-12) — DEPLOYED

Removed `binary_sensor.emhass_plan_problem`. Kept
`binary_sensor.emhass_plan_valid`. HA deploy
`energy-stack-20260812-230725`; orphan removed from entity registry
(HTTP 404 on state).

## Trading Prices card — Zonneplan tariff group (2026-08-12) — DEPLOYED

Same HA deploy. Prices card shows
`sensor.zonneplan_current_tariff_group` (label **Tariff group**).
`sensor.emhass_price_rank_now` template still in package (unused by card).

## Temporary prediction_horizon 12 — RESTORED to 32 (2026-08-11 ~12:26 CEST)

Envoy outage recovery complete. Trading/MPC healthy on horizon 12; load
history now **32/32** healthy. Live `prediction_horizon` **12 → 32** on
energy host + local gitignored copy. Backup:
`config.json.bak-horizon32-*`. Next natural MPC uses full 16 h horizon.

## Plan vs battery overlay sign align — DEPLOYED then revised (2026-08-09)

Trading overlay looked inverse: Sigen `plant_battery_power` (+charge/−discharge)
vs EMHASS `p_batt_forecast` (−charge/+discharge). First fix used a new template
sensor (lost recorder history). Revised: ApexCharts `invert: true` on the live
Sigen entity so signs align and 24 h history stays. Status card still shows raw
Sigen power.

## EMHASS actionable alert escalation — committed, not deployed (2026-08-09)

Branch `feature/emhass-actionable-alert-escalation`: phone only after
operator-action deadlines; detection/intentional/recovery stay Logbook-only.

- HA: `script.emhass_log_event` + `input_text.emhass_alert_event`; C/E 30 min;
  D3 2h/6h; D2 reason/phase with `emhass_d2_actionable_notified` phone marker;
  C2a/E1 ownership suppress vs B6/plan-stale/D2 actionable.
- Monitor: B2–B6 detect → log, phone after 60 min; B6 ownership helper with
  pending retry; hard A4/A6/B1 stay immediate.
- Dashboard: native EMHASS events Logbook card (48h) on Sigen diagnostics.
- Spec/plan under `docs/superpowers/`; deploy still needs operator approval.

## 7-day net electricity profit graph — repo-ready, corrected post-review (2026-08-01)

**Author:** Auto / Claude

Operator decision **overrode** the original 23:55-snapshot plan/spec after
review. Implemented per revised spec
`docs/superpowers/specs/2026-08-01-emhass-7d-net-profit-graph-design.md` on
branch `feature/emhass-7d-net-profit-graph`:

- **No new sensor.** `sensor.emhass_net_electricity_profit_daily` (23:55
  trigger snapshot) was **removed** — never deployed, repo-only.
- `sensor.emhass_net_electricity_profit_today` (existing, continuous,
  production − delivery) changed `state_class: measurement` →
  **`total`** (not `total_increasing` — net profit can decrease intraday).
- Dashboard `statistics-graph` "Net electricity profit — last 7 days" now
  reads that **today** sensor with `stat_types: [state]` (not `mean`).
  Closed-day bar = last recorded value that calendar day ≈ EOD P/L; today's
  bar = latest value so far = live P/L. `days_to_show: 7`, `period: day`,
  `chart_type: bar` unchanged.
- Month sensor (`sensor.emhass_net_electricity_profit_month`) untouched —
  still `measurement`.

**Not live-verified** until HA deploy. Unlike the rejected snapshot approach,
there is no forward-only/backfill gap concern — the graph reads existing
continuous history the moment `state_class: total` is deployed.

**Operator deploy:**

```bash
source scripts/lib/source-site-env.sh
./scripts/deploy-home-assistant.sh
```

**Post-deploy live checks:**

1. `sensor.emhass_net_electricity_profit_today` has `state_class: total` (Developer Tools → States → Attributes).
2. Trading view shows a live-moving bar for today and, after the next midnight boundary, a closed bar for the prior day at its EOD value.
3. Master/control behavior unchanged (display-only change).

## Sigen diagnostics dashboard — DEPLOYED (2026-08-05 ~22:47 CEST); stale/unavailable check pending operator approval

- Tasks 1–4 complete on `feature/sigen-diagnostics-dashboard`: package
  `ha_sigen_diagnostics.yaml`, dashboard `dashboards/sigen_diagnostics.yaml`,
  merge key `sigen-diagnostics`, deploy uploads wired.
- Operator docs: [SETUP.md](../docs/SETUP.md) § Sigen diagnostics dashboard;
  entity IDs in [ENTITY_CONTRACT.md](../docs/ENTITY_CONTRACT.md) § Sigen diagnostics.
- **Task 6 (live deploy + acceptance):**
  - Preflight: `render-ha-packages.sh` clean; both test modules
    (`test_ha_config_merge`, `test_sigen_diagnostics_dashboard`) — 17/17 passed.
  - `./scripts/deploy-home-assistant.sh` — config check passed, HA Container
    restarted clean. Backup:
    `/home/halil/homeassistant/config/backups/energy-stack-20260805-224449/`.
  - **Acceptance A (freshness, <180s):** PASS — `sigenergy2mqtt_health=healthy`,
    `mqtt_connected=on`, `modbus_connected=on`, all ages ~6–21s post-restart.
  - **Acceptance B (MQTT cross-check):** PASS —
    `mosquitto_sub sigenergy2mqtt/health/state` on `ENERGY_REMOTE_HOST` → `healthy`.
  - **Acceptance C (stale/unavailable after stopping `sigenergy2mqtt`): SKIPPED
    by explicit operator/controller decision** — do not stop/restart
    `sigenergy2mqtt` while master control may be active; interrupting
    Modbus→MQTT without explicit operator approval is unsafe. Needs a
    scheduled maintenance window + operator go-ahead before running.
  - **Acceptance D (entity availability, best-effort):** PASS — all 38 entities
    referenced by the rendered `sigen_diagnostics.yaml` dashboard resolved via
    REST with no `unavailable`/`unknown`/missing states. UI-only checks
    (sidebar entry, temp statistics-graph populated, bridge history-graph
    3 series, simple-entity rows don't toggle EMS, trading dashboard still
    loads) still need an operator visual pass.
  - **Status: DONE_WITH_CONCERNS** — live and healthy; C intentionally not run.

## MPC abort 14:30 CEST (2026-08-05) — load-sample freshness false positive (read-only)

- Containers healthy; prices/weather OK. Only the **14:30** natural MPC failed.
- `last_enever_run.json`: `mpc.ok=false`, `failure_kind=load_history`,
  newest finite sample **10.0 min** old (max 5). Last success marker still
  `2026-08-05 14:00:44`. Master **on**; `emhass_mpc_fresh` /
  `emhass_control_allowed` **off** (`plan_stale`).
- Root: `sensor.emhass_house_load_sample_w` sat at **0 W** for ~11 min
  (`12:20`→`12:31` UTC / `14:20`→`14:31` CEST). HA recorder collapses unchanged
  state, so history `last_changed` did not advance; fail-closed gate treated it
  as stale. Underlying cause of the zero clamp: formula
  `max(grid + pv − batt, 0)` while battery discharged ~**2.7 kW** (prior plan)
  and grid+PV roughly cancelled it — not a P1/HA outage.
- Sample sensor live again by ~14:55 (`sampled_at` current, non-zero). Next
  natural **15:00** MPC should recover if samples keep changing. No code/deploy
  done (read-only ask).

## Open-Meteo soft refresh — DEPLOYED (2026-08-01 ~07:45 CEST)

- Soft clear at `max_age − 2×step` (**90**); hard B5/fail at **150**.
- Live `emhass/share/config.json` on energy host: `open_meteo_cache_max_age=150`.
- Cron container reports hard=150, soft=90. `emhass`/`emhass-cron`/`energy-monitor` rebuilt+restarted.
- Post-recreate weather cache was empty; `refresh-weather` ran to rebuild. Watch next natural MPC.

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
