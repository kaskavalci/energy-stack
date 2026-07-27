# EMHASS net electricity profit (today + month)

**Date:** 2026-07-23  
**Author:** Composer

## Goal

Show **net electricity profit today** and **this month** on the EMHASS trading
dashboard Prices card, derived from Zonneplan cost sensors.

## Formula

```text
net_profit = production_costs − delivery_costs
```

Positive means export earnings exceed import spend.

## Sources (Zonneplan HACS, inclusive of tax)

| Role | Entity |
|------|--------|
| Production today | `sensor.zonneplan_electricity_production_costs_today` |
| Delivery today | `sensor.zonneplan_electricity_delivery_costs_today` |
| Production month | `sensor.zonneplan_electricity_production_costs_this_month` |
| Delivery month | `sensor.zonneplan_electricity_delivery_costs_this_month` |

Do **not** use `*_excl_tax` variants.

## Approach

Template sensors in `ha_emhass_dashboard.yaml`, referenced from the existing
Prices entities card in `dashboards/emhass_trading.yaml`.

| Sensor | Entity ID |
|--------|-----------|
| Net profit today | `sensor.emhass_net_electricity_profit_today` |
| Net profit this month | `sensor.emhass_net_electricity_profit_month` |

### Sensor contract

- `unit_of_measurement`: `EUR`
- `device_class`: `monetary`
- `state_class`: `measurement` (can rise or fall within the period)
- Round to 2 decimal places
- Attributes: `production`, `delivery` (numeric source values used)
- If either source is missing / `unknown` / `unavailable` / non-numeric →
  state `unknown` (no zero fallback)

### Dashboard

Prices card entities (order):

1. `sensor.unit_load_cost`
2. `sensor.unit_prod_price`
3. `sensor.emhass_price_rank_now`
4. `sensor.emhass_net_electricity_profit_today` — name: `Net profit today`
5. `sensor.emhass_net_electricity_profit_month` — name: `Net profit this month`

## Out of scope

- Showing the four Zonneplan source costs on the dashboard
- Alerts, history graphs, year totals
- Custom cards / ApexCharts
- `site.env` entity ID wiring
- Energy-host / EMHASS MPC changes

## Verification

1. Structural tests for dashboard Prices card and template sensors
2. `./scripts/render-ha-packages.sh`
3. Deploy with `./scripts/deploy-home-assistant.sh`
4. Live: profit ≈ production − delivery for today and month
