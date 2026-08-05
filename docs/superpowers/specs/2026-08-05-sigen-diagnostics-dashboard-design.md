# Sigen diagnostics dashboard

**Date:** 2026-08-05  
**Author:** Cursor Grok 4.5  
**Status:** Revised after design review (major revisions)

## Goal

Add a **separate, read-only Lovelace dashboard** for Sigenergy plant health and
`sigenergy2mqtt` bridge health. Operators can answer “is the battery/inverter
OK?” and “is Modbus/MQTT transport healthy *right now*?” without opening the
EMHASS trading UI or using writers that could fight the master.

## Scope

| In | Out |
|----|-----|
| New YAML-mode dashboard (sidebar entry) | EMHASS plan / price / profit cards |
| Battery SOC/SOH, power, cell/PCS temps, cell voltage, available energy | PV string power / energy tallies |
| Remote EMS switch + mode as **read-only** `simple-entity` rows | Writable controls, service `tap_action`s, `button` / `script` / `number` rows |
| Plant + inverter alarms, grid status | Grid-code settings |
| Bridge **liveness** (health MQTT) + discovered metrics counters/latency | Alerting / cooldowns |
| Exact 7d history cards (temps via statistics; bridge via history-graph) | ApexCharts / Mushroom / other custom cards |
| Full deploy manifest wiring (merge + upload + tests) | Changing `sigenergy2mqtt` Modbus poll set |

## Approach

**Single-view native curated dashboard** (Approach 1), plus a small HA package
that exposes upstream health MQTT topics Home Assistant does not discover today.

## Live verification (2026-08-05, Odroid HA)

Dump method: HA REST `/api/states` + energy-host `mosquitto_sub` on
`sigenergy2mqtt/health/#` and `sigenergy2mqtt/metrics/#`.

| Finding | Evidence |
|---------|----------|
| Plant/inverter diagnostics exist under `sigen_0_*` | e.g. SOC, temps, alarms present and updating |
| Metrics **are** HA entities `sensor.sigenergy2mqtt_*` | Discovery on this install maps metrics topics → those entity IDs |
| Upstream docs still describe metrics as MQTT topics under `sigenergy2mqtt/metrics/…` | [TOPICS.md](https://raw.githubusercontent.com/seud0nym/sigenergy2mqtt/main/resources/sensors/TOPICS.md) |
| Health is MQTT-only — **no** HA entity today | Live: `sigenergy2mqtt/health/state` = `healthy`; attributes JSON includes `mqtt_connected`, `modbus_connected`, `overdue_topics`, `timestamp` |
| Temp sensors support `statistics-graph` | `device_class=temperature`, `state_class=measurement`; recorder history present |
| Bridge metric sensors do **not** have `state_class` | `statistics-graph` is unsuitable; use `history-graph` |
| Protocol | `sensor.sigenergy2mqtt_modbus_protocol` = `2.8` (published `2025-11-28`); temp attrs `since-protocol: V1.8` |
| Alarm states on this firmware | Decoded strings; clear = `No Alarm` (not a raw `0` integer in HA state) |

Exact IDs are recorded in [docs/ENTITY_CONTRACT.md](../../ENTITY_CONTRACT.md)
(section **Sigen diagnostics**).

## Architecture

```text
sigenergy2mqtt
  ├─ HA discovery → sensor/switch/select sigen_0_* + sensor.sigenergy2mqtt_*
  ├─ MQTT health  → (new) mqtt sensors in ha_sigen_diagnostics.yaml
  └─ Lovelace     → dashboards/sigen_diagnostics.yaml (read-only)
```

| Piece | Choice |
|-------|--------|
| Dashboard file | `home-assistant/dashboards/sigen_diagnostics.yaml` |
| Support package | `home-assistant/ha_sigen_diagnostics.yaml` (MQTT health sensors only) |
| Lovelace key | `sigen-diagnostics` |
| Title | Sigen diagnostics |
| Icon | `mdi:battery-heart-variant` |
| Mode | `yaml`, `show_in_sidebar: true` |
| Cards | Native `entities`, `statistics-graph`, `history-graph` only |
| Placeholders | `__SIGEN_PLANT_SLUG__` for plant/inverter paths; bridge IDs are fixed `sigenergy2mqtt_*` |

### Deploy registration (blocking — must all change)

Live sidebar registration does **not** come from editing tracked
`configuration.yaml` alone. Additive merge owns Lovelace keys via
`ENERGY_STACK_MANIFEST` in `scripts/lib/ha_config_merge.py`, and the deploy
script must upload new files.

| File | Required change |
|------|-----------------|
| `scripts/lib/ha_config_merge.py` | Add package `reterminal_sigen_diagnostics: !include ha_sigen_diagnostics.yaml`; add `lovelace_dashboards["sigen-diagnostics"]` with mode/title/icon/filename |
| `scripts/deploy-home-assistant.sh` | Upload `ha_sigen_diagnostics.yaml` with other packages; upload `dashboards/sigen_diagnostics.yaml` beside trading |
| `scripts/tests/test_ha_config_merge.py` | Assert merge output contains `sigen-diagnostics:` and the new package key (preserve third-party dashboards) |
| `configuration.yaml` (repo fragment) | Mirror the same lovelace + package entries for human/docs parity |
| `scripts/render-ha-packages.sh` | Render `__SIGEN_PLANT_SLUG__` in the new dashboard (and package if needed) |

## Layout (one view, top → bottom)

### 1. Battery health (`entities`)

| Metric | Entity (slug = `SIGEN_PLANT_SLUG`) |
|--------|-------------------------------------|
| SOC | `sensor.<slug>_plant_battery_soc` |
| SOH | `sensor.<slug>_plant_battery_soh` |
| Battery power | `sensor.<slug>_plant_battery_power` |
| Avg cell temp | `sensor.<slug>_inverter_1_average_cell_temperature` |
| Max battery temp | `sensor.<slug>_inverter_1_max_battery_temperature` |
| Min battery temp | `sensor.<slug>_inverter_1_min_battery_temperature` |
| PCS temp | `sensor.<slug>_inverter_1_temperature` |
| Avg cell voltage | `sensor.<slug>_inverter_1_average_cell_voltage` |
| Available charge energy | `sensor.<slug>_inverter_1_available_battery_charge_energy` |
| Available discharge energy | `sensor.<slug>_inverter_1_available_battery_discharge_energy` |

### 2. Status & alarms (`entities`, read-only)

| Metric | Entity | Presentation |
|--------|--------|--------------|
| Remote EMS | `switch.<slug>_plant_remote_ems` | `type: simple-entity` |
| Control mode | `select.<slug>_plant_remote_ems_control_mode` | `type: simple-entity` |
| Grid status | `sensor.<slug>_plant_grid_status` | default |
| Grid sensor status | `sensor.<slug>_plant_grid_sensor_status` | default |
| Plant alarms | `sensor.<slug>_plant_alarms` | default |
| Plant ESS / gateway / PCS alarms | `sensor.<slug>_general_alarm_3`, `_general_alarm_4`, `_general_pcs_alarm` | default |
| Inverter ESS / gateway / PCS alarms | `sensor.<slug>_inverter_1_alarm_3`, `_alarm_4`, `_pcs_alarm` | default |

**Read-only rule:**

1. Include Remote EMS **and** control mode, both as `type: simple-entity`
   ([entities card](https://www.home-assistant.io/dashboards/entities/)).
2. No `button.` / `script.` / `number.` rows; no service `tap_action`.
3. Structural tests require both EMS entities present with `simple-entity`, and
   forbid writable row types above.

### 3. Bridge liveness + metrics (`entities`)

**Liveness first** (proves the process is alive; counters alone cannot):

| Metric | Entity (new MQTT sensors) | Notes |
|--------|---------------------------|-------|
| Health state | `sensor.sigenergy2mqtt_health` | `state_topic` `sigenergy2mqtt/health/state`; `default_entity_id`; `expire_after: 180` |
| MQTT connected | `binary_sensor.sigenergy2mqtt_mqtt_connected` | attributes JSON → `'ON'`/`'OFF'` with matching `payload_on`/`payload_off`; `expire_after: 180` |
| Modbus connected | `binary_sensor.sigenergy2mqtt_modbus_connected` | same ON/OFF mapping; `expire_after: 180` |
| Overdue topics | `sensor.sigenergy2mqtt_overdue_topics` | attributes `overdue_topics`; `expire_after: 180` |
| Health timestamp | `sensor.sigenergy2mqtt_health_timestamp` | attributes `timestamp` Unix epoch int → `as_datetime`; `expire_after: 180` |

Package `ha_sigen_diagnostics.yaml` defines these via MQTT sensors. Use
`default_entity_id` (not `object_id`). HA must already have an MQTT integration
to the energy-host broker.

**Freshness:** `expire_after: 180` seconds on every health entity. Live
`sigenergy2mqtt` container healthcheck treats attributes timestamp older than
**120s** as unhealthy (30s poll). After 180s without MQTT updates, HA state
becomes `unavailable` — counters alone must not look “healthy.”

**Timestamp payload:** Unix epoch integer. Live fixture (2026-08-05):
`1785955534` → `2026-08-05T18:45:34+00:00`.

**Operator reading:** if health is `unavailable`, ≠ `healthy`, either connected
binary is `off`, overdue_topics > 0, or metric `last-updated` is stale, treat
transport as bad **even when error counters are 0**.

**Counters / latency** (existing discovered entities; each row
`secondary_info: last-updated`):

| Metric | Entity |
|--------|--------|
| Started | `sensor.sigenergy2mqtt_started` |
| Protocol | `sensor.sigenergy2mqtt_modbus_protocol` |
| Modbus read errors | `sensor.sigenergy2mqtt_modbus_read_errors` |
| Modbus write errors | `sensor.sigenergy2mqtt_modbus_write_errors` |
| Modbus read mean / max | `sensor.sigenergy2mqtt_modbus_read_mean`, `_modbus_read_max` |
| Modbus write mean / max | `sensor.sigenergy2mqtt_modbus_write_mean`, `_modbus_write_max` |
| Modbus reads/sec | `sensor.sigenergy2mqtt_modbus_reads_sec` |
| Modbus cache hit % | `sensor.sigenergy2mqtt_modbus_cache_hit_percentage` |
| MQTT publish failures | `sensor.sigenergy2mqtt_mqtt_publish_failures` |
| MQTT physical publish % | `sensor.sigenergy2mqtt_mqtt_physical_publish_percentage` |

### 4. History (exact cards)

Recorder retention on this site: `purge_keep_days: 7`.

**Card A — temperatures** (`statistics-graph`)

| Field | Value |
|-------|--------|
| `days_to_show` | `7` |
| `period` | `hour` |
| `chart_type` | `line` |
| `stat_types` | `[mean]` |
| Entities (order) | avg cell → max battery → min battery → PCS (`inverter_1_*` temps above) |

**Acceptance:** after deploy, open the card on live HA and confirm series render
(not empty). Pre-implement check (2026-08-05): all four have
`state_class=measurement` and multi-day recorder history.

**Card B — bridge counters/latency** (`history-graph`, intentional)

| Field | Value |
|-------|--------|
| `hours_to_show` | `168` |
| Entities (order) | `sensor.sigenergy2mqtt_modbus_read_errors`, `sensor.sigenergy2mqtt_modbus_write_errors`, `sensor.sigenergy2mqtt_modbus_read_mean` |
| Why not statistics-graph | Live `state_class` is `None` on these entities (2026-08-05 dump) |
| YAML comment | Must state fallback is intentional |

No other bridge graph cards in v1.

## Availability, alarms, protocol

### Required availability (acceptance)

Before calling the feature done, live HA must show **available** (not
`unavailable` / `unknown`) for:

- All battery-health entities in §1
- Remote EMS switch + control mode
- All alarm + grid status entities in §2
- All new health MQTT sensors in §3 (after package deploy)
- All listed metrics sensors in §3

Temp entities require Sigenergy protocol ≥ V1.8 for those registers
(`since-protocol: V1.8` on live attrs). Site runs protocol **2.8**.

### Alarm semantics

On this plant, alarm sensors are **decoded strings**, not raw bitmasks in the
HA state:

| State | Meaning |
|-------|---------|
| `No Alarm` | Clear |
| Any other non-empty string | Active alarm description / code text |
| `unavailable` / `unknown` | Sensor/transport problem — not “clear” |

Do not treat numeric `0` as the clear sentinel unless a future firmware change
makes the state numeric; UI copy / docs should say **clear = `No Alarm`**.

## Error handling

- No “all clear” rollup binary sensor in v1.
- Native cards show `unavailable` / `unknown` as HA does.
- Transport truth order: health/connected/overdue/`last-updated` → then counters.

## Testing

### Structural (CI / local unittest)

Mirror `scripts/tests/test_emhass_trading_dashboard.py` plus merge tests:

1. Dashboard loads; single view; title/path/`sigen-diagnostics` markers.
2. No `custom:` cards; no embedded `resources`.
3. Required entity IDs / placeholders present (plant slug + fixed bridge IDs).
4. Remote EMS switch + mode rows exist with `type: simple-entity`.
5. No `button.` / `script.` / `number.` rows; no service `tap_action`.
6. Card A entity order locked; Card B is `history-graph` with the three exact
   entities and `hours_to_show: 168`.
7. `test_ha_config_merge.py`: merge emits `sigen-diagnostics:` and
   `reterminal_sigen_diagnostics` package; preserves foreign dashboards.
8. Package YAML defines the four/five MQTT health entities with the verified
   topics.

### Live acceptance (post-deploy, not unit-testable)

1. Sidebar entry appears after deploy/restart.
2. Health state reads `healthy` (or correctly `degraded`) matching
   `mosquitto_sub` on the energy host.
3. Connected binaries match health attributes JSON.
4. Temperature Card A shows non-empty 7d series in UI.
5. Bridge Card B shows history for the three entities.
6. `simple-entity` rows do not toggle Remote EMS / mode from the dashboard.

## Docs

1. [ENTITY_CONTRACT.md](../../ENTITY_CONTRACT.md) — Sigen diagnostics entity
   table (done as part of this revision; keep in sync if IDs change).
2. `docs/SETUP.md` — short sidebar note.
3. `memory-bank/activeContext.md` / `techContext.md` after deploy.

## Success criteria

- Deploy merge + upload path registers the dashboard without hand-editing live
  `configuration.yaml` outside the merge tool.
- Health/connectivity entities exist and update from MQTT.
- Battery + alarm + bridge counters visible with `last-updated` on metrics.
- Exact history cards behave as specified on live HA.
- Trading dashboard behavior unchanged; no writable EMS controls on this UI.

## Non-goals

- Alerting / notifications for high temp or Modbus errors
- Changing Modbus poll set or enabling extra registers
- Merging into the EMHASS trading dashboard
- Multi-inverter generalization beyond `inverter_1`
- Forcing `state_class` onto upstream metrics entities (use history-graph)
