# Progress

*Last updated: 2026-07-11 (afternoon CEST)*  
*Author: Auto*

## Completed

- [x] EMHASS + Zonneplan + Sigen trading stack (energy host + HA host)
- [x] Alert hygiene (B5 gate, 120 min cache age)
- [x] Constant 300 W load forecast in cron MPC
- [x] C2 night Envoy false-alert fix (HA templates)
- [x] Additive HA config merge (`scripts/lib/ha_config_merge.py`)
- [x] Repo split from reterminal monorepo (2026-06-30)
- [x] MPC recovery after load-sensor history 500 + publish URL slash fix (2026-07-11 morning; regressed same afternoon ~07:30 CEST)
- [x] MPC recovery after PV history 500 — host placeholder `sensor.p_pv_forecast` (2026-07-11 ~13:28 CEST; `mpc.ok: true`)
- [x] P0 repo: `config.json.example` placeholders, alpha/beta doc reconciliation, `fetch_current_load` unit tests (2026-07-11)

## Known issues

- SOC execution headroom not implemented (`memory-bank/notebook/emhass-soc-min-trading.md`)
- `emhass_control_allowed` freshness gating not implemented
- Package keys still prefixed `reterminal_emhass_*` on live HA (rename deferred)
- HA recorder corrupted hours break full-day history: Jul 10 **14 UTC** (P1), Jul 11 **07 UTC** (PV/envoy/P1). Zonneplan tariff and `sensor.p_pv_forecast` unaffected.
- Jul 12 P1 revert assumption was wrong — Jul 11 full-day P1 history still HTTP 500 (bad hour 07 UTC). Date rollover alone does not unblock P1 revert.
- `sensor.sigen_0_plant_battery_soc` stale since 2026-07-10 23:00 UTC (sigenergy2mqtt; separate from MPC)
- EMHASS rejects identical load and PV sensor keys — cannot use Zonneplan tariff for both placeholders

## What needs to be done, when

### NOW — host ops (done 2026-07-11 ~13:28 CEST)

| Step | Action | Result |
|------|--------|--------|
| 1 | Load keys → `sensor.zonneplan_current_electricity_tariff` | Already active |
| 2 | PV keys → `sensor.p_pv_forecast` (not Zonneplan — EMHASS constraint) | Applied |
| 3 | `rm -f /data/params.pkl` + restart emhass | Done |
| 4 | Manual MPC | `mpc.ok: true` at 13:28:13 CEST |

**Proper underlying fix (HA host, not energy host):** repair HA recorder for Jul 11 07 UTC and Jul 10 14 UTC. Until then, placeholders required for any sensor whose full-day history returns 500.

### Before push / deploy — P0 merge blockers (repo)

- [x] **`emhass/share/config.json.example`:** load + PV placeholders with revert comments
- [x] **Reconcile alpha/beta docs:** `emhass/README.md` + design spec footnote
- [x] **`fetch_current_load` unit tests** in `emhass-cron/tests/`

### Before / at deploy — P1

- [ ] **`emhass_recovery_complete` guard** — ensure recovery path does not apply stale MPC output after sensor/config changes
- [ ] **SOC floor migration notes** — document 10% → 5% floor change for operators and HA templates
- [ ] **`block_reason` vs policy alignment** — `memory-bank/notebook/emhass-actionable-alerts-policy.md` must match live alert templates and monitor logic
- [ ] Run `./scripts/render-ha-packages.sh` + `test_ha_config_merge` (or equivalent merge test) before `deploy-home-assistant.sh`

### After HA recorder repair — revert placeholders

Only after full-day history HTTP 200 for affected sensors on Jul 11 (and P1 on Jul 10/11 as needed).

| Step | Action |
|------|--------|
| 1 | Revert load keys to `sensor.electricity_meter_power_consumption` |
| 2 | Revert PV keys to `sensor.emhass_pv_power_w` |
| 3 | Delete `/data/params.pkl`, restart emhass |
| 4 | Run manual MPC (`fetch_prices_emhass.py --action mpc -v`); verify `mpc.ok` |
| 5 | Monitor 2+ cron slots (30 min) before considering stable |

### P2 / later (not blocking deploy)

- [ ] `binary_sensor.emhass_fail_recovery_stalled` — detect when recovery retries exhaust without success
- [ ] Stale battery SOC (`sensor.sigen_0_plant_battery_soc`) — sigenergy2mqtt investigation
- [ ] Security minors from `.bug-hunter` scan
- [ ] SOC apply-time clamp (`memory-bank/notebook/emhass-soc-min-trading.md`)
- [ ] Freshness gating before EMS apply (`emhass_control_allowed`)
- [ ] Remove alpha/beta from MPC payload per 2026-07-05 safe-blending design
- [ ] Optional coordinated release tags with reterminal repo

### Ongoing

- Monitor B2_mpc notifications on energy host
- Verify HA history API for P1 and PV sensors before any placeholder revert
- Live `config.json` may differ from git — confirm via `ENERGY_REMOTE_HOST` / `ENERGY_REMOTE_DIR` in `config/site.env`

## Roadmap (unchanged long-term)

- [ ] SOC apply-time clamp
- [ ] Freshness gating before EMS apply
- [ ] Optional coordinated release tags with reterminal repo
