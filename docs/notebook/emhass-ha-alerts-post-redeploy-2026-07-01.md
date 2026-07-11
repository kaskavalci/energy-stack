# EMHASS HA alerts after codebase move & redeploy

**Date:** 2026-07-01  
**Author:** Composer  
**Scope:** Read-only investigation — notifications after `energy-stack` move and redeploy (~2026-06-30 20:36 CEST).

---

## Context

After moving the repo and redeploying `energy-stack` + HA packages, multiple Companion app notifications appeared. This note records what fired, the exact alert text, root cause, and whether the stack is healthy.

**Hosts:** energy `.135`, HA `.109` (from `config/site.env`).

---

## Executive summary

| Layer | Verdict |
|-------|---------|
| MPC pipeline (prices → CSV → optim → publish) | **Healthy** — `last_enever_run.json` all `ok: true`, cron succeeding every 30 min |
| energy-monitor (`.135` → HA push) | **Not the source** — no `Notified HA` in container logs since redeploy |
| HA `script.emhass_push_alert` automations | **Source of all pushes** — mostly one SOC floor cascade + one dawn PV alert |
| Battery execution | **Paused by design** — SOC 9.4% &lt; 10% reserve; waiting for MPC charge slot for recovery |

The notification burst was **expected safety behavior** (10% floor + gate logic), not a broken MPC or deploy regression.

---

## Redeploy timeline

| Time (CEST) | Event |
|-------------|-------|
| ~20:36 | `emhass-cron` + `energy-monitor` containers recreated |
| ~20:40 | HA packages reloaded (`automation.*` last config ~20:40 UTC / 18:40 in API timestamps) |
| ~20:58 | SOC **9.4%** crosses `emhass_min_soc_reserve` (10%) → floor cascade |
| 21:01 | First post-redeploy MPC succeeds and publishes |
| 05:37 (next day) | C1 Enphase PV alert (sunrise Envoy lag) |

---

## Alerts that actually fired

All via `notify.notify` through `script.emhass_push_alert`. Confirmed from automation `last_triggered` (HA REST API).

### Deploy evening — 2026-06-30 ~20:58–21:03 CEST

SOC was **9.4%** with `input_boolean.emhass_auto_control_master` **on**.

#### 1. EMHASS D3: SOC — 20:58

```
Battery SOC 9.4% with auto control on.
```

- **Automation:** `emhass_notify_d3_soc_extreme`
- **Why:** `binary_sensor.emhass_fail_soc_extreme` → on (SOC ≤ floor with master on). Fires immediately (no `for:` delay).

#### 2. EMHASS D4: SOC floor cutout — 20:58

```
SOC 9.4% hit floor (10%). Discharge stopped. Recovery charging will engage when EMHASS plans charge.
```

- **Automation:** `emhass_soc_floor_cutout_stop_discharge_at_min_soc`
- **Why:** Inverter was discharging; SOC below `input_number.emhass_min_soc_reserve`. Forced **Standby** — correct Layer-2 safety.

#### 3. EMHASS D2: Remote EMS off — 20:59

```
Control gates failed — Remote EMS disabled (master still on; turn off master to acknowledge).
```

- **Automation:** `emhass_sigen_disable_when_plan_stale_with_master_on`
- **Why:** `binary_sensor.emhass_control_allowed` went off for 1 min (SOC below floor). Alias mentions “stale plan” but trigger is **any** gate failure — here SOC, not MPC.

#### 4. EMHASS D1: gates — 21:03

```
Master ON but control not allowed. optim=Optimal, p_batt age=<N> min.
```

- **Automation:** `emhass_notify_d1_control_blocked`
- **Why:** `fail_control_blocked` on for 5 min. Reads like a fault; plan was fine — gates blocked **only because SOC &lt; 10%**.

#### Possible 5th — ~20:40 (package reload)

```
SOC <N>% reached recovery target 15%. Normal MPC control resumed.
```

- **Automation:** `emhass_recovery_complete_notify_and_resume_normal_control`
- **Why:** Likely spurious during HA entity reload (`last_triggered` 20:40:58). SOC was not actually above 15%.

### Next morning — 2026-07-01 ~05:37 CEST

#### 5. EMHASS C1: Enphase PV

```
Envoy PV unavailable or stale (>20 min). Check Enphase integration.
```

- **Automation:** `emhass_notify_c1_pv_sensor`
- **Why:** `fail_pv_sensor` on ~03:22 at sunrise; 15 min `for:` then notify. Cleared ~03:45. Unrelated to redeploy.

---

## Alerts that did **not** fire

| ID | Reason skipped |
|----|----------------|
| A2 optim / A5 stale plan | MPC Optimal; plan refreshed at 21:01 |
| A3 flat prices | Price spread present |
| C2a/C2b/E1/E2 | Sensors briefly unavailable at reload; recovered before `for:` (10–15 min) |
| D5 recovery **charge** | MPC planned **discharge**, not charge — recovery requires `p_batt < -500 W` |
| energy-monitor B2/B3/B4/B5 | All host checks OK since redeploy |

At **20:40** many `binary_sensor.emhass_fail_*` flipped on for seconds during reload; delay conditions prevented a wider alert storm.

---

## SOC model (three knobs)

| Entity | Live value | Role |
|--------|------------|------|
| `input_number.emhass_soc_min` | 5% | EMHASS optimizer floor (`battery_minimum_state_of_charge` via cron) |
| `input_number.emhass_min_soc_reserve` | 10% | HA execution floor: discharge stop, alerts, `control_allowed` |
| `input_number.emhass_recovery_target_soc` | 15% | Recovery charge target; “recovery complete” notify above this |

**Design intent:** MPC may **plan** down to 5%; HA will **not discharge** below 10% and blocks normal control there. Recovery charges toward 15% on the **first MPC charge slot**, not unconditionally.

**Known gap (acknowledged, not fixed):** Optimizer headroom 5–10% can appear in plans but execution will not follow discharge into that band until `emhass_min_soc_reserve` is aligned with `emhass_soc_min`.

---

## Will the system work as designed?

| Component | Status at investigation |
|-----------|-------------------------|
| emhass-cron MPC + publish | Yes — every 30 min |
| energy-monitor | Yes — no false host pushes |
| Normal trading (`sigen_apply_emhass_plan`) | No until SOC ≥ 10% (`control_allowed` on) |
| Recovery path | Yes when MPC schedules charge and SOC &lt; 15% (bypasses SOC gate on `control_allowed`) |
| Current stall | SOC 9.4%, plan discharge (+900 W) → recovery correctly waits |

Path back to normal trading:

1. MPC publishes a **charge** step (`p_batt < -500 W`).
2. `emhass_recovery_charge` applies limits (even with `control_allowed` off).
3. SOC &gt; 10% → `control_allowed` on → scheduled apply resumes.
4. SOC &gt; 15% → D5 “recovery complete” notify.

---

## State snapshot (investigation time)

```
input_number.emhass_soc_min:              5%
input_number.emhass_min_soc_reserve:     10%
input_number.emhass_recovery_target_soc: 15%
sensor.sigen_0_plant_battery_soc:        9.4%  (last_updated stuck since floor event)
binary_sensor.emhass_control_allowed:    off
select.sigen_0_plant_remote_ems_control_mode: Standby
sensor.optim_status:                     Optimal
sensor.p_batt_forecast:                  +900 W (discharge)
```

**Open check:** SOC `last_updated` frozen ~10 h while `battery_power` still updated — verify against Sigen app if gates seem stuck longer than expected.

---

## HA log noise (non-push)

Template errors during reload when `sensor.p_batt_forecast` was briefly missing:

```
UndefinedError: 'None' has no attribute 'last_updated'
  → binary_sensor.emhass_control_allowed (p_batt_age_min)
  → sensor.emhass_plan_schedule (plan_updated_min)
```

Cosmetic during reload; cleared after first publish (~20:43).

Obsolete automations show `unavailable` (`emhass_notify_a1`, `emhass_notify_c2_sigen_sensors`, etc.) — leftover entity IDs from pre-merge packages. Harmless clutter.

---

## References

- HA monitoring: `home-assistant/ha_emhass_monitoring.yaml`
- Sigen EMS + recovery: `home-assistant/ha_sigen_ems.yaml`
- Control gates: `home-assistant/ha_emhass_dashboard.yaml` (`binary_sensor.emhass_control_allowed`)
- Host monitor: `monitor/health_notify.py`
- Prior alert hygiene: `memory-bank/notebook/emhass-alert-hygiene-2026-06.md` (if present on operator machine)
