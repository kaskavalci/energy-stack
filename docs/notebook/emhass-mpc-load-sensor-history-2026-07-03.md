# EMHASS MPC failure — load sensor history 500

**Date:** 2026-07-03  
**Author:** Composer  
**Scope:** Read-only investigation — MPC failing every 30 min since 2026-07-02 ~19:31 CEST.

---

## Executive summary

| Layer | Verdict |
|-------|---------|
| Price fetch (Zonneplan → CSV) | **OK** |
| Weather cache (Open-Meteo) | **OK** |
| `naive-mpc-optim` | **Failing** — HA history HTTP 500 on `sensor.emhass_house_load_power_w` |
| `publish-data` (HA automation) | **OK** — republishes **cached** plan, not a fresh optimization |

MPC has had no successful optimization since **2026-07-02 19:01 CEST**. HA forecast sensors are stale; `sensor.optim_status` last changed **2026-07-01**.

---

## Timeline

| Time (CEST) | Event |
|-------------|-------|
| 2026-07-02 19:01 | Last successful `naive-mpc-optim` (`Optimization completed`) |
| 2026-07-02 ~19:31 | First failure — HA history 500 for `sensor.emhass_house_load_power_w` |
| 2026-07-02 ~17:00 | Sensor likely created (history queries before this time return 500) |
| 2026-07-03 13:30+ | Still failing every cron slot; `last_enever_run.json` → `mpc.ok: false` |

Config on the energy host was probably pointed at `sensor.emhass_house_load_power_w` between the 19:01 success and 19:31 failure.

---

## Root cause

Live `emhass/share/config.json` references the new HA template sensor for EMHASS data retrieval:

- `sensor_power_load_no_var_loads` → `sensor.emhass_house_load_power_w`
- `var_model` → `sensor.emhass_house_load_power_w`
- `sensor_linear_interp` includes it

During `naive-mpc-optim`, EMHASS calls the HA history API for configured load sensors — **even when** `emhass-cron` passes a constant 300 W `load_power_forecast` (`alpha=1.0`, `beta=0.0`).

EMHASS requests:

```text
GET /api/history/period/2026-07-02T00:00:00Z?filter_entity_id=sensor.emhass_house_load_power_w
```

HA returns **HTTP 500** when the query window includes time before the sensor existed (~17:00 on 2026-07-02). Other sensors (`emhass_pv_power_w`, P1 net, battery power) return 200 for the same pattern.

| History query start | HTTP |
|---------------------|------|
| `2026-07-02T00:00:00Z` (EMHASS default) | **500** |
| `2026-07-02T17:00:00Z` onward | 200 |
| `2026-07-03T00:00:00Z` | 200 |
| Current state API | 200 |

EMHASS surfaces this as HTTP 400 on `naive-mpc-optim`; the MPC chain aborts before `publish-data`.

---

## What is not broken

- Zonneplan price slots and CSV writes (`prices.ok: true` in `last_enever_run.json`)
- Open-Meteo weather cache geometry
- EMHASS container health
- HA `publish-data` REST automation (replays last good optimization payload)

Monitor alert **B2_mpc** is correctly firing.

---

## Potential solutions

### 1. Revert load sensor in `config.json` (recommended, fast)

Point EMHASS history keys to a sensor with full recorder history and a non-NaN `_positive` column in EMHASS mix forecast:

- **Working (2026-07-03 recovery):** `sensor.electricity_meter_power_consumption`
- **History OK but MPC NaN:** `sensor.sigen_0_consumed_power` — HA history returns 200, but EMHASS `get_mix_forecast` fails with `ValueError: cannot convert float NaN to integer` on `sensor.sigen_0_consumed_power_positive`
- **History 500:** `sensor.emhass_house_load_power_w` — do not use in `config.json`

Keys to change:

- `sensor_power_load_no_var_loads`
- `var_model`
- Remove `sensor.emhass_house_load_power_w` from `sensor_linear_interp`

Keep `sensor.emhass_house_load_power_w` in HA for dashboards/observability only.

**After any `config.json` sensor change:** delete EMHASS `/data/params.pkl` (or restart `emhass` container) — stale pickle keeps old sensor refs even when `config.json` is updated.

Deploy: edit live `config.json` on energy host; update `config.json.example` + push via `./scripts/deploy-energy-stack.sh`.

### 2. Wait for self-heal (low confidence)

When EMHASS history window shifts to a full day where the sensor existed (e.g. query from `2026-07-03T00:00:00Z`), the API returns 200 today. MPC *might* recover at the next UTC day boundary. Fragile — any sensor recreation repeats the failure.

### 3. Fix HA recorder behavior

Investigate why `/api/history/period` returns 500 for template sensors when the range predates entity creation. May need HA upgrade (live: 2026.5.3), recorder purge/repair, or a core bug report.

### 4. Decouple observability from EMHASS config (longer term)

Document that `sensor.emhass_house_load_power_w` must not be wired into `sensor_power_load_no_var_loads` / `var_model` until EMHASS skips history fetch when `load_power_forecast` is supplied at runtime. Consider a deploy check or comment in `config.json.example`.

---

## Verification after fix

```bash
ssh "${ENERGY_REMOTE_HOST}" 'cd "${ENERGY_REMOTE_DIR}" && \
  docker compose exec -T emhass-cron python3 /app/fetch_prices_emhass.py --action mpc -v'
```

Expect: `Optimization completed`, `last_enever_run.json` → `mpc.ok: true`, fresh `sensor.p_batt_forecast` / `sensor.soc_batt_forecast` slots.

---

## Recovery applied (2026-07-03 ~15:00 CEST)

| Step | Result |
|------|--------|
| Safety check | `emhass_control_allowed` was `off` (plan_stale); Sigen EMS = Maximum Self-consumption |
| Live `config.json` | `sensor.electricity_meter_power_consumption` for load keys (not `sigen_0_consumed_power` — NaN in mix; not `emhass_house_load_power_w`) |
| `/data/params.pkl` | Deleted + `emhass` restart (required — stale pickle kept old sensor refs) |
| `secrets_emhass.yaml` | `hass_url` trailing slash added |
| Manual MPC | `Optimization completed`; `mpc.ok: true` at 15:00:15 |
| Post-recovery | `emhass_control_allowed` → `on` (fresh plan, SOC 100%) |

Scheduled `:00` cron overlapped manual run; monitor `:30` for unattended success.


- `emhass/share/config.json.example` — load sensor keys
- `home-assistant/ha_emhass_sensors.yaml` — `sensor.emhass_house_load_power_w` template
- `emhass-cron/fetch_prices_emhass.py` — constant 300 W `load_power_forecast` payload
- `memory-bank/activeContext.md` — load forecast design note
