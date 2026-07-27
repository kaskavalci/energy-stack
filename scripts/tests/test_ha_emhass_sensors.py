"""Semantic contract tests for EMHASS Home Assistant sensors."""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "home-assistant" / "ha_emhass_sensors.yaml"
SAMPLE_ENTITY_ID = "sensor.emhass_house_load_sample_w"
RAW_ENTITY_ID = "sensor.emhass_house_load_power_w"


def load_package() -> Mapping[str, Any]:
    with PACKAGE.open(encoding="utf-8") as stream:
        document = YAML(typ="safe").load(stream)
    if not isinstance(document, Mapping):
        raise TypeError("HA sensor package root must be a mapping")
    return document


class EmhassLoadSampleSensorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.package = load_package()

    def sample_sensor(self) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        matches: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
        for template_item in self.package.get("template", []):
            if not isinstance(template_item, Mapping):
                continue
            for sensor in template_item.get("sensor", []):
                if (
                    isinstance(sensor, Mapping)
                    and sensor.get("default_entity_id") == SAMPLE_ENTITY_ID
                ):
                    matches.append((template_item, sensor))
        self.assertEqual(
            len(matches),
            1,
            f"expected exactly one {SAMPLE_ENTITY_ID} template sensor",
        )
        return matches[0]

    def raw_sensor(self) -> Mapping[str, Any]:
        matches: list[Mapping[str, Any]] = []
        for template_item in self.package.get("template", []):
            if not isinstance(template_item, Mapping):
                continue
            for sensor in template_item.get("sensor", []):
                if (
                    isinstance(sensor, Mapping)
                    and sensor.get("default_entity_id") == RAW_ENTITY_ID
                ):
                    matches.append(sensor)
        self.assertEqual(
            len(matches),
            1,
            f"expected exactly one preserved {RAW_ENTITY_ID} template sensor",
        )
        return matches[0]

    def test_sample_sensor_uses_minute_trigger(self) -> None:
        template_item, _ = self.sample_sensor()
        self.assertEqual(
            template_item.get("trigger"),
            [{"platform": "time_pattern", "seconds": "0"}],
        )

    def test_sample_sensor_has_power_metadata(self) -> None:
        _, sensor = self.sample_sensor()
        self.assertEqual(sensor.get("name"), "EMHASS house load sample")
        self.assertEqual(sensor.get("unique_id"), "emhass_house_load_sample_w")
        self.assertEqual(sensor.get("unit_of_measurement"), "W")
        self.assertEqual(sensor.get("device_class"), "power")
        self.assertEqual(sensor.get("state_class"), "measurement")

    def test_sample_sensor_uses_numeric_source_and_forces_update(self) -> None:
        _, sensor = self.sample_sensor()
        raw_sensor = self.raw_sensor()
        self.assertEqual(raw_sensor.get("name"), "EMHASS house load power")
        self.assertEqual(
            sensor.get("state"),
            "{{ states('sensor.emhass_house_load_power_w')"
            " | float(0) | round(0) }}\n",
        )
        availability = " ".join(str(sensor.get("availability", "")).split())
        self.assertEqual(
            availability,
            "{% set value = states('sensor.emhass_house_load_power_w') %} "
            "{{ value not in ['unknown', 'unavailable', 'none', ''] "
            "and (value | float(none)) is not none }}",
        )
        self.assertEqual(
            sensor.get("attributes"),
            {"sampled_at": "{{ now().isoformat() }}"},
        )


if __name__ == "__main__":
    unittest.main()
