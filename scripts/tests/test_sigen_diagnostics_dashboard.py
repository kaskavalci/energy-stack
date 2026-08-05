"""Structural regression tests for the Sigen diagnostics dashboard."""

from __future__ import annotations

import unittest
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = REPO_ROOT / "home-assistant" / "dashboards" / "sigen_diagnostics.yaml"
PACKAGE = REPO_ROOT / "home-assistant" / "ha_sigen_diagnostics.yaml"

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
TEMP_ENTITIES = [
    "sensor.__SIGEN_PLANT_SLUG___inverter_1_average_cell_temperature",
    "sensor.__SIGEN_PLANT_SLUG___inverter_1_max_battery_temperature",
    "sensor.__SIGEN_PLANT_SLUG___inverter_1_min_battery_temperature",
    "sensor.__SIGEN_PLANT_SLUG___inverter_1_temperature",
]
BRIDGE_HISTORY_ENTITIES = [
    "sensor.sigenergy2mqtt_modbus_read_errors",
    "sensor.sigenergy2mqtt_modbus_write_errors",
    "sensor.sigenergy2mqtt_modbus_read_mean",
]


def load_yaml(path: Path) -> Mapping[str, Any]:
    with path.open(encoding="utf-8") as stream:
        document = YAML(typ="safe").load(stream)
    if not isinstance(document, Mapping):
        raise TypeError(f"{path} root must be a mapping")
    return document


def iter_mappings(value: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from iter_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_mappings(child)


def entity_rows(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for node in iter_mappings(document):
        entities = node.get("entities")
        if not isinstance(entities, list):
            continue
        for item in entities:
            if isinstance(item, Mapping) and "entity" in item:
                rows.append(item)
            elif isinstance(item, str):
                rows.append({"entity": item})
    return rows


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
    @classmethod
    def setUpClass(cls) -> None:
        cls.dashboard = load_yaml(DASHBOARD)

    def test_single_view_native_only(self) -> None:
        views = self.dashboard.get("views", [])
        self.assertEqual(len(views), 1)
        custom_types = {
            node.get("type")
            for node in iter_mappings(self.dashboard)
            if isinstance(node.get("type"), str)
            and str(node["type"]).startswith("custom:")
        }
        self.assertEqual(custom_types, set())
        self.assertEqual(self.dashboard.get("resources", []), [])

    def test_temperature_statistics_graph(self) -> None:
        graphs = [
            n
            for n in iter_mappings(self.dashboard)
            if n.get("type") == "statistics-graph"
        ]
        self.assertEqual(len(graphs), 1)
        graph = graphs[0]
        self.assertEqual(graph.get("days_to_show"), 7)
        self.assertEqual(graph.get("period"), "hour")
        self.assertEqual(graph.get("stat_types"), ["mean"])
        entities = [
            e["entity"] if isinstance(e, Mapping) else e for e in graph["entities"]
        ]
        self.assertEqual(entities, TEMP_ENTITIES)

    def test_bridge_history_graph(self) -> None:
        graphs = [
            n
            for n in iter_mappings(self.dashboard)
            if n.get("type") == "history-graph"
        ]
        self.assertEqual(len(graphs), 1)
        graph = graphs[0]
        self.assertEqual(graph.get("hours_to_show"), 168)
        entities = [
            e["entity"] if isinstance(e, Mapping) else e for e in graph["entities"]
        ]
        self.assertEqual(entities, BRIDGE_HISTORY_ENTITIES)

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


if __name__ == "__main__":
    unittest.main()
