"""Structural regression tests for the Sigenergy EMS Home Assistant package."""

from __future__ import annotations

import re
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


D2_CONTROL_FAILURE_REASONS = frozenset({"stale_plan", "bad_optim"})
RESTORE_CALLER_REASONS = {
    "sigen_disable_remote_ems": ("master_off",),
    "emhass_sigen_kill_switch_off": ("master_off",),
    "emhass_sigen_enforce_master_off": ("startup_or_reconnect",),
    "emhass_sigen_disable_on_stale_plan": ("stale_plan",),
    "emhass_sigen_disable_on_bad_optim": ("bad_optim",),
    "emhass_soc_floor_cutout": ("soc_floor",),
    "emhass_recovery_reconcile": ("recovery", "recovery", "recovery"),
}


def restore_service_calls(text: str) -> list[str]:
    calls: list[str] = []
    for match in re.finditer(
        r"^(?P<indent>\s*)- service: script\.sigen_restore_native_control\n"
        r"(?P<body>.*?)(?=^(?P=indent)- |\Z)",
        text,
        re.S | re.M,
    ):
        calls.append(match.group("body"))
    return calls


def restore_call_reason(call_block: str) -> str | None:
    match = re.search(r"reason:\s*(\w+)", call_block)
    return match.group(1) if match else None


def disable_remote_ems_calls(text: str) -> list[str]:
    calls: list[str] = []
    for match in re.finditer(
        r"^(?P<indent>\s*)- service: script\.sigen_disable_remote_ems\n"
        r"(?P<body>.*?)(?=^(?P=indent)- |\Z)",
        text,
        re.S | re.M,
    ):
        calls.append(match.group("body"))
    return calls


def disable_call_reason(call_block: str) -> str | None:
    match = re.search(r"reason:\s*(\w+)", call_block)
    return match.group(1) if match else None


RESTORE_AUTOMATION_NEXT = {
    "emhass_sigen_kill_switch_off": "emhass_sigen_enforce_master_off",
    "emhass_sigen_enforce_master_off": "emhass_sigen_apply_plan_scheduled",
    "emhass_sigen_disable_on_stale_plan": "emhass_sigen_disable_on_bad_optim",
    "emhass_sigen_disable_on_bad_optim": "emhass_soc_floor_cutout",
    "emhass_soc_floor_cutout": "emhass_recovery_start",
    # Last automation today; still next-id bounded when a later id appears.
    "emhass_recovery_reconcile": None,
}
DISABLE_REMOTE_EMS_REASON_CALLERS = frozenset(
    {
        "emhass_sigen_kill_switch_off",
        "emhass_sigen_enforce_master_off",
    }
)

CONTROL_FAILURE_GATE_RE = re.compile(
    r"reason in control_failure_reasons"
    r"|reason in \['stale_plan',\s*'bad_optim'\]"
)


def automation_block_bounded(
    automation_id: str,
    next_automation_id: str | None = None,
) -> str:
    """Return one automation body, cut at the next id or explicit successor."""
    body = package_text().split(f"  - id: {automation_id}\n", 1)[1]
    if next_automation_id is not None:
        return body.split(f"  - id: {next_automation_id}\n", 1)[0]
    next_id = body.find("\n  - id: ")
    if next_id == -1:
        return body
    return body[:next_id]


def choose_branch_and_default(choose_body: str, choose_indent: str) -> tuple[str, str]:
    default_key = f"{choose_indent}  default:\n"
    if default_key not in choose_body:
        return choose_body, ""
    branches, default = choose_body.split(default_key, 1)
    return branches, default


def iter_choose_blocks(text: str) -> list[tuple[str, str]]:
    """Yield (branches_text, default_text) for each top-level `- choose:` block."""
    blocks: list[tuple[str, str]] = []
    for match in re.finditer(r"^(?P<indent>\s*)- choose:\n", text, re.M):
        indent = match.group("indent")
        rest = text[match.end() :]
        end = re.search(rf"^{re.escape(indent)}- ", rest, re.M)
        body = rest if end is None else rest[: end.start()]
        blocks.append(choose_branch_and_default(body, indent))
    return blocks


class SigenRestoreReasonContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.package = package_text()
        self.restore = script_block(
            "sigen_restore_native_control", "sigen_disable_remote_ems"
        )

    def test_restore_script_declares_reason_field(self) -> None:
        self.assertIn("fields:", self.restore)
        reason_block = self.restore.split("reason:", 1)[1]
        self.assertIn("selector:", reason_block)
        self.assertIn("default: unknown", self.restore)

    def test_every_restore_caller_passes_reason(self) -> None:
        for automation_id, expected_reasons in RESTORE_CALLER_REASONS.items():
            if automation_id == "sigen_disable_remote_ems":
                block = script_block(
                    automation_id, "sigen_clear_recovery"
                )
            elif automation_id in RESTORE_AUTOMATION_NEXT:
                block = automation_block_bounded(
                    automation_id,
                    RESTORE_AUTOMATION_NEXT[automation_id],
                )
            else:
                self.fail(f"unknown restore caller mapping for {automation_id}")
            with self.subTest(automation_id=automation_id):
                if automation_id == "sigen_disable_remote_ems":
                    self.assertIn("default: master_off", block)
                    calls = restore_service_calls(block)
                    self.assertEqual(len(calls), 1)
                    self.assertIn('reason: "{{ reason }}"', calls[0])
                    continue
                if automation_id in DISABLE_REMOTE_EMS_REASON_CALLERS:
                    disable_calls = disable_remote_ems_calls(block)
                    self.assertEqual(len(disable_calls), 1)
                    self.assertEqual(
                        disable_call_reason(disable_calls[0]),
                        expected_reasons[0],
                    )
                    continue
                calls = restore_service_calls(block)
                self.assertEqual(
                    len(calls),
                    len(expected_reasons),
                    msg=f"restore call count mismatch in {automation_id}",
                )
                for index, (call, expected_reason) in enumerate(
                    zip(calls, expected_reasons)
                ):
                    with self.subTest(automation_id=automation_id, call_index=index):
                        self.assertEqual(
                            restore_call_reason(call),
                            expected_reason,
                            msg=f"restore call {index} missing reason in {automation_id}",
                        )

    def test_idle_apply_plan_restore_passes_idle_reason(self) -> None:
        apply = self.package.split("  sigen_apply_emhass_plan:\n", 1)[1].split(
            "\nautomation:\n", 1
        )[0]
        calls = restore_service_calls(apply)
        self.assertEqual(len(calls), 1)
        self.assertEqual(restore_call_reason(calls[0]), "idle")

    def test_timeout_text_uses_live_master_mode_and_limit_state(self) -> None:
        self.assertNotIn("Master is off, but", self.restore)
        self.assertIn("input_boolean.emhass_auto_control_master", self.restore)
        self.assertIn(
            "select.__SIGEN_PLANT_SLUG___plant_remote_ems_control_mode",
            self.restore,
        )
        self.assertIn(
            "number.__SIGEN_PLANT_SLUG___plant_max_charging_limit",
            self.restore,
        )

    def test_d2_signature_contains_reason_and_phase(self) -> None:
        self.assertIn("input_text.emhass_d2_failure_signature", self.package)
        self.assertIn("input_datetime.emhass_d2_last_failure", self.package)
        self.assertIn("input_number.emhass_d2_failure_count", self.package)
        self.assertIn("emhass_d2_failure_signature", self.restore)
        self.assertIn("{{ reason ~ '|' ~ phase }}", self.restore)
        self.assertIn("native_mode_confirmation", self.restore)
        self.assertIn("limit_confirmation", self.restore)

    def test_d2_same_context_escalation_dedupe_and_resolve(self) -> None:
        """Second same-signature timeout within 10 min may phone; duplicates suppress."""
        self.assertIn("10 * 60", self.restore)
        self.assertIn("previous_count == 1", self.restore)
        self.assertIn("previous_count >= 2", self.restore)
        self.assertIn("input_boolean.emhass_d2_actionable_notified", self.restore)
        self.assertIn("phase: recovering", self.restore)
        self.assertIn("phase: actionable", self.restore)
        self.assertIn("phase: suppressed", self.restore)
        self.assertIn("phase: resolved", self.restore)
        self.assertIn("input_number.emhass_d2_failure_count", self.restore)
        self.assertRegex(
            self.restore,
            r"entity_id: input_number\.emhass_d2_failure_count\n"
            r"(?:.*\n){0,3}\s+value: 0",
        )
        self.assertRegex(
            self.restore,
            r"entity_id: input_text\.emhass_d2_failure_signature\n"
            r"(?:.*\n){0,3}\s+value: \"\"",
        )
        # Phone-sent marker must arm before helper writes.
        for title in (
            "EMHASS D2: Native fallback unconfirmed",
            "EMHASS D2: Native fallback limits unconfirmed",
        ):
            marker = f'title: "{title}"'
            self.assertIn(marker, self.restore)
            after_title = self.restore.split(marker, 1)[1]
            turn_on_pos = after_title.index(
                "entity_id: input_boolean.emhass_d2_actionable_notified"
            )
            sig_pos = after_title.index(
                "entity_id: input_text.emhass_d2_failure_signature"
            )
            self.assertLess(turn_on_pos, sig_pos)
            between = after_title[:sig_pos]
            self.assertIn("input_boolean.turn_on", between)
            self.assertNotIn("continue_on_error: true", between)

    def test_intentional_reasons_do_not_use_phone_alert_path(self) -> None:
        timeout_section = self.restore.split("continue_on_timeout: true", 1)[1]
        direct_push = re.search(
            r"not wait\.completed.*?then:\s*\n\s*- service: script\.emhass_push_alert",
            timeout_section,
            re.S,
        )
        self.assertIsNone(
            direct_push,
            "timeout must not call push_alert unconditionally; "
            "intentional reasons use log_event only",
        )
        self.assertIn("script.emhass_log_event", timeout_section)
        self.assertNotIn("intentional_reasons:", timeout_section)
        for reason in D2_CONTROL_FAILURE_REASONS:
            with self.subTest(reason=reason):
                self.assertIn(f"'{reason}'", timeout_section)

        chooses = iter_choose_blocks(timeout_section)
        self.assertGreaterEqual(
            len(chooses),
            1,
            "timeout handling must use choose for phone gating",
        )
        for choose_idx, (branches, default) in enumerate(chooses):
            with self.subTest(choose_index=choose_idx):
                self.assertNotIn(
                    "script.emhass_push_alert",
                    default,
                    "choose default must not phone; intentional/recovering path is log-only",
                )
                for push in re.finditer(
                    r"service: script\.emhass_push_alert",
                    branches,
                ):
                    preceding = branches[: push.start()]
                    cond_pos = preceding.rfind("- conditions:")
                    self.assertNotEqual(
                        cond_pos,
                        -1,
                        "push_alert must sit under a choose branch with conditions",
                    )
                    branch_gate = preceding[cond_pos:push.start()]
                    self.assertRegex(
                        branch_gate,
                        CONTROL_FAILURE_GATE_RE,
                        "push_alert branch must gate on control-failure reasons",
                    )
                    # Notify first; advance D2 helpers only after successful push.
                    seq_pos = preceding.rfind("sequence:")
                    self.assertNotEqual(seq_pos, -1)
                    seq = branches[seq_pos:]
                    next_branch = seq.find("\n              - conditions:")
                    if next_branch != -1:
                        seq = seq[:next_branch]
                    push_in_seq = seq.index("service: script.emhass_push_alert")
                    marker_in_seq = seq.index(
                        "entity_id: input_boolean.emhass_d2_actionable_notified"
                    )
                    sig_in_seq = seq.index(
                        "entity_id: input_text.emhass_d2_failure_signature"
                    )
                    count_in_seq = seq.index(
                        "entity_id: input_number.emhass_d2_failure_count"
                    )
                    self.assertLess(push_in_seq, marker_in_seq)
                    self.assertLess(marker_in_seq, sig_in_seq)
                    self.assertLess(push_in_seq, count_in_seq)
                    push_block = seq[
                        seq.rfind("- service:", 0, push_in_seq) : marker_in_seq
                    ]
                    self.assertNotIn("continue_on_error: true", push_block)
                    # Phone-sent marker and helpers must not ignore write failures.
                    helper_chunk = seq[marker_in_seq:]
                    log_after = helper_chunk.find(
                        "service: script.emhass_log_event"
                    )
                    if log_after != -1:
                        helper_chunk = helper_chunk[:log_after]
                    self.assertNotIn("continue_on_error: true", helper_chunk)
                    self.assertIn("input_boolean.turn_on", seq[push_in_seq:sig_in_seq])


class SigenFailureFallbackNotificationTests(unittest.TestCase):
    def test_stale_plan_logs_detection_and_defers_phone_to_restore(self) -> None:
        stale = automation_block(
            "emhass_sigen_disable_on_stale_plan",
            "emhass_sigen_disable_on_bad_optim",
        )
        self.assertIn("service: script.emhass_log_event", stale)
        self.assertNotIn("service: script.emhass_push_alert", stale)
        calls = restore_service_calls(stale)
        self.assertEqual(len(calls), 1)
        self.assertEqual(restore_call_reason(calls[0]), "stale_plan")

    def test_bad_optim_logs_detection_and_defers_phone_to_restore(self) -> None:
        bad_optim = automation_block(
            "emhass_sigen_disable_on_bad_optim",
            "emhass_soc_floor_cutout",
        )
        self.assertIn("service: script.emhass_log_event", bad_optim)
        self.assertNotIn("service: script.emhass_push_alert", bad_optim)
        calls = restore_service_calls(bad_optim)
        self.assertEqual(len(calls), 1)
        self.assertEqual(restore_call_reason(calls[0]), "bad_optim")


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


class EmhassBatteryPowerSignAlignTests(unittest.TestCase):
    """Sigen plant_battery_power (+charge) vs EMHASS p_batt (−charge)."""

    def setUp(self) -> None:
        self.package = DASHBOARD_PACKAGE_PATH.read_text(encoding="utf-8")

    def test_no_template_sign_flip_sensor(self) -> None:
        # Prefer ApexCharts invert on the live Sigen entity (keeps recorder history).
        self.assertNotIn("emhass_battery_power_emhass_sign_w", self.package)
        self.assertNotIn("EMHASS battery power (EMHASS sign)", self.package)


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
        self.assertIn("state_class: total", today)
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
            "EMHASS recovery target SOC",
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
