# Sigen Diagnostics Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a separate read-only Lovelace dashboard for Sigenergy plant health and `sigenergy2mqtt` bridge liveness/metrics, with full deploy-manifest wiring.

**Architecture:** MQTT health topics (not HA-discovered today) become entities via a new package `ha_sigen_diagnostics.yaml` using `default_entity_id`, explicit binary ON/OFF payloads, and `expire_after: 180`. A single-view native dashboard shows plant diagnostics, EMS posture as `simple-entity`, bridge health + counters, a 7d temp `statistics-graph`, and a 168h bridge `history-graph`. Registration goes through `ENERGY_STACK_MANIFEST` merge + deploy uploads.

**Tech Stack:** Home Assistant YAML packages + Lovelace; MQTT integration sensors; Python `unittest` + `ruamel.yaml`; `./scripts/render-ha-packages.sh` / `./scripts/deploy-home-assistant.sh`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-05-sigen-diagnostics-dashboard-design.md`
- Entity contract: `docs/ENTITY_CONTRACT.md` § Sigen diagnostics
- Lovelace key: `sigen-diagnostics`; file `dashboards/sigen_diagnostics.yaml`
- Package key: `reterminal_sigen_diagnostics` → `ha_sigen_diagnostics.yaml`
- MQTT entity IDs: set with `default_entity_id` (full `domain.object_id`); **do not** use `object_id`
- Binary connected sensors: template → `'ON'`/`'OFF'` with `payload_on: "ON"` / `payload_off: "OFF"`
- Health freshness: every health MQTT entity sets `expire_after: 180` (seconds). Rationale: container healthcheck treats attributes timestamp older than **120s** as unhealthy and polls every **30s**; 180s = 1.5× that window. After expiry HA state is `unavailable`
- Health `timestamp` attribute is a **Unix epoch integer** (live fixture `1785955534` → `2026-08-05T18:45:34+00:00`)
- Cards: native only — no `custom:`, no ApexCharts, no embedded `resources`
- Read-only: **every** `switch.` and `select.` row uses `type: simple-entity`; no `button`/`script`/`number` rows; no service `tap_action`
- Alarm clear sentinel: state string `No Alarm`
- Temp history: `statistics-graph`, `days_to_show: 7`, `period: hour`, four entities in fixed order
- Bridge history: `history-graph`, `hours_to_show: 168`, exactly three metric entities
- **Every** bridge metric row: `secondary_info: last-updated`
- Plant slug placeholder: `__SIGEN_PLANT_SLUG__`
- No energy-host / EMHASS MPC / `sigenergy2mqtt` Modbus poll changes
- Create implementation commits only when the user explicitly authorizes commits; otherwise leave diffs unstaged and report the suggested message
- Read `memory-bank/activeContext.md`, `memory-bank/techContext.md`, and `.agents/skills/emhass-energy-stack/SKILL.md` before live deploy
- Source `config/site.env` before SSH/deploy; never hardcode hosts or tokens

## File Map

| File | Responsibility |
|------|----------------|
| `home-assistant/ha_sigen_diagnostics.yaml` | MQTT health/liveness entities |
| `home-assistant/dashboards/sigen_diagnostics.yaml` | Lovelace dashboard YAML |
| `scripts/lib/ha_config_merge.py` | Manifest package + lovelace dashboard registration |
| `configuration.yaml` | Repo fragment mirror of package + lovelace entry |
| `scripts/deploy-home-assistant.sh` | Upload new package + dashboard |
| `scripts/tests/test_ha_config_merge.py` | Merge asserts for new keys + exact filenames |
| `scripts/tests/test_sigen_diagnostics_dashboard.py` | Parsed YAML package + dashboard structural tests |
| `docs/SETUP.md` | Operator sidebar note |
| `memory-bank/activeContext.md` / `techContext.md` | Landed + deploy status |

## Shared fixtures (use in tests)

```python
# Live health attributes payload sample (energy host, 2026-08-05)
HEALTH_ATTRIBUTES_FIXTURE = {
    "status": "healthy",
    "mqtt_connected": True,
    "modbus_connected": True,
    "overdue_topics": 0,
    "monitored_topics": 0,
    "timestamp": 1785955534,
}
HEALTH_TIMESTAMP_ISO = "2026-08-05T18:45:34+00:00"
HEALTH_EXPIRE_AFTER = 180
BRIDGE_METRIC_ENTITIES = [
    "sensor.sigenergy2mqtt_started",
    "sensor.sigenergy2mqtt_modbus_protocol",
    "sensor.sigenergy2mqtt_modbus_read_errors",
    "sensor.sigenergy2mqtt_modbus_write_errors",
    "sensor.sigenergy2mqtt_modbus_read_mean",
    "sensor.sigenergy2mqtt_modbus_read_max",
    "sensor.sigenergy2mqtt_modbus_write_mean",
    "sensor.sigenergy2mqtt_modbus_write_max",
    "sensor.sigenergy2mqtt_modbus_reads_sec",
    "sensor.sigenergy2mqtt_modbus_cache_hit_percentage",
    "sensor.sigenergy2mqtt_mqtt_publish_failures",
    "sensor.sigenergy2mqtt_mqtt_physical_publish_percentage",
]
```

---

### Task 1: Failing structural tests (merge + package + dashboard)

**Files:**
- Modify: `scripts/tests/test_ha_config_merge.py`
- Create: `scripts/tests/test_sigen_diagnostics_dashboard.py`

**Interfaces:**
- Consumes: `ENERGY_STACK_MANIFEST`, `merge_configuration`
- Produces: tests requiring exact manifest filenames, parsed MQTT package contracts, dashboard read-only + history rules

- [ ] **Step 1: Extend merge tests with exact filenames**

In `scripts/tests/test_ha_config_merge.py`, add asserts to `test_energy_stack_preserves_third_party_packages` (and the independent-deploy test):

```python
        self.assertIn("sigen-diagnostics:", out)
        self.assertIn(
            "reterminal_sigen_diagnostics: !include ha_sigen_diagnostics.yaml",
            out,
        )
        self.assertIn("filename: dashboards/sigen_diagnostics.yaml", out)
        self.assertIn("filename: dashboards/emhass_trading.yaml", out)
```

Also assert the Python manifest dict (not only merge text):

```python
    def test_energy_stack_manifest_owns_sigen_diagnostics_files(self) -> None:
        self.assertEqual(
            ENERGY_STACK_MANIFEST.packages["reterminal_sigen_diagnostics"],
            "!include ha_sigen_diagnostics.yaml",
        )
        dash = ENERGY_STACK_MANIFEST.lovelace_dashboards["sigen-diagnostics"]
        self.assertEqual(dash["filename"], "dashboards/sigen_diagnostics.yaml")
        self.assertEqual(dash["title"], "Sigen diagnostics")
        self.assertTrue(dash["show_in_sidebar"])
```

- [ ] **Step 2: Create parsed package + dashboard tests**

Create `scripts/tests/test_sigen_diagnostics_dashboard.py` with the fixtures above and helpers from the previous plan draft (`load_yaml`, `iter_mappings`, `entity_rows`), then:

```python
class SigenDiagnosticsPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.package = load_yaml(PACKAGE)
        cls.sensors = {
            s["default_entity_id"]: s for s in cls.package["mqtt"]["sensor"]
        }
        cls.binary = {
            s["default_entity_id"]: s for s in cls.package["mqtt"]["binary_sensor"]
        }

    def test_health_sensor_contract(self) -> None:
        s = self.sensors["sensor.sigenergy2mqtt_health"]
        self.assertEqual(s["state_topic"], "sigenergy2mqtt/health/state")
        self.assertEqual(s["expire_after"], HEALTH_EXPIRE_AFTER)
        self.assertNotIn("object_id", s)

    def test_attribute_sensors_contract(self) -> None:
        overdue = self.sensors["sensor.sigenergy2mqtt_overdue_topics"]
        ts = self.sensors["sensor.sigenergy2mqtt_health_timestamp"]
        for s in (overdue, ts):
            self.assertEqual(s["state_topic"], "sigenergy2mqtt/health/attributes")
            self.assertEqual(s["expire_after"], HEALTH_EXPIRE_AFTER)
            self.assertNotIn("object_id", s)
        self.assertEqual(
            overdue["value_template"], "{{ value_json.overdue_topics }}"
        )
        self.assertEqual(ts["device_class"], "timestamp")
        self.assertEqual(
            ts["value_template"],
            "{{ (value_json.timestamp | int) | as_datetime }}",
        )

    def test_timestamp_template_matches_live_fixture(self) -> None:
        # Mirror HA template intent in Python for the documented payload.
        from datetime import datetime, timezone

        rendered = datetime.fromtimestamp(
            int(HEALTH_ATTRIBUTES_FIXTURE["timestamp"]), tz=timezone.utc
        ).isoformat()
        self.assertEqual(rendered, HEALTH_TIMESTAMP_ISO)

    def test_binary_connected_payload_mapping(self) -> None:
        for eid, key in (
            ("binary_sensor.sigenergy2mqtt_mqtt_connected", "mqtt_connected"),
            ("binary_sensor.sigenergy2mqtt_modbus_connected", "modbus_connected"),
        ):
            s = self.binary[eid]
            self.assertEqual(s["state_topic"], "sigenergy2mqtt/health/attributes")
            self.assertEqual(s["expire_after"], HEALTH_EXPIRE_AFTER)
            self.assertEqual(s["payload_on"], "ON")
            self.assertEqual(s["payload_off"], "OFF")
            self.assertEqual(
                s["value_template"],
                "{{ 'ON' if value_json." + key + " else 'OFF' }}",
            )
            self.assertNotIn("object_id", s)
            # Fixture resolves to ON when connected true
            connected = HEALTH_ATTRIBUTES_FIXTURE[key]
            self.assertEqual("ON" if connected else "OFF", "ON")


class SigenDiagnosticsDashboardTests(unittest.TestCase):
    # ... single_view_native_only, graphs unchanged from prior draft ...

    def test_every_switch_and_select_is_simple_entity(self) -> None:
        for row in entity_rows(self.dashboard):
            eid = str(row["entity"])
            if eid.startswith(("switch.", "select.")):
                self.assertEqual(
                    row.get("type"),
                    "simple-entity",
                    msg=f"{eid} must be simple-entity",
                )

    def test_no_writable_control_rows(self) -> None:
        for row in entity_rows(self.dashboard):
            eid = str(row["entity"])
            self.assertFalse(eid.startswith(("button.", "script.", "number.")))
            self.assertNotIn("tap_action", row)

    def test_all_bridge_metrics_show_last_updated(self) -> None:
        by_id = {r["entity"]: r for r in entity_rows(self.dashboard)}
        for eid in BRIDGE_METRIC_ENTITIES:
            self.assertEqual(
                by_id[eid].get("secondary_info"),
                "last-updated",
                msg=eid,
            )

    def test_health_entities_on_dashboard(self) -> None:
        ids = {r["entity"] for r in entity_rows(self.dashboard)}
        for eid in (
            "sensor.sigenergy2mqtt_health",
            "binary_sensor.sigenergy2mqtt_mqtt_connected",
            "binary_sensor.sigenergy2mqtt_modbus_connected",
            "sensor.sigenergy2mqtt_overdue_topics",
            "sensor.sigenergy2mqtt_health_timestamp",
        ):
            self.assertIn(eid, ids)
```

Include the temperature `statistics-graph` and bridge `history-graph` tests from the prior draft (unchanged entity lists).

- [ ] **Step 3: Run tests — expect failures**

```bash
cd /Users/halil/workspace/misc/energy-stack
.venv/bin/python -m unittest scripts.tests.test_ha_config_merge scripts.tests.test_sigen_diagnostics_dashboard -v
```

Expected: FAIL (missing manifest keys / package / dashboard).

- [ ] **Step 4: Commit (only if user authorized)**

```text
test: add failing Sigen diagnostics structural coverage
```

---

### Task 2: Deploy manifest + upload path

**Files:**
- Modify: `scripts/lib/ha_config_merge.py`
- Modify: `configuration.yaml`
- Modify: `scripts/deploy-home-assistant.sh`
- Test: `scripts/tests/test_ha_config_merge.py`

**Interfaces:**
- Consumes: Task 1 merge assertions
- Produces: registered dashboard + package upload paths

- [ ] **Step 1: Update `ENERGY_STACK_MANIFEST`**

```python
        "reterminal_sigen_diagnostics": "!include ha_sigen_diagnostics.yaml",
```

```python
        "sigen-diagnostics": {
            "mode": "yaml",
            "title": "Sigen diagnostics",
            "icon": "mdi:battery-heart-variant",
            "show_in_sidebar": True,
            "filename": "dashboards/sigen_diagnostics.yaml",
        },
```

Confirm `MONOREPO_MANIFEST` still derives packages/dashboards from `ENERGY_STACK_MANIFEST`.

- [ ] **Step 2: Mirror in repo `configuration.yaml`**

```yaml
    reterminal_sigen_diagnostics: !include ha_sigen_diagnostics.yaml
```

```yaml
    sigen-diagnostics:
      mode: yaml
      title: Sigen diagnostics
      icon: mdi:battery-heart-variant
      show_in_sidebar: true
      filename: dashboards/sigen_diagnostics.yaml
```

- [ ] **Step 3: Extend deploy uploads**

```bash
for f in ha_emhass.yaml ha_emhass_sensors.yaml ha_emhass_dashboard.yaml ha_emhass_monitoring.yaml ha_emhass_helpers.yaml ha_sigen_ems.yaml ha_sigen_diagnostics.yaml; do
  upload "${RENDERED}/${f}" "${f}"
done
upload "${RENDERED}/dashboards/emhass_trading.yaml" "dashboards/emhass_trading.yaml"
upload "${RENDERED}/dashboards/sigen_diagnostics.yaml" "dashboards/sigen_diagnostics.yaml"
```

- [ ] **Step 4: Re-run merge tests**

```bash
.venv/bin/python -m unittest scripts.tests.test_ha_config_merge -v
```

Expected: PASS.

- [ ] **Step 5: Commit (only if user authorized)**

```text
feat(ha): register Sigen diagnostics dashboard in deploy merge
```

---

### Task 3: MQTT health package

**Files:**
- Create: `home-assistant/ha_sigen_diagnostics.yaml`
- Test: `scripts.tests.test_sigen_diagnostics_dashboard.SigenDiagnosticsPackageTests`

**Interfaces:**
- Consumes: topics `sigenergy2mqtt/health/state` and `…/attributes`
- Produces: ENTITY_CONTRACT health entity IDs with expiry

- [ ] **Step 1: Create package**

```yaml
# Sigenergy bridge health (MQTT topics not HA-discovered by sigenergy2mqtt).
# Topics + Unix timestamp payload verified live 2026-08-05.
# expire_after 180s: 1.5× container health freshness window (120s @ 30s poll).
# See docs/superpowers/specs/2026-08-05-sigen-diagnostics-dashboard-design.md

mqtt:
  sensor:
    - name: Sigenergy2mqtt health
      unique_id: energy_stack_sigenergy2mqtt_health
      default_entity_id: sensor.sigenergy2mqtt_health
      state_topic: sigenergy2mqtt/health/state
      expire_after: 180
      icon: mdi:heart-pulse

    - name: Sigenergy2mqtt overdue topics
      unique_id: energy_stack_sigenergy2mqtt_overdue_topics
      default_entity_id: sensor.sigenergy2mqtt_overdue_topics
      state_topic: sigenergy2mqtt/health/attributes
      value_template: "{{ value_json.overdue_topics }}"
      expire_after: 180
      icon: mdi:timer-alert-outline

    - name: Sigenergy2mqtt health timestamp
      unique_id: energy_stack_sigenergy2mqtt_health_timestamp
      default_entity_id: sensor.sigenergy2mqtt_health_timestamp
      state_topic: sigenergy2mqtt/health/attributes
      device_class: timestamp
      value_template: "{{ (value_json.timestamp | int) | as_datetime }}"
      expire_after: 180
      icon: mdi:clock-outline

  binary_sensor:
    - name: Sigenergy2mqtt MQTT connected
      unique_id: energy_stack_sigenergy2mqtt_mqtt_connected
      default_entity_id: binary_sensor.sigenergy2mqtt_mqtt_connected
      state_topic: sigenergy2mqtt/health/attributes
      value_template: "{{ 'ON' if value_json.mqtt_connected else 'OFF' }}"
      payload_on: "ON"
      payload_off: "OFF"
      expire_after: 180
      device_class: connectivity
      icon: mdi:mqtt

    - name: Sigenergy2mqtt Modbus connected
      unique_id: energy_stack_sigenergy2mqtt_modbus_connected
      default_entity_id: binary_sensor.sigenergy2mqtt_modbus_connected
      state_topic: sigenergy2mqtt/health/attributes
      value_template: "{{ 'ON' if value_json.modbus_connected else 'OFF' }}"
      payload_on: "ON"
      payload_off: "OFF"
      expire_after: 180
      device_class: connectivity
      icon: mdi:lan-connect
```

- [ ] **Step 2: Run package tests**

```bash
.venv/bin/python -m unittest \
  scripts.tests.test_sigen_diagnostics_dashboard.SigenDiagnosticsPackageTests -v
```

Expected: PASS.

- [ ] **Step 3: Commit (only if user authorized)**

```text
feat(ha): add MQTT sensors for sigenergy2mqtt health topics
```

---

### Task 4: Lovelace dashboard YAML

**Files:**
- Create: `home-assistant/dashboards/sigen_diagnostics.yaml`
- Test: `scripts.tests.test_sigen_diagnostics_dashboard.SigenDiagnosticsDashboardTests`

**Interfaces:**
- Consumes: Task 3 entity IDs; `__SIGEN_PLANT_SLUG__`; discovered metrics
- Produces: single-view read-only dashboard

- [ ] **Step 1: Create dashboard**

Same card layout as the prior plan draft, with these hard requirements:

1. Remote EMS switch + mode: `type: simple-entity`
2. Bridge metrics card: **all** `BRIDGE_METRIC_ENTITIES` with `secondary_info: last-updated`
3. Bridge liveness card lists the five health entities
4. Temp `statistics-graph` and bridge `history-graph` exactly as specified
5. Comment on history-graph: intentional because metrics lack `state_class`

Use the full YAML body from the previous plan draft for `sigen_diagnostics.yaml` (battery / status / liveness / metrics / graphs), ensuring every metric row has `secondary_info: last-updated`.

- [ ] **Step 2: Run structural tests + render with site slug**

```bash
.venv/bin/python -m unittest \
  scripts.tests.test_ha_config_merge \
  scripts.tests.test_sigen_diagnostics_dashboard -v
./scripts/render-ha-packages.sh
# Derive slug from site.env — do not hard-code sigen_0
source scripts/lib/source-site-env.sh
test -f .rendered/home-assistant/ha_sigen_diagnostics.yaml
test -f .rendered/home-assistant/dashboards/sigen_diagnostics.yaml
grep -F "sensor.${SIGEN_PLANT_SLUG}_plant_battery_soc" \
  .rendered/home-assistant/dashboards/sigen_diagnostics.yaml
grep -F 'default_entity_id: sensor.sigenergy2mqtt_health' \
  .rendered/home-assistant/ha_sigen_diagnostics.yaml
grep -F 'expire_after: 180' \
  .rendered/home-assistant/ha_sigen_diagnostics.yaml
```

Expected: tests PASS; rendered files exist; slug substitution matches `SIGEN_PLANT_SLUG`.

- [ ] **Step 3: Commit (only if user authorized)**

```text
feat(ha): add Sigen diagnostics Lovelace dashboard
```

---

### Task 5: Operator docs + memory bank

**Files:**
- Modify: `docs/SETUP.md`
- Modify: `memory-bank/techContext.md`
- Modify: `memory-bank/activeContext.md` (stub until Task 6)

- [ ] **Step 1: SETUP note**

```markdown
## Sigen diagnostics dashboard

Sidebar: **Sigen diagnostics** (`dashboards/sigen_diagnostics.yaml`).
Read-only plant health (temps, SOC/SOH, alarms, EMS posture) plus
`sigenergy2mqtt` health MQTT sensors (`expire_after: 180`) and Modbus/MQTT metrics.
Registered via energy-stack deploy merge (`sigen-diagnostics`).
Entity IDs: [ENTITY_CONTRACT.md](ENTITY_CONTRACT.md) § Sigen diagnostics.
```

Set SETUP **Last updated** to the write date.

- [ ] **Step 2: techContext one-liner**

```markdown
- Sigen diagnostics is a separate YAML Lovelace dashboard (`sigen-diagnostics`)
  with MQTT health sensors (`expire_after: 180`) from `ha_sigen_diagnostics.yaml`;
  native cards only.
```

- [ ] **Step 3: Commit (only if user authorized)**

```text
docs: note Sigen diagnostics dashboard for operators
```

---

### Task 6: Live deploy and acceptance

**Files:**
- Modify: `memory-bank/activeContext.md`

**Freshness threshold:** health entities must have `last_updated` age **&lt; 180s** while the bridge is up; after a controlled pause of publishing, they must become `unavailable` within **180s** (plus a small observe slack of 30s).

- [ ] **Step 1: Preflight**

```bash
source scripts/lib/source-site-env.sh
./scripts/render-ha-packages.sh
.venv/bin/python -m unittest \
  scripts.tests.test_ha_config_merge \
  scripts.tests.test_sigen_diagnostics_dashboard -v
```

- [ ] **Step 2: Deploy**

```bash
./scripts/deploy-home-assistant.sh
```

- [ ] **Step 3: Live acceptance (reproducible commands)**

```bash
source scripts/lib/source-site-env.sh
TOKEN=$(grep -E 'long_lived_token|hass_key' emhass/secrets_emhass.yaml | head -1 | sed -E 's/^[^:]+:[[:space:]]*//')
HA_URL=$(grep hass_url emhass/secrets_emhass.yaml | head -1 | sed -E 's/^[^:]+:[[:space:]]*//' | tr -d '"' | sed 's:/*$::')

# A) Health entities available and fresh (<180s)
python3 - <<'PY'
import json, os, urllib.request
from datetime import datetime, timezone
token=os.environ["TOKEN"]; base=os.environ["HA_URL"]
req=urllib.request.Request(base+"/api/states", headers={"Authorization":"Bearer "+token})
states={s["entity_id"]:s for s in json.load(urllib.request.urlopen(req))}
ids=[
 "sensor.sigenergy2mqtt_health",
 "binary_sensor.sigenergy2mqtt_mqtt_connected",
 "binary_sensor.sigenergy2mqtt_modbus_connected",
 "sensor.sigenergy2mqtt_overdue_topics",
 "sensor.sigenergy2mqtt_health_timestamp",
]
now=datetime.now(timezone.utc)
for eid in ids:
    s=states[eid]
    assert s["state"] not in ("unavailable","unknown"), eid
    lu=datetime.fromisoformat(s["last_updated"].replace("Z","+00:00"))
    age=(now-lu).total_seconds()
    assert age < 180, f"{eid} age={age}"
    print(eid, s["state"], f"age={age:.1f}s OK")
assert states["sensor.sigenergy2mqtt_health"]["state"]=="healthy"
assert states["binary_sensor.sigenergy2mqtt_mqtt_connected"]["state"]=="on"
assert states["binary_sensor.sigenergy2mqtt_modbus_connected"]["state"]=="on"
print("freshness OK")
PY

# Export TOKEN/HA_URL into the heredoc environment:
export TOKEN HA_URL
# (re-run the python block with env vars set)

# B) Cross-check MQTT on energy host
ssh "${ENERGY_REMOTE_HOST}" "docker compose -f '${ENERGY_REMOTE_DIR}/docker-compose.yml' exec -T mosquitto mosquitto_sub -h localhost -t 'sigenergy2mqtt/health/state' -C 1 -W 5"

# C) Stale behavior: pause bridge publish, wait ≤210s, expect unavailable
ssh "${ENERGY_REMOTE_HOST}" "docker compose -f '${ENERGY_REMOTE_DIR}/docker-compose.yml' stop sigenergy2mqtt"
sleep 210
python3 - <<'PY'
import json, os, urllib.request
token=os.environ["TOKEN"]; base=os.environ["HA_URL"]
req=urllib.request.Request(base+"/api/states", headers={"Authorization":"Bearer "+token})
states={s["entity_id"]:s for s in json.load(urllib.request.urlopen(req))}
for eid in [
 "sensor.sigenergy2mqtt_health",
 "binary_sensor.sigenergy2mqtt_mqtt_connected",
 "binary_sensor.sigenergy2mqtt_modbus_connected",
]:
    assert states[eid]["state"]=="unavailable", (eid, states[eid]["state"])
    print(eid, "unavailable OK")
PY
ssh "${ENERGY_REMOTE_HOST}" "docker compose -f '${ENERGY_REMOTE_DIR}/docker-compose.yml' start sigenergy2mqtt"
# Wait until healthy again, then re-check freshness (<180s)

# D) UI checks (manual): sidebar entry; temp statistics-graph non-empty;
#    bridge history-graph shows 3 series; simple-entity rows do not toggle EMS;
#    trading dashboard still loads.
```

Before running stop/start, confirm with the operator — this briefly interrupts Modbus→MQTT (master may already be trading). Prefer a short maintenance window.

- [ ] **Step 4: Record results in `memory-bank/activeContext.md`**

- [ ] **Step 5: Commit memory-bank only if user authorized**

```text
docs(memory): note Sigen diagnostics dashboard deploy
```

---

## Spec coverage (self-review)

| Spec / review requirement | Task |
|---------------------------|------|
| `default_entity_id` (not `object_id`) | 3 + package tests |
| Binary ON/OFF payload mapping | 3 + package tests |
| `expire_after: 180` + stale acceptance | 3 + 6 |
| Unix timestamp fixture | package test + Global Constraints |
| Parsed YAML package tests | 1 |
| Every switch/select `simple-entity` | 1 + 4 |
| All bridge metrics `last-updated` | 1 + 4 |
| Exact manifest filenames + rendered files + site.env slug | 1, 2, 4 |
| Reproducible live freshness/stale commands | 6 |
| Separate dashboard + history cards | 4 |
| Deploy merge + upload | 2 |

No remaining `object_id` usage. No bare JSON-boolean binary templates. Stale health is specified and tested.
