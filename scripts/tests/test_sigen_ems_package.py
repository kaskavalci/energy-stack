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


class SigenNativeRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.restore = script_block(
            "sigen_restore_native_control", "sigen_disable_remote_ems"
        )

    def test_restore_is_restartable_and_confirms_native_before_limit_writes(self) -> None:
        self.assertIn("mode: restart", self.restore)
        select_position = self.restore.index("service: select.select_option")
        first_wait_position = self.restore.index("- wait_template:")
        target_calculation_position = self.restore.index("charge_device_max_kw:")
        charge_write_position = self.restore.index(
            "entity_id: number.__SIGEN_PLANT_SLUG___plant_max_charging_limit"
        )
        self.assertLess(select_position, first_wait_position)
        self.assertLess(first_wait_position, target_calculation_position)
        self.assertLess(target_calculation_position, charge_write_position)
        self.assertIn('timeout: "00:01:15"', self.restore)

    def test_restore_clamps_both_operator_caps_to_device_maxima(self) -> None:
        self.assertIn("charge_device_max_kw", self.restore)
        self.assertIn("discharge_device_max_kw", self.restore)
        self.assertIn(
            "[charge_cap_kw | float(0), charge_device_max_kw | float(0)]",
            self.restore,
        )
        self.assertIn(
            "[discharge_cap_kw | float(0), discharge_device_max_kw | float(0)]",
            self.restore,
        )
        self.assertGreaterEqual(self.restore.count("| min | round(2)"), 2)

    def test_restore_records_timestamp_only_after_limit_confirmation(self) -> None:
        second_wait_position = self.restore.rindex("- wait_template:")
        timestamp_position = self.restore.index(
            "entity_id: input_datetime.emhass_last_limit_write"
        )
        self.assertLess(second_wait_position, timestamp_position)
        self.assertIn('timeout: "00:00:30"', self.restore)
        self.assertIn("Native fallback mode confirmation failed", self.restore)
        self.assertIn("Native fallback limit confirmation failed", self.restore)

    def test_disable_wrapper_is_restartable(self) -> None:
        disable = script_block(
            "sigen_disable_remote_ems", "sigen_apply_emhass_plan"
        )
        self.assertIn("mode: restart", disable)
        self.assertIn("service: script.sigen_restore_native_control", disable)


if __name__ == "__main__":
    unittest.main()
