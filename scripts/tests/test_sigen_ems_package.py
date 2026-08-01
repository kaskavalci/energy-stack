"""Structural regression tests for the Sigenergy EMS Home Assistant package."""

from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_PATH = REPO_ROOT / "home-assistant" / "ha_sigen_ems.yaml"
DASHBOARD_PACKAGE_PATH = REPO_ROOT / "home-assistant" / "ha_emhass_dashboard.yaml"
MONITORING_PACKAGE_PATH = REPO_ROOT / "home-assistant" / "ha_emhass_monitoring.yaml"


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
            "sigen_disable_remote_ems", "sigen_clear_recovery"
        )
        self.assertIn("mode: restart", disable)
        self.assertIn("service: script.sigen_restore_native_control", disable)


class SigenReconnectReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.automation = automation_block(
            "emhass_sigen_enforce_master_off",
            "emhass_sigen_apply_plan_scheduled",
        )

    def test_reconnect_and_ha_start_have_distinct_trigger_ids(self) -> None:
        self.assertIn("- id: command_mode", self.automation)
        self.assertIn("- id: reconnect", self.automation)
        self.assertIn("- id: ha_start", self.automation)
        self.assertIn("platform: homeassistant", self.automation)
        self.assertIn('"unknown"', self.automation)
        self.assertIn('"unavailable"', self.automation)

    def test_native_reconnect_and_ha_start_are_not_filtered_out(self) -> None:
        self.assertIn("trigger.id in ['reconnect', 'ha_start']", self.automation)
        self.assertIn("service: script.sigen_disable_remote_ems", self.automation)


class EmhassPlanValidityPresentationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.package = (
            REPO_ROOT / "home-assistant" / "ha_emhass_dashboard.yaml"
        ).read_text(encoding="utf-8")

    def test_valid_plan_stays_positive_and_problem_is_separate(self) -> None:
        valid = self.package.split(
            "      - name: EMHASS plan valid\n", 1
        )[1].split("      - name: EMHASS plan problem\n", 1)[0]
        self.assertNotIn("device_class: problem", valid)
        self.assertIn("      - name: EMHASS plan problem\n", self.package)
        problem = self.package.split(
            "      - name: EMHASS plan problem\n", 1
        )[1].split("      - name: EMHASS control allowed\n", 1)[0]
        self.assertIn("device_class: problem", problem)
        self.assertIn(
            'state: "{{ not is_state(\'binary_sensor.emhass_mpc_fresh\', \'on\') }}"',
            problem,
        )


class EmhassNetElectricityProfitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dashboard = DASHBOARD_PACKAGE_PATH.read_text(encoding="utf-8")

    def _sensor_block(self, name: str, next_name: str) -> str:
        return self.dashboard.split(f"      - name: {name}\n", 1)[1].split(
            f"      - name: {next_name}\n", 1
        )[0]

    def test_today_profit_is_production_minus_delivery(self) -> None:
        today = self._sensor_block(
            "EMHASS net electricity profit today",
            "EMHASS net electricity profit month",
        )
        self.assertIn("default_entity_id: sensor.emhass_net_electricity_profit_today", today)
        self.assertIn("unit_of_measurement: EUR", today)
        self.assertIn("device_class: monetary", today)
        self.assertIn("state_class: measurement", today)
        self.assertIn(
            "sensor.zonneplan_electricity_production_costs_today", today
        )
        self.assertIn(
            "sensor.zonneplan_electricity_delivery_costs_today", today
        )
        self.assertIn("production - delivery", today)
        self.assertIn("unknown", today)
        self.assertNotIn("excl_tax", today)
        self.assertIn("production:", today)
        self.assertIn("delivery:", today)

    def test_month_profit_is_production_minus_delivery(self) -> None:
        month = self._sensor_block(
            "EMHASS net electricity profit month",
            "EMHASS net electricity profit daily",
        )
        self.assertIn("default_entity_id: sensor.emhass_net_electricity_profit_month", month)
        self.assertIn(
            "sensor.zonneplan_electricity_production_costs_this_month", month
        )
        self.assertIn(
            "sensor.zonneplan_electricity_delivery_costs_this_month", month
        )
        self.assertIn("production - delivery", month)
        self.assertIn("unknown", month)
        self.assertNotIn("excl_tax", month)

    def test_daily_profit_snapshot_contract(self) -> None:
        package = self.dashboard
        self.assertIn("EMHASS net electricity profit daily", package)
        self.assertIn(
            "default_entity_id: sensor.emhass_net_electricity_profit_daily",
            package,
        )
        daily = self._sensor_block(
            "EMHASS net electricity profit daily",
            "EMHASS recovery target SOC",
        )
        self.assertIn('at: "23:55:00"', package)
        # Trigger block must sit above the daily sensor name in the file.
        trigger_idx = package.index('at: "23:55:00"')
        name_idx = package.index("EMHASS net electricity profit daily")
        self.assertLess(trigger_idx, name_idx)
        self.assertIn("unit_of_measurement: EUR", daily)
        self.assertIn("device_class: monetary", daily)
        self.assertIn("state_class: measurement", daily)
        self.assertIn(
            "sensor.zonneplan_electricity_production_costs_today", daily
        )
        self.assertIn(
            "sensor.zonneplan_electricity_delivery_costs_today", daily
        )
        self.assertIn("production - delivery", daily)
        self.assertIn("for_date:", daily)
        self.assertIn("production:", daily)
        self.assertIn("delivery:", daily)
        self.assertNotIn("excl_tax", daily)
        # Skip write when sources bad: template condition (not overwrite with unknown/0).
        condition_region = package[trigger_idx:name_idx]
        self.assertIn("condition:", condition_region)
        self.assertIn("float(none) is not none", condition_region)


class EmhassMpcFreshnessContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dashboard = DASHBOARD_PACKAGE_PATH.read_text(encoding="utf-8")
        self.monitoring = MONITORING_PACKAGE_PATH.read_text(encoding="utf-8")

    def test_source_controlled_freshness_uses_success_marker(self) -> None:
        fresh = self.dashboard.split(
            "      - name: EMHASS MPC fresh\n", 1
        )[1].split("      - name: EMHASS plan valid\n", 1)[0]
        self.assertIn("input_datetime.emhass_last_successful_mpc", fresh)
        # Use input_datetime timestamp attr — naive state strings break when
        # container TZ ≠ HA time_zone (false plan_stale / negative age_min).
        self.assertIn(
            "state_attr('input_datetime.emhass_last_successful_mpc', 'timestamp')",
            fresh,
        )
        self.assertNotIn("as_timestamp(completed", fresh)
        self.assertIn("age_s >= -5 * 60", fresh)
        self.assertIn("age_s < 40 * 60", fresh)
        self.assertIn("sensor.p_batt_forecast", fresh)
        self.assertIn("sensor.optim_status", fresh)
        self.assertIn("reason:", fresh)

    def test_control_gate_consumes_central_freshness_reason(self) -> None:
        control = self.dashboard.split(
            "      - name: EMHASS control allowed\n", 1
        )[1]
        self.assertIn("binary_sensor.emhass_mpc_fresh", control)
        self.assertIn("fresh_reason", control)
        self.assertNotIn("states.sensor.p_batt_forecast.last_updated", control)

    def test_validity_and_stale_monitor_use_central_sensor(self) -> None:
        self.assertIn(
            'state: "{{ is_state(\'binary_sensor.emhass_mpc_fresh\', \'on\') }}"',
            self.dashboard,
        )
        stale = self.monitoring.split(
            "      - name: EMHASS fail plan stale\n", 1
        )[1].split("      - name: EMHASS fail PV sensor\n", 1)[0]
        self.assertIn("binary_sensor.emhass_mpc_fresh", stale)
        self.assertIn("plan_stale", stale)
        self.assertNotIn("p_batt_forecast.last_updated", stale)


class SigenIdleSelfConsumptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.apply = package_text().split(
            "  sigen_apply_emhass_plan:\n", 1
        )[1].split("\nautomation:\n", 1)[0]

    def floor_cutout(self) -> str:
        return automation_block(
            "emhass_soc_floor_cutout", "emhass_recovery_start"
        )

    def test_floor_cutout_excludes_native_mode(self) -> None:
        floor = self.floor_cutout()
        trigger = floor.split("    trigger:\n", 1)[1].split(
            "    condition:\n", 1
        )[0]
        condition = floor.split("    condition:\n", 1)[1].split(
            "    action:\n", 1
        )[0]
        native_mode = "Maximum Self-consumption (Default)"
        discharge_modes = [
            "Command Discharging (Output power from PV first)",
            "Command Discharging (Output power from the battery first)",
        ]
        self.assertNotIn(native_mode, trigger)
        self.assertNotIn(native_mode, condition)
        for discharge_mode in discharge_modes:
            self.assertIn(discharge_mode, trigger)
            self.assertIn(discharge_mode, condition)

    def test_floor_cutout_restores_concurrent_idle_after_hard_stop(self) -> None:
        action = self.floor_cutout().split("    action:\n", 1)[1]
        reconcile = action.split("      - if:\n", 1)[1]
        self.assertIn("p >= (0 - threshold)", action)
        self.assertIn("p <= threshold", action)
        self.assertIn("entity_id: input_boolean.emhass_recovery_active", reconcile)
        self.assertIn('state: "off"', reconcile)
        self.assertIn("service: script.sigen_restore_native_control", action)
        self.assertLess(
            action.index("service: script.sigen_apply_idle_limits"),
            action.index("service: script.sigen_restore_native_control"),
        )
        self.assertLess(
            action.index("service: logbook.log"),
            action.index("service: script.sigen_restore_native_control"),
        )

    def test_floor_cutout_is_inclusive_and_keeps_hard_stop(self) -> None:
        floor = self.floor_cutout()
        self.assertGreaterEqual(floor.count("0 <= soc <= min_soc"), 2)
        self.assertIn("service: script.sigen_set_ems_mode", floor)
        self.assertIn("mode: idle", floor)
        self.assertIn("service: script.sigen_apply_idle_limits", floor)

    def test_floor_cutout_is_restartable_and_preempts_limit_writers(self) -> None:
        floor = self.floor_cutout()
        action = floor.split("    action:\n", 1)[1]
        preempt = action.split(
            "      - service: script.sigen_set_ems_mode\n", 1
        )[0]
        self.assertIn("mode: restart", floor)
        self.assertIn("service: script.turn_off", preempt)
        self.assertIn("script.sigen_apply_emhass_plan", preempt)
        self.assertIn("script.sigen_restore_native_control", preempt)
        self.assertIn("script.sigen_set_ems_mode", preempt)
        self.assertIn("script.sigen_apply_discharge_limits", preempt)
        self.assertNotIn("script.sigen_apply_charge_limits", preempt)
        self.assertNotIn("script.sigen_start_recovery", preempt)
        self.assertNotIn("script.sigen_apply_active_recovery", preempt)

    def test_floor_cutout_preempts_before_standby_and_zero_limits(self) -> None:
        action = self.floor_cutout().split("    action:\n", 1)[1]
        self.assertIn("service: script.turn_off", action)
        preempt_position = action.index("service: script.turn_off")
        standby_position = action.index("service: script.sigen_set_ems_mode")
        zero_limits_position = action.index(
            "service: script.sigen_apply_idle_limits"
        )
        self.assertLess(preempt_position, standby_position)
        self.assertLess(preempt_position, zero_limits_position)

    def test_true_deadband_uses_confirmed_native_restoration(self) -> None:
        marker = (
            'value_template: "{{ p >= (0 - threshold) '
            'and p <= threshold }}"'
        )
        self.assertIn(marker, self.apply)
        branch = self.apply.split(marker, 1)[1].split("        default:\n", 1)[0]
        self.assertIn("service: script.sigen_restore_native_control", branch)
        self.assertNotIn("service: script.sigen_apply_idle_limits", branch)

    def test_rejected_positive_discharge_reaches_standby_not_native(self) -> None:
        discharge_guard = (
            'value_template: "{{ p > threshold and discharge_limit >= threshold '
            'and soc > min_soc_frac * 100 }}"'
        )
        native_guard = (
            'value_template: "{{ p >= (0 - threshold) '
            'and p <= threshold }}"'
        )
        self.assertIn(discharge_guard, self.apply)
        self.assertIn(native_guard, self.apply)
        native_branch = self.apply.split(native_guard, 1)[1].split(
            "        default:\n", 1
        )[0]
        self.assertNotIn("discharge_limit", native_branch)
        fallback = self.apply.split("        default:\n", 1)[1]
        self.assertIn("service: script.sigen_set_ems_mode", fallback)
        self.assertIn("mode: idle", fallback)
        self.assertIn("service: script.sigen_apply_idle_limits", fallback)

    def test_final_fallback_remains_standby_with_zero_limits(self) -> None:
        fallback = self.apply.split("        default:\n", 1)[1]
        self.assertIn("service: script.sigen_set_ems_mode", fallback)
        self.assertIn("mode: idle", fallback)
        self.assertIn("service: script.sigen_apply_idle_limits", fallback)
        self.assertNotIn("service: script.sigen_restore_native_control", fallback)


class EmhassRecoveryStateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.package = package_text()

    def test_normal_apply_uses_plan_completion_control_enable_and_safe_retry(self) -> None:
        normal_apply = automation_block(
            "emhass_sigen_apply_plan_scheduled",
            "emhass_sigen_disable_on_stale_plan",
        )
        self.assertIn(
            """- id: plan_completed
        platform: state
        entity_id: input_datetime.emhass_last_successful_mpc""",
            normal_apply,
        )
        self.assertIn(
            """- id: control_enabled
        platform: state
        entity_id: binary_sensor.emhass_control_allowed
        to: "on\"""",
            normal_apply,
        )
        self.assertIn(
            """- id: retry
        platform: time_pattern
        minutes: "/5\"""",
            normal_apply,
        )
        self.assertIn(
            """{{ trigger.id != 'retry'
             or now().minute not in [0, 30] }}""",
            normal_apply,
        )
        self.assertIn(
            "state_attr('input_datetime.emhass_last_limit_write', 'timestamp')",
            normal_apply,
        )
        self.assertIn(
            "{{ (as_timestamp(now()) - last_ts) >= 300 }}",
            normal_apply,
        )
        self.assertNotIn("as_timestamp(last)", normal_apply)
        self.assertNotIn('minutes: "/30"', normal_apply)
        self.assertIn("binary_sensor.emhass_control_allowed", normal_apply)
        self.assertIn("input_boolean.emhass_recovery_active", normal_apply)

    def test_recovery_helpers_are_restart_restored(self) -> None:
        boolean = self.package.split("input_boolean:\n", 1)[1].split(
            "input_number:\n", 1
        )[0]
        self.assertIn("emhass_recovery_active:", boolean)
        self.assertNotIn("initial:", boolean.split("emhass_recovery_active:", 1)[1])
        number = self.package.split("  emhass_recovery_charge_w:\n", 1)[1].split(
            "  emhass_soc_target:\n", 1
        )[0]
        self.assertNotIn("initial:", number)

    def test_recovery_start_requires_floor_freshness_and_charge(self) -> None:
        start = automation_block(
            "emhass_recovery_start", "emhass_recovery_reconcile"
        )
        self.assertIn("binary_sensor.emhass_mpc_fresh", start)
        self.assertIn("below: input_number.emhass_soc_min", start)
        self.assertIn("p < (0 - threshold)", start)
        self.assertIn("service: script.sigen_start_recovery", start)
        self.assertNotIn("sensor.emhass_mpc_status", start)

    def test_recovery_start_requires_soc_in_valid_range(self) -> None:
        start = automation_block(
            "emhass_recovery_start", "emhass_recovery_reconcile"
        )
        conditions = start.split("    condition:\n", 1)[1].split(
            "    action:\n", 1
        )[0]
        valid_soc = """{% set soc = states('sensor.__SIGEN_PLANT_SLUG___plant_battery_soc') | float(none) %}
          {{ soc is not none and 0 <= soc <= 100 }}"""
        self.assertIn(valid_soc, conditions)
        self.assertLess(
            conditions.index(valid_soc),
            conditions.index("below: input_number.emhass_soc_min"),
        )
        self.assertLess(
            start.index(valid_soc),
            start.index("service: script.sigen_start_recovery"),
        )

    def test_active_recovery_owns_actuator_until_target(self) -> None:
        reconcile = self.package.split(
            "  - id: emhass_recovery_reconcile\n", 1
        )[1]
        self.assertIn('minutes: "/5"', reconcile)
        self.assertIn("platform: homeassistant", reconcile)
        self.assertIn("id: reconnect", reconcile)
        self.assertIn("soc >= target", reconcile)
        self.assertIn("service: script.sigen_apply_active_recovery", reconcile)
        self.assertIn("service: script.sigen_clear_recovery", reconcile)
        self.assertIn("service: script.sigen_apply_emhass_plan", reconcile)
        self.assertIn("service: script.sigen_restore_native_control", reconcile)

    def test_recovery_target_trigger_is_inclusive_and_soc_valid(self) -> None:
        reconcile = self.package.split(
            "  - id: emhass_recovery_reconcile\n", 1
        )[1]
        triggers = reconcile.split("    trigger:\n", 1)[1].split(
            "    condition:\n", 1
        )[0]
        target_reached = triggers.split("      - id: target_reached\n", 1)[1].split(
            "      - id: target_changed\n", 1
        )[0]
        self.assertIn("platform: template", target_reached)
        self.assertIn(
            "states('sensor.__SIGEN_PLANT_SLUG___plant_battery_soc') | float(none)",
            target_reached,
        )
        self.assertIn(
            "states('sensor.emhass_recovery_target_soc') | float(none)",
            target_reached,
        )
        self.assertIn("soc is not none", target_reached)
        self.assertIn("target is not none", target_reached)
        self.assertIn("0 <= soc <= 100", target_reached)
        self.assertIn("soc >= target", target_reached)
        self.assertNotIn(
            "above: sensor.emhass_recovery_target_soc",
            triggers,
        )

    def test_active_recovery_always_applies_clamped_charge_limit(self) -> None:
        active = script_block(
            "sigen_apply_active_recovery", "sigen_apply_emhass_plan"
        )
        self.assertIn("service: script.sigen_apply_charge_limits", active)
        self.assertIn('watts: "{{ charge_limit_w }}"', active)
        self.assertNotIn("emhass_power_threshold_w", active)

    def test_reconcile_clears_only_for_master_invalid_soc_or_target(self) -> None:
        reconcile = self.package.split(
            "  - id: emhass_recovery_reconcile\n", 1
        )[1]
        self.assertNotIn("stored_w < threshold", reconcile)
        self.assertNotIn(
            "SOC recovery cancelled because its stored setpoint was invalid.",
            reconcile,
        )
        self.assertIn("emhass_auto_control_master", reconcile)
        self.assertIn("trigger.id == 'invalid_soc'", reconcile)
        self.assertIn(
            "soc is not none and 0 <= soc <= 100 and soc >= target",
            reconcile,
        )
        self.assertEqual(
            reconcile.count("service: script.sigen_clear_recovery"),
            3,
        )

    def test_recovery_completion_requires_soc_in_valid_range(self) -> None:
        reconcile = self.package.split(
            "  - id: emhass_recovery_reconcile\n", 1
        )[1]
        action = reconcile.split("    action:\n", 1)[1]
        after_invalid = action.split(
            'message: "SOC recovery cancelled because SOC was invalid for two minutes."\n',
            1,
        )[1]
        completion_conditions = after_invalid.split(
            "            sequence:\n", 1
        )[0]
        self.assertIn(
            'value_template: "{{ soc is not none and 0 <= soc <= 100 and soc >= target }}"',
            completion_conditions,
        )

    def test_ordinary_apply_and_failure_fallback_skip_active_recovery(self) -> None:
        scheduled = automation_block(
            "emhass_sigen_apply_plan_scheduled",
            "emhass_sigen_disable_on_stale_plan",
        )
        stale = automation_block(
            "emhass_sigen_disable_on_stale_plan",
            "emhass_sigen_disable_on_bad_optim",
        )
        bad_optim = automation_block(
            "emhass_sigen_disable_on_bad_optim",
            "emhass_soc_floor_cutout",
        )
        for block in (scheduled, stale, bad_optim):
            self.assertIn("input_boolean.emhass_recovery_active", block)
            self.assertIn('state: "off"', block)

    def test_master_off_clears_recovery_before_native_restore(self) -> None:
        kill = automation_block(
            "emhass_sigen_kill_switch_off",
            "emhass_sigen_enforce_master_off",
        )
        clear_pos = kill.index("service: script.sigen_clear_recovery")
        native_pos = kill.index("service: script.sigen_disable_remote_ems")
        self.assertLess(clear_pos, native_pos)

    def test_recovery_stalled_monitor_observes_explicit_state(self) -> None:
        monitoring = MONITORING_PACKAGE_PATH.read_text(encoding="utf-8")
        stalled = monitoring.split(
            "  - id: emhass_notify_D3_recovery_stalled\n", 1
        )[1]
        self.assertIn("input_boolean.emhass_recovery_active", stalled)
        self.assertIn("id: active_recovery", stalled)
        self.assertIn("id: waiting_below_floor", stalled)


if __name__ == "__main__":
    unittest.main()
