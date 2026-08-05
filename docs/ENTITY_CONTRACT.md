# Entity and package contract (cross-repo)

**Last updated:** 2026-08-05  
**Author:** Cursor Grok 4.5

EMHASS on the energy host and HA packages in this repo depend on stable entity IDs on Home Assistant. Some IDs are created by the **reterminal** repo (P1 MQTT aliases and daily helpers).

Site-specific entity IDs are set in **`config/site.env`** and rendered into HA packages at deploy time. See [SETUP.md](SETUP.md).

## Deploy order

1. **reterminal** — `ha_p1_mqtt_aliases.yaml`, `ha_helpers.yaml` (P1 + dashboard helpers)
2. **energy-stack** — `./scripts/setup-local.sh`, edit `config/site.env`, then HA + energy deploy

Deploying energy-stack HA packages before P1 aliases exist will leave templates `unavailable`.

## `config/site.env` → HA entity mapping

| `site.env` key | Default example | Used by |
|----------------|-----------------|---------|
| `ENVOY_PV_ENTITY` | `sensor.envoy_CHANGE_ME_current_power_production` | PV templates, monitoring, dashboard |
| `SIGEN_PLANT_SLUG` | `sigen_0` | All `*_plant_*` Sigen entities |
| `P1_POWER_NET_ENTITY` | `sensor.electricity_meter_power_net` | House load, monitoring |
| `P1_POWER_CONSUMPTION_ENTITY` | `sensor.electricity_meter_power_consumption` | Monitoring, dashboard |
| `P1_POWER_PRODUCTION_ENTITY` | `sensor.electricity_meter_power_production` | Monitoring, dashboard |
| `ZONNEPLAN_PRICE_SENSOR` | `sensor.zonneplan_current_electricity_tariff` | emhass-cron (docker env) |
| `EMHASS_SOC_SENSOR` | `sensor.sigen_0_plant_battery_soc` | MPC `soc_init` |

## Owned packages

| Package | Repo | Purpose |
|---------|------|---------|
| `reterminal_p1_mqtt` | reterminal | Legacy `sensor.electricity_meter_*` from MQTT P1 |
| `reterminal_energy` | reterminal | Daily kWh/cost helpers for reTerminal display |
| `reterminal_emhass*` + `reterminal_sigen_ems` | energy-stack | MPC publish, monitoring, EMS scripts |

Package keys keep the `reterminal_emhass_*` prefix on live HA for zero-downtime (rename deferred).

## Required entities (minimum)

| Entity / sensor | Provided by | Used by |
|-----------------|-------------|---------|
| `sensor.electricity_meter_power_net` | reterminal P1 aliases | `ha_emhass_sensors.yaml`, house load |
| `sensor.electricity_meter_power_*` | reterminal P1 aliases | Monitoring, dashboards |
| `sensor.emhass_house_load_power_w` | energy-stack templates | Instantaneous calculated house load |
| `sensor.emhass_house_load_sample_w` | energy-stack templates | EMHASS history / naive-forecast source, sampled every minute (W) |
| `sensor.emhass_pv_power_w` | energy-stack templates | PV observation |
| `sensor.zonneplan_current_electricity_tariff` | HA HACS | emhass-cron price fetch |
| `sensor.sigen_0_plant_battery_soc` | sigenergy2mqtt | MPC `soc_init` |
| Envoy `sensor.envoy_*` | HA integration | PV truth (AC-coupled) |

## Sigen diagnostics (verified 2026-08-05)

Live Odroid HA dump + energy-host MQTT. Plant slug `sigen_0` from
`SIGEN_PLANT_SLUG`. Design:
[specs/2026-08-05-sigen-diagnostics-dashboard-design.md](superpowers/specs/2026-08-05-sigen-diagnostics-dashboard-design.md).

### Plant / inverter (sigenergy2mqtt HA discovery)

| Entity | Role |
|--------|------|
| `sensor.sigen_0_plant_battery_soc` | SOC |
| `sensor.sigen_0_plant_battery_soh` | SOH |
| `sensor.sigen_0_plant_battery_power` | Battery power |
| `sensor.sigen_0_inverter_1_average_cell_temperature` | Avg cell °C (`state_class=measurement`, since-protocol V1.8) |
| `sensor.sigen_0_inverter_1_max_battery_temperature` | Max battery °C |
| `sensor.sigen_0_inverter_1_min_battery_temperature` | Min battery °C |
| `sensor.sigen_0_inverter_1_temperature` | PCS °C |
| `sensor.sigen_0_inverter_1_average_cell_voltage` | Avg cell V |
| `sensor.sigen_0_inverter_1_available_battery_charge_energy` | Available charge kWh |
| `sensor.sigen_0_inverter_1_available_battery_discharge_energy` | Available discharge kWh |
| `switch.sigen_0_plant_remote_ems` | Remote EMS (dashboard: `simple-entity` only) |
| `select.sigen_0_plant_remote_ems_control_mode` | EMS mode (dashboard: `simple-entity` only) |
| `sensor.sigen_0_plant_grid_status` | Grid status |
| `sensor.sigen_0_plant_grid_sensor_status` | Grid sensor status |
| `sensor.sigen_0_plant_alarms` | Plant alarms (clear = `No Alarm`) |
| `sensor.sigen_0_general_alarm_3` | Plant ESS alarms |
| `sensor.sigen_0_general_alarm_4` | Plant gateway alarms |
| `sensor.sigen_0_general_pcs_alarm` | Plant PCS alarms |
| `sensor.sigen_0_inverter_1_alarm_3` | Inverter ESS alarms |
| `sensor.sigen_0_inverter_1_alarm_4` | Inverter gateway alarms |
| `sensor.sigen_0_inverter_1_pcs_alarm` | Inverter PCS alarms |

### Bridge metrics (HA discovery → `sensor.sigenergy2mqtt_*`)

Upstream MQTT topics are `sigenergy2mqtt/metrics/<name>`. On this site they
appear as Home Assistant entities with the IDs below (not under `sigen_0_`).

| Entity | MQTT topic (source) |
|--------|---------------------|
| `sensor.sigenergy2mqtt_started` | `sigenergy2mqtt/metrics/started` |
| `sensor.sigenergy2mqtt_modbus_protocol` | `…/modbus_protocol` |
| `sensor.sigenergy2mqtt_modbus_read_errors` | `…/modbus_read_errors` |
| `sensor.sigenergy2mqtt_modbus_write_errors` | `…/modbus_write_errors` |
| `sensor.sigenergy2mqtt_modbus_read_mean` | `…/modbus_read_mean` |
| `sensor.sigenergy2mqtt_modbus_read_max` | `…/modbus_read_max` |
| `sensor.sigenergy2mqtt_modbus_write_mean` | `…/modbus_write_mean` |
| `sensor.sigenergy2mqtt_modbus_write_max` | `…/modbus_write_max` |
| `sensor.sigenergy2mqtt_modbus_reads_sec` | `…/modbus_reads_sec` |
| `sensor.sigenergy2mqtt_modbus_cache_hit_percentage` | `…/modbus_cache_hit_percentage` |
| `sensor.sigenergy2mqtt_mqtt_publish_failures` | `…/mqtt_publish_failures` |
| `sensor.sigenergy2mqtt_mqtt_physical_publish_percentage` | `…/mqtt_physical_publish_percentage` |

These metric sensors currently have **no** `state_class` → dashboard history
uses `history-graph`, not `statistics-graph`.

### Bridge health (MQTT only today → new package entities)

Live topics (2026-08-05): `sigenergy2mqtt/health/state`,
`sigenergy2mqtt/health/attributes`. **Not** discovered into HA yet.

| Planned entity | Source |
|----------------|--------|
| `sensor.sigenergy2mqtt_health` | `sigenergy2mqtt/health/state` |
| `binary_sensor.sigenergy2mqtt_mqtt_connected` | attributes `mqtt_connected` |
| `binary_sensor.sigenergy2mqtt_modbus_connected` | attributes `modbus_connected` |
| `sensor.sigenergy2mqtt_overdue_topics` | attributes `overdue_topics` |
| `sensor.sigenergy2mqtt_health_timestamp` | attributes `timestamp` |

Provided by planned package `ha_sigen_diagnostics.yaml` (energy-stack).

## Related repos

- **reterminal** — ESPHome firmware + P1 alias package
