"""Structural regression tests for the Sigenergy EMS Home Assistant package."""

from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_PATH = REPO_ROOT / "home-assistant" / "ha_sigen_ems.yaml"


def package_text() -> str:
    return PACKAGE_PATH.read_text(encoding="utf-8")


def script_block(name: str, next_name: str) -> str:
    text = package_text()
    return text.split(f"  {name}:\n", 1)[1].split(f"  {next_name}:\n", 1)[0]


def automation_block(automation_id: str, next_automation_id: str) -> str:
    text = package_text()
    return text.split(f"  - id: {automation_id}\n", 1)[1].split(
        f"  - id: {next_automation_id}\n", 1
    )[0]


class SigenActiveLimitClampTests(unittest.TestCase):
    def test_charge_limit_clamps_to_advertised_device_max(self) -> None:
        charge = script_block(
            "sigen_apply_charge_limits", "sigen_apply_discharge_limits"
        )
        self.assertIn(
            "state_attr('number.__SIGEN_PLANT_SLUG___plant_max_charging_limit', 'max')",
            charge,
        )
        self.assertIn(
            "[requested_kw | float(0), device_max_kw | float(0)] | min",
            charge,
        )

    def test_discharge_limit_clamps_to_advertised_device_max(self) -> None:
        discharge = script_block(
            "sigen_apply_discharge_limits", "sigen_apply_idle_limits"
        )
        self.assertIn(
            "state_attr('number.__SIGEN_PLANT_SLUG___plant_max_discharging_limit', 'max')",
            discharge,
        )
        self.assertIn(
            "[requested_kw | float(0), device_max_kw | float(0)] | min",
            discharge,
        )


if __name__ == "__main__":
    unittest.main()
