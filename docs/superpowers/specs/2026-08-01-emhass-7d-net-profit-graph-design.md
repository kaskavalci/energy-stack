# EMHASS net electricity profit — last 7 days graph

**Date:** 2026-08-01  
**Author:** Composer

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

## Approach

**Daily snapshot sensor + native `statistics-graph`.**

At **23:55** local time, a trigger-based template sensor records that day’s
net profit once. The dashboard graphs long-term statistics for that sensor
over 7 days. No ApexCharts, no backfill, no energy-host/MPC changes.

### Sensor

| Field | Value |
|-------|--------|
| Name | EMHASS net electricity profit daily |
| Entity ID | `sensor.emhass_net_electricity_profit_daily` |
| Package | `home-assistant/ha_emhass_dashboard.yaml` (with existing profit sensors) |
| Trigger | `platform: time` / `at: "23:55:00"` (HA local = `Europe/Amsterdam`) |
| Unit | `EUR` |
| `device_class` | `monetary` |
| `state_class` | `measurement` |
| State | `(production − delivery) \| round(2)` when both sources numeric |
| Attributes | `for_date` (`YYYY-MM-DD`), `production`, `delivery` |

**Skip write when sources are missing / `unknown` / `unavailable` / non-numeric.**
Do not overwrite a previous good daily value with `0` or `unknown`.

**Why 23:55:** capture before Zonneplan “today” totals reset at midnight.

**Forward-only:** no historical backfill. The graph accumulates one bar per
successful midnight snapshot after deploy.

### Dashboard

In `home-assistant/dashboards/emhass_trading.yaml`, add under the Prices
entities card:

```yaml
type: statistics-graph
title: Net electricity profit — last 7 days
days_to_show: 7
period: day
chart_type: bar
stat_types:
  - mean
entities:
  - entity: sensor.emhass_net_electricity_profit_daily
    name: Net profit (EUR)
```

Existing Prices card entities (today + month) stay unchanged.

`stat_types: mean` is correct because the sensor updates once per day; the
daily mean equals that single sample.

## Edge cases

| Case | Behavior |
|------|----------|
| Bad sources at 23:55 | Skip update; keep last good state |
| HA down across 23:55 | That calendar day has no bar (gap); no invented catch-up |
| Negative net | Allowed (loss day) |
| DST | Rely on HA local `at: "23:55:00"`; no UTC math in the template |
| First day after deploy | Graph empty until first successful snapshot |

## Out of scope

- Backfill from recorder history
- Production/delivery breakdown series
- Month/year charts, alerts
- ApexCharts or new frontend resources
- Energy-host / EMHASS MPC / cron changes
- Reterminal firmware (same HA dashboard only)

## Verification

1. Structural tests: trigger sensor contract in package YAML; dashboard card
   presence/config; no new custom card types beyond existing allowlist.
2. `./scripts/render-ha-packages.sh` succeeds.
3. Deploy with `./scripts/deploy-home-assistant.sh`.
4. After first 23:55: sensor numeric + `for_date` set; graph shows ≥1 bar
   within a day.
