# EMHASS net electricity profit — last 7 days graph

**Date:** 2026-08-01 (revised same day, post-review — operator decision)
**Author:** Composer / Claude

## Goal

Show **daily net electricity profit/loss for the last 7 calendar days** as a
bar graph on the EMHASS trading dashboard (`emhass-trading`). Same Lovelace
YAML is used on laptop and reterminal kiosk.

## Formula

Same as existing today/month net profit sensors:

```text
net_profit = production_costs − delivery_costs
```

Positive means export earnings exceed import spend. Sources are Zonneplan HACS
**inclusive of tax** (not `*_excl_tax`).

| Role | Entity |
|------|--------|
| Production today | `sensor.zonneplan_electricity_production_costs_today` |
| Delivery today | `sensor.zonneplan_electricity_delivery_costs_today` |

## Approach (revised — operator decision)

**No separate daily snapshot sensor.** Reuse the existing continuous
`sensor.emhass_net_electricity_profit_today` (already updates live all day
from Zonneplan today cost sensors) and change how the dashboard reads its
recorder statistics: `state_class: total` + `statistics-graph` `stat_types:
[state]` instead of a 23:55 trigger snapshot.

An earlier revision of this spec used a 23:55 trigger-based snapshot sensor
(`sensor.emhass_net_electricity_profit_daily`) plus `stat_types: [mean]`.
**Rejected on review**: it added a second sensor, a new failure mode (missed
23:55 write = permanent gap for that day), and `mean` only worked because the
sensor had a single daily sample — fragile coupling between sensor update
frequency and graph correctness. The continuous approach below has no new
entity, no time trigger, and no skip-on-bad-sources condition to maintain.

### Sensor change

| Field | Value |
|-------|-------|
| Name | EMHASS net electricity profit today (existing, unchanged entity ID) |
| Entity ID | `sensor.emhass_net_electricity_profit_today` |
| Package | `home-assistant/ha_emhass_dashboard.yaml` |
| `state_class` | **`total`** (was `measurement`) |
| Unit / `device_class` | `EUR` / `monetary` (unchanged) |
| State | `(production − delivery) \| round(2)` when both sources numeric, else `unknown` (unchanged) |

`state_class: total` (not `total_increasing`) because net profit can go up
*or* down through the day (e.g. import spend during a cloudy afternoon
briefly lowers it) — `total_increasing` would treat any decrease as a meter
reset and corrupt the statistics. `total` supports non-monotonic cumulative
values and still lets `statistics-graph` compute a meaningful per-period
`state` (last known value in that period). The month sensor
(`sensor.emhass_net_electricity_profit_month`) is **unchanged** —
`measurement`, no graph consumer.

### Dashboard

In `home-assistant/dashboards/emhass_trading.yaml`, immediately after the
Prices entities card:

```yaml
- type: statistics-graph
  title: Net electricity profit — last 7 days
  days_to_show: 7
  period: day
  chart_type: bar
  stat_types:
    - state
  entities:
    - entity: sensor.emhass_net_electricity_profit_today
      name: Net profit (EUR)
```

`stat_types: [state]` (not `mean`) — with a `total`-class sensor that updates
continuously through the day, HA's recorder tracks per-period `state` as the
**last recorded value in that period**. That is exactly what "closed day"
and "today" need:

- **Closed day bar** = last value recorded before that calendar day's
  midnight reset ≈ end-of-day P/L for that day.
- **Today's bar** = the sensor's latest value so far today = live running
  P/L, updating throughout the day as Zonneplan today-costs update.

`mean` would average a full day's worth of intraday samples (which trend
from 0 toward the end-of-day figure), producing a number with no clear
meaning. `state` matches how operators actually read "yesterday's P/L" and
"today's P/L so far."

Existing Prices card entities (today + month) stay unchanged.

## Edge cases

| Case | Behavior |
|------|----------|
| Sources unknown at read time | `state` template returns `unknown`; that instant is not recorded as a numeric statistic (recorder skips non-numeric states) |
| HA down for part of a day | Last recorded value before/after the outage still stands as that period's `state`; no invented catch-up |
| Negative net | Allowed (loss day); `total` (not `total_increasing`) permits this |
| Midnight reset | Today's Zonneplan `*_today` sensors reset to 0 at midnight, so the sensor's value drops — expected and does not break `total` (which allows decreases, unlike `total_increasing`) |
| First day after deploy | Graph has data as soon as the sensor has any recorded numeric state that day (already true pre-change) |

## Out of scope

- New snapshot/trigger sensors
- Backfill from recorder history
- Production/delivery breakdown series
- Month/year charts, alerts
- ApexCharts or new frontend resources
- Energy-host / EMHASS MPC / cron changes
- Reterminal firmware (same HA dashboard only)

## Verification

1. Structural tests: today sensor `state_class: total`; dashboard card
   entity = today sensor, `stat_types: [state]`; no new sensor/trigger block.
2. `./scripts/render-ha-packages.sh` succeeds.
3. Deploy with `./scripts/deploy-home-assistant.sh`.
4. After deploy: graph shows a bar for today (live, moves through the day)
   and, once a day boundary has passed, a closed bar for the prior day near
   its actual EOD net profit.
