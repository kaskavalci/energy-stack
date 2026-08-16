# EMHASS daily planned profit (tomorrow lock)

**Date:** 2026-08-16  
**Author:** Composer

## Goal

Show **planned** vs **realized** net electricity profit per calendar day
(`Europe/Amsterdam`), on the trading dashboard: today numbers plus a 7-day
planned-vs-realized chart.

Realized already exists. Planned does not: `sensor.total_cost_fun_value` is a
rolling horizon objective, not a calendar-day plan.

## Decisions (from brainstorm)

| Topic | Choice |
|-------|--------|
| Approach | Cron lock after successful MPC (not HA-only templates) |
| Lock target | **Tomorrow’s** calendar day (not remaining today) |
| Lock trigger | Dynamic: first time Zonneplan forecast has ≥1 tomorrow slot |
| Metric | Sum of EMHASS `cost_profit` for slots dated that calendar day |
| Missed lock | Leave planned `unknown`; one HA `notify.notify` (no Telegram) |
| UI | Planned today + realized today; 7-day dual bar chart |

Rejected simpler alternatives (kept for context): next-24h lock; live
`total_cost_fun_value` only without a frozen day plan.

## Why lock tomorrow (not today)

Day-ahead prices usually appear in the afternoon. MPC then plans overnight
actions (e.g. discharge at 03:30) on **tomorrow’s** calendar date.

- Locking **remaining today** at afternoon misses that overnight window.
- Locking **today** the next afternoon is too late: 03:30 already passed.

So: when tomorrow’s prices first appear on day **D**, freeze planned profit for
**D+1** from that plan (all D+1 slots). On D+1, “planned today” is the value
locked on D.

Evidence (this site, ~7 days of `emhass-cron` logs): day-ahead slot jump is
usually at the **14:00** MPC (sensor `last_updated` ~14:00 CEST); sometimes
earlier (~13:24); rarely late (2026-08-16 ~16:16). Online EPEX publish is
~13:00–14:00; HACS `zonneplan_one` polls prices every **15 minutes**. Do not
use a fixed clock as the lock condition.

## Formula

### Planned (locked)

```text
planned(D+1) = sum(cost_profit for opt_res rows whose local date == D+1)
```

Source: `opt_res_latest.csv` after a successful MPC. Time zone:
`Europe/Amsterdam`. Round to 2 decimal places (EUR).

### Realized (existing)

```text
realized(day) = production_costs − delivery_costs
```

Entity: `sensor.emhass_net_electricity_profit_today` (Zonneplan, incl. tax).
Unchanged by this design.

## Architecture

```text
Zonneplan forecast (HA) ──► emhass-cron MPC (success)
                                │
                                ├─ tomorrow slots present?
                                │         │
                                │        yes + D+1 not locked
                                │         │
                                ▼         ▼
                    opt_res_latest.csv → sum cost_profit (date == D+1)
                                │
                                ├─ /share/daily_planned_profit.json
                                └─ publish HA planned-today sensor (for *today*)

energy-monitor (periodic)
        └─ if after alert floor and tomorrow still unlocked
              → one notify.notify via HA
```

| Piece | Role |
|-------|------|
| `emhass-cron` | Detect lock; compute; persist; publish sensor state |
| `/share/daily_planned_profit.json` | Source of truth per calendar date |
| HA sensor + recorder | Dashboard + 7-day statistics |
| `energy-monitor` | Missed-lock alert only |
| Realized sensor | Unchanged |

## Persistence contract

File: `emhass/share/daily_planned_profit.json` (on energy host `/share`).

```json
{
  "version": 1,
  "days": {
    "2026-08-17": {
      "planned_eur": 3.45,
      "locked_at": "2026-08-16T14:00:52+02:00",
      "mpc_at": "2026-08-16T14:00:52+02:00",
      "slot_count": 48,
      "locked_on_date": "2026-08-16"
    }
  }
}
```

Rules:

- Key = calendar date of the **planned day** (D+1), ISO `YYYY-MM-DD`.
- Write once; never overwrite an existing date key.
- Retain at least 14 days of keys (prune older on write).
- Gitignore live file; do not commit secrets or live share snapshots.

## Lock trigger (dynamic)

After each **successful** MPC:

1. Read Zonneplan forecast used for that run (same source as price CSV).
2. If any forecast slot local date is **tomorrow** relative to MPC local now,
   and JSON has no key for tomorrow → lock.
3. Else → no-op.

No fixed clock (13:00 / 14:00 / 15:00 / 16:30) as the condition. Clock may
only bound the **missed-lock alert**, not the lock itself.

## HA sensors

| Entity | Meaning |
|--------|---------|
| `sensor.emhass_planned_profit_today` | Planned € for **today’s** date from JSON; `unknown` if missing |
| attrs | `locked_at`, `plan_date`, `slot_count`, `mpc_at`, `locked_on_date` |

Sensor contract:

- `unit_of_measurement`: `EUR`
- `device_class`: `monetary`
- `state_class`: `total` (constant within the day; may jump at midnight to the
  next day’s pre-locked value or to `unknown`)
- Round state to 2 decimal places when numeric

Publish path: cron POSTs state after lock and on each successful MPC (refresh
“today” from JSON so midnight rollover does not depend on HA templates alone).

Optional later (out of scope): `sensor.emhass_planned_profit_tomorrow` for
visibility right after lock.

## Dashboard

In `home-assistant/dashboards/emhass_trading.yaml`:

1. Prices card: add **Planned today**
   (`sensor.emhass_planned_profit_today`) next to **Net profit today**.
2. Statistics graph (7 days, `period: day`, `chart_type: bar`,
   `stat_types: [state]`):
   - `sensor.emhass_planned_profit_today` — name: Planned (EUR)
   - `sensor.emhass_net_electricity_profit_today` — name: Realized (EUR)

Reuse the existing 7-day realized graph pattern from
`2026-08-01-emhass-7d-net-profit-graph-design.md` (continuous sensor +
`state_class: total` + `stat_types: [state]`). Extend to two series; do not add
a 23:55 snapshot sensor.

## Missed-lock alert

| Field | Value |
|-------|--------|
| Owner | `energy-monitor` (`health_notify.py`) |
| Channel | HA `notify.notify` only (same as existing monitor alerts; **not** Telegram) |
| Condition | Local time ≥ **22:00** Europe/Amsterdam, and tomorrow’s date has no JSON lock |
| Frequency | Once per calendar evening (signature = date of missing tomorrow) |
| Message | Day-ahead / tomorrow prices did not appear; planned profit for tomorrow not locked |

If prices appear after 22:00 but before midnight, lock on next successful MPC;
do not require a second alert that night if lock succeeds (clear pending
signature on lock).

## Edge cases

| Case | Behavior |
|------|----------|
| MPC fails when tomorrow first visible | Retry on next successful MPC; still unlocked |
| Tomorrow already locked | No overwrite |
| DST transitions | All day bounds and “tomorrow” in `Europe/Amsterdam` |
| Short horizon after lock | Sum whatever D+1 slots exist in that plan; store `slot_count` |
| Partial D+1 coverage | Accept first full day-ahead plan as the freeze; do not merge later MPCs |
| Host restart | JSON on `/share` persists; republish today from JSON on next MPC |

## Out of scope

- Telegram for EMHASS alerts
- Changing realized profit formula or Zonneplan sources
- Archiving every `opt_res` for retrospective rebuild
- Fixed clock lock (14:00 / 15:00 / 16:30)
- Next-24h non-calendar lock
- Live-only `total_cost_fun_value` without frozen day plan
- Automatic container updates / WUD changes

## Verification (implementation phase)

1. Unit tests: tomorrow detect; sum by local date; once-per-day; no overwrite;
   missed-lock once; prune.
2. Package/dashboard structural tests for new sensor and chart entities.
3. `./scripts/render-ha-packages.sh`
4. Live (after deploy): on a day-ahead jump, JSON gains tomorrow key; next
   calendar day sensor shows that value; 7-day chart gains a planned bar;
   forced miss path sends one HA notify.

## Related

- Realized profit: `docs/superpowers/specs/2026-07-23-emhass-net-electricity-profit-design.md`
- 7-day realized graph: `docs/superpowers/specs/2026-08-01-emhass-7d-net-profit-graph-design.md`
- Price path: `emhass-cron/fetch_prices_emhass.py` →
  `sensor.zonneplan_current_electricity_tariff` `forecast`
