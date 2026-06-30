# Entity and package contract (cross-repo)

**Last updated:** 2026-06-30

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
| `sensor.emhass_house_load_power_w` | energy-stack templates | `config.json`, MPC load source |
| `sensor.emhass_pv_power_w` | energy-stack templates | PV observation |
| `sensor.zonneplan_current_electricity_tariff` | HA HACS | emhass-cron price fetch |
| `sensor.sigen_0_plant_battery_soc` | sigenergy2mqtt | MPC `soc_init` |
| Envoy `sensor.envoy_*` | HA integration | PV truth (AC-coupled) |

## Related repos

- **reterminal** — ESPHome firmware + P1 alias package
