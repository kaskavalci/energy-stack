# Sigen diagnostics dashboard

**Date:** 2026-08-05  
**Author:** Cursor Grok 4.5

## Goal

Add a **separate, read-only Lovelace dashboard** for Sigenergy plant health and
`sigenergy2mqtt` bridge health. Operators can answer “is the battery/inverter
OK?” and “is Modbus/MQTT transport healthy?” without opening the EMHASS trading
UI or using writers that could fight the master.

## Scope

| In | Out |
|----|-----|
| New YAML-mode dashboard (sidebar entry) | EMHASS plan / price / profit cards |
| Battery SOC/SOH, power, cell/PCS temps, cell voltage, available energy | PV string power / energy tallies |
| Remote EMS + mode **state** (display only) | Any `switch` / `number` / `button` / `script` controls |
| Plant + inverter alarms, grid status | Grid-code settings |
| `sigenergy2mqtt` Modbus/MQTT metrics | New template “health OK” binary sensors |
| Native 7-day history for temps (+ bridge series as supported) | ApexCharts / Mushroom / other custom cards |
| Deploy + structural tests + short SETUP note | Alerts, thresholds, `sigenergy2mqtt` config changes |

## Approach

**Single-view native curated dashboard** (Approach 1).

Same deploy pattern as `emhass_trading`: tracked YAML under
`home-assistant/dashboards/`, registered in repo `configuration.yaml`, rendered
with `__SIGEN_PLANT_SLUG__`, uploaded by `deploy-home-assistant.sh`.

No new HA template sensors unless implementation discovers a required graph
entity lacks recorder statistics — prefer existing MQTT discovery sensors.

## Architecture

| Piece | Choice |
|-------|--------|
| File | `home-assistant/dashboards/sigen_diagnostics.yaml` |
| Lovelace key | e.g. `sigen-diagnostics` |
| Title | Sigen diagnostics |
| Icon | e.g. `mdi:battery-heart-variant` (final icon bikeshed OK at implement) |
| Mode | `yaml`, `show_in_sidebar: true` |
| Cards | Native `entities` + `statistics-graph` (fallback `history-graph` only where stats unsupported) |
| Placeholders | `__SIGEN_PLANT_SLUG__` for plant/inverter entity paths |
| Ownership | Diagnostics only; trading remains `dashboards/emhass_trading.yaml` |

```text
sigenergy2mqtt (energy host)
  -> MQTT discovery entities on HA
  -> sigen_diagnostics.yaml (read-only Lovelace)
```

## Layout (one view, top → bottom)

### 1. Battery health (`entities`)

| Metric | Entity pattern |
|--------|----------------|
| SOC | `sensor.__SIGEN_PLANT_SLUG___plant_battery_soc` |
| SOH | `sensor.__SIGEN_PLANT_SLUG___plant_battery_soh` |
| Battery power | `sensor.__SIGEN_PLANT_SLUG___plant_battery_power` |
| Avg cell temp | `sensor.__SIGEN_PLANT_SLUG___inverter_1_average_cell_temperature` |
| Max battery temp | `sensor.__SIGEN_PLANT_SLUG___inverter_1_max_battery_temperature` |
| Min battery temp | `sensor.__SIGEN_PLANT_SLUG___inverter_1_min_battery_temperature` |
| PCS temp | `sensor.__SIGEN_PLANT_SLUG___inverter_1_temperature` |
| Avg cell voltage | `sensor.__SIGEN_PLANT_SLUG___inverter_1_average_cell_voltage` |
| Available charge energy | `sensor.__SIGEN_PLANT_SLUG___inverter_1_available_battery_charge_energy` |
| Available discharge energy | `sensor.__SIGEN_PLANT_SLUG___inverter_1_available_battery_discharge_energy` |

Prefer plant-level SOC/SOH/power; inverter ESS entities for cell/PCS detail.

### 2. Status & alarms (`entities`, read-only)

| Metric | Entity pattern |
|--------|----------------|
| Control mode | `select.__SIGEN_PLANT_SLUG___plant_remote_ems_control_mode` with `type: simple-entity` (display only; no mode changes from this card) |
| Grid status | `sensor.__SIGEN_PLANT_SLUG___plant_grid_status` |
| Grid sensor status | `sensor.__SIGEN_PLANT_SLUG___plant_grid_sensor_status` |
| Plant alarms | `sensor.__SIGEN_PLANT_SLUG___plant_alarms` |
| Plant ESS / gateway / PCS alarms | `sensor.__SIGEN_PLANT_SLUG___general_alarm_3`, `_general_alarm_4`, `_general_pcs_alarm` |
| Inverter ESS / gateway / PCS alarms | `sensor.__SIGEN_PLANT_SLUG___inverter_1_alarm_3`, `_alarm_4`, `_pcs_alarm` |

**Read-only rule (explicit):**

1. **Omit** `switch.__SIGEN_PLANT_SLUG___plant_remote_ems` — Lovelace switch rows are toggles; mode + alarms are enough for posture.
2. Control mode **must** use `type: simple-entity` so the select is not writable from this dashboard.
3. No `button.` / `script.` / `number.` rows; no `tap_action` that calls services.
4. Structural tests lock (1)–(3).

### 3. Bridge health — `sigenergy2mqtt` (`entities`)

| Metric | Entity |
|--------|--------|
| Started | `sensor.sigenergy2mqtt_started` |
| Modbus read errors | `sensor.sigenergy2mqtt_modbus_read_errors` |
| Modbus write errors | `sensor.sigenergy2mqtt_modbus_write_errors` |
| Modbus read mean / max | `sensor.sigenergy2mqtt_modbus_read_mean`, `_modbus_read_max` |
| Modbus write mean / max | `sensor.sigenergy2mqtt_modbus_write_mean`, `_modbus_write_max` |
| Modbus reads/sec | `sensor.sigenergy2mqtt_modbus_reads_sec` |
| Modbus cache hit % | `sensor.sigenergy2mqtt_modbus_cache_hit_percentage` |
| MQTT publish failures | `sensor.sigenergy2mqtt_mqtt_publish_failures` |
| MQTT physical publish % | `sensor.sigenergy2mqtt_mqtt_physical_publish_percentage` |

### 4. History

**Temperatures** — native `statistics-graph`, `days_to_show: 7`, `period: hour`,
`chart_type: line`, `stat_types: [mean]` (same period style as the trading PV
actual graph):

- avg cell, max battery, min battery, PCS temperature (inverter_1 entities above)

**Bridge** — prefer `statistics-graph` when entities have suitable
`state_class`. Live plant recorder keeps **7 days** (`purge_keep_days: 7` in
repo `configuration.yaml`), so 7d graphs match retention. If error counters
lack long-term statistics, use `history-graph` with `hours_to_show: 168` for
those series only, with a YAML comment stating the fallback is intentional.

Candidate bridge series: Modbus read errors, write errors, read mean ms.

## Error handling / unavailable

- No new availability wrappers or “all clear” binary sensors.
- Native cards show `unavailable` / `unknown` as HA does.
- Bridge metrics are the transport signal when plant sensors go stale.

## Testing

Structural unittest mirroring `scripts/tests/test_emhass_trading_dashboard.py`:

1. Dashboard file loads; single view; expected title/path markers.
2. No `custom:` card types; no embedded `resources`.
3. Required entity ID placeholders / patterns present.
4. No `button.` / `script.` / `number.` / `switch.` entity rows; control mode
   row uses `type: simple-entity`.
5. Temperature 7d graph lists the four temp entities in a fixed order
   (avg cell → max → min → PCS).

## Deploy / docs

1. Add Lovelace dashboard entry in tracked `configuration.yaml`.
2. Upload `dashboards/sigen_diagnostics.yaml` from `deploy-home-assistant.sh`
   (same pattern as `emhass_trading.yaml`).
3. Ensure `render-ha-packages.sh` rewrites `__SIGEN_PLANT_SLUG__` in the new
   dashboard (same as trading).
4. Short operator note in `docs/SETUP.md` (sidebar name + purpose).
5. Update `memory-bank/activeContext.md` / `techContext.md` after deploy.

## Success criteria

- Sidebar shows **Sigen diagnostics** after HA deploy/restart.
- Battery temps, SOC/SOH, alarms, and bridge error/latency entities visible.
- 7d temperature graph renders for sensors with statistics.
- No dashboard control can change Remote EMS, mode, or power limits.
- Trading dashboard unchanged in behavior.

## Non-goals

- Alerting or cooldown notifications for high temp / Modbus errors
- Changing Modbus poll set or enabling extra registers
- Merging this into the EMHASS trading dashboard
- Multi-inverter generalization beyond `inverter_1` (site is single inverter)
