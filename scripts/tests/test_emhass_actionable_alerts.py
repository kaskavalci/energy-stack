"""Structural tests for the EMHASS actionable alert escalation HA contract."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MONITORING_PATH = REPO_ROOT / "home-assistant" / "ha_emhass_monitoring.yaml"

DIAGNOSTIC_AUTOMATION_IDS = (
    "emhass_notify_A2_optim_mpc",
    "emhass_notify_A3_flat_prices",
    "emhass_notify_A5_plan_stale",
    "emhass_notify_D1_control_blocked",
)

ESCALATION_CASES = (
    (
        "C1",
        "emhass_notify_C1_pv",
        "emhass_escalate_C1_pv",
        "binary_sensor.emhass_fail_pv_sensor",
        "Check Enphase integration",
    ),
    (
        "C2a",
        "emhass_notify_C2a_house_load",
        "emhass_escalate_C2a_house_load",
        "binary_sensor.emhass_fail_house_load",
        "Check mqtt4dsmr or sigenergy2mqtt",
    ),
    (
        "C2b",
        "emhass_notify_C2b_soc",
        "emhass_escalate_C2b_soc",
        "binary_sensor.emhass_fail_battery_soc",
        "Check sigenergy2mqtt",
    ),
    (
        "E1",
        "emhass_notify_E1_p1",
        "emhass_escalate_E1_p1",
        "binary_sensor.emhass_fail_p1_sensors",
        "Check mqtt4dsmr",
    ),
    (
        "E2",
        "emhass_notify_E2_sigen_mqtt",
        "emhass_escalate_E2_sigen_mqtt",
        "binary_sensor.emhass_fail_sigen_mqtt",
        "Check sigenergy2mqtt",
    ),
)


def monitoring_text() -> str:
    return MONITORING_PATH.read_text(encoding="utf-8")


def script_block(name: str, next_name: str | None = None) -> str:
    text = monitoring_text()
    body = text.split(f"  {name}:\n", 1)[1]
    if next_name is None:
        for section in ("template:", "automation:", "input_text:"):
            if f"\n{section}\n" in body:
                body = body.split(f"\n{section}\n", 1)[0]
                break
        return body
    return body.split(f"  {next_name}:\n", 1)[0]


def automation_block(automation_id: str) -> str:
    text = monitoring_text()
    marker = f"  - id: {automation_id}\n"
    body = text.split(marker, 1)[1]
    next_id = body.find("\n  - id: ")
    if next_id == -1:
        return body
    return body[:next_id]


def input_text_block(name: str) -> str:
    text = monitoring_text()
    section = text.split("input_text:\n", 1)[1]
    body = section.split(f"  {name}:\n", 1)[1]
    next_key = re.search(r"\n  [a-z_]+:", body)
    if next_key:
        return body[: next_key.start()]
    return body.split("\n\n", 1)[0]


class EmhassAlertEventAnchorTests(unittest.TestCase):
    def test_alert_event_input_text_exists_with_bounded_length(self) -> None:
        block = input_text_block("emhass_alert_event")
        self.assertIn("name: EMHASS alert event", block)
        self.assertIn("max: 255", block)


def logbook_service_data(script: str) -> str:
    body = script.split("service: logbook.log\n", 1)[1]
    return body.split("\n      - ", 1)[0]


class EmhassLogEventScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.block = script_block("emhass_log_event", "emhass_push_alert")

    def test_log_event_declares_required_fields(self) -> None:
        for field_name in ("incident_id", "phase", "message", "action"):
            self.assertIn(f"{field_name}:", self.block)

    def test_log_event_action_is_optional_with_empty_default(self) -> None:
        action_field = self.block.split("action:", 1)[1].split("message:", 1)[0]
        self.assertIn("default:", action_field)
        self.assertRegex(action_field, r"default:\s*(?:''|\"\")")

    def test_log_event_sets_anchor_then_writes_logbook(self) -> None:
        self.assertIn("input_text.set_value", self.block)
        self.assertIn("service: logbook.log", self.block)
        logbook_pos = self.block.index("service: logbook.log")
        anchor_pos = self.block.index("input_text.set_value")
        self.assertLess(anchor_pos, logbook_pos)

    def test_log_event_logbook_targets_anchor_entity(self) -> None:
        logbook_data = logbook_service_data(self.block)
        self.assertIn("entity_id: input_text.emhass_alert_event", logbook_data)
        self.assertNotIn(
            "entity_id: input_text.emhass_alert_event",
            self.block.split("service: logbook.log\n", 1)[0],
        )


class DiagnosticAutomationRoutingTests(unittest.TestCase):
    def test_diagnostic_automations_log_without_phone_notifier(self) -> None:
        for automation_id in DIAGNOSTIC_AUTOMATION_IDS:
            block = automation_block(automation_id)
            with self.subTest(automation_id=automation_id):
                self.assertIn("service: script.emhass_log_event", block)
                self.assertNotIn("service: script.emhass_push_alert", block)
                self.assertIn("phase: detected", block)

    def test_d1_binary_excludes_intentional_safe_block_reasons(self) -> None:
        text = monitoring_text()
        marker = "unique_id: emhass_fail_control_blocked\n"
        block = text.split(marker, 1)[1]
        next_sensor = block.find("\n      - name:")
        if next_sensor != -1:
            block = block[:next_sensor]
        self.assertIn("master_off", block)
        self.assertIn("soc_below_floor", block)
        self.assertRegex(
            block,
            r"reason not in \['ok', 'master_off', 'soc_below_floor'\]",
        )


class ThirtyMinuteEscalationRoutingTests(unittest.TestCase):
    def test_impairments_log_at_t_and_phone_at_t_plus_thirty(self) -> None:
        for (
            incident_id,
            detect_id,
            escalate_id,
            fail_binary,
            action_message,
        ) in ESCALATION_CASES:
            detect = automation_block(detect_id)
            escalate = automation_block(escalate_id)
            with self.subTest(incident_id=incident_id):
                detect_trigger = detect.split("action:", 1)[0]
                self.assertIn(f"entity_id: {fail_binary}", detect_trigger)
                self.assertIn('to: "on"', detect_trigger)
                self.assertNotIn("for:", detect_trigger)
                self.assertNotIn("delay:", detect)
                self.assertIn("service: script.emhass_log_event", detect)
                self.assertIn(f"incident_id: {incident_id}", detect)
                self.assertIn("phase: detected", detect)
                self.assertNotIn("service: script.emhass_push_alert", detect)
                self.assertNotIn("phase: actionable", detect)

                escalate_trigger = escalate.split("action:", 1)[0]
                self.assertIn(f"entity_id: {fail_binary}", escalate_trigger)
                self.assertIn('to: "on"', escalate_trigger)
                self.assertRegex(escalate_trigger, r"for:\s*\n\s*minutes:\s*30")
                self.assertNotIn("delay:", escalate)
                self.assertIn(f"entity_id: {fail_binary}", escalate)
                self.assertIn('state: "on"', escalate)
                self.assertIn("phase: actionable", escalate)
                self.assertIn(action_message, escalate)
                self.assertEqual(
                    escalate.count("service: script.emhass_push_alert"),
                    1,
                )
                # Notify first (match D3); log after with continue_on_error.
                escalate_action = escalate.split("action:", 1)[1]
                # Ownership suppress branch (C2a/E1) has log-only; phone is in default.
                if "emhass_load_history_root_active" in escalate_action:
                    self.assertIn("phase: suppressed_duplicate", escalate_action)
                    self.assertIn(
                        "input_boolean.emhass_load_history_root_active",
                        escalate_action,
                    )
                    phone_chunk = escalate_action.split("default:", 1)[1]
                else:
                    phone_chunk = escalate_action
                push_pos = phone_chunk.index(
                    "service: script.emhass_push_alert"
                )
                log_pos = phone_chunk.index(
                    "service: script.emhass_log_event"
                )
                # For ownership paths the first log may be suppress; use phone_chunk.
                # Find actionable log after push.
                actionable_log = phone_chunk.find(
                    "phase: actionable", push_pos
                )
                self.assertNotEqual(actionable_log, -1)
                log_before_actionable = phone_chunk.rfind(
                    "service: script.emhass_log_event", 0, actionable_log
                )
                self.assertLess(push_pos, log_before_actionable)
                self.assertNotIn(
                    "continue_on_error:",
                    phone_chunk[push_pos:log_before_actionable],
                )
                self.assertIn(
                    "continue_on_error: true",
                    phone_chunk[log_before_actionable:],
                )

    def test_recovery_is_logged_with_duration_without_phone(self) -> None:
        block = automation_block("emhass_log_CE_sensor_recovery")
        for incident_id, _, _, fail_binary, _ in ESCALATION_CASES:
            with self.subTest(incident_id=incident_id):
                self.assertIn(f"id: {incident_id}", block)
                self.assertIn(f"entity_id: {fail_binary}", block)
        self.assertIn('from: "on"', block)
        self.assertIn('to: "off"', block)
        self.assertIn("phase: resolved", block)
        self.assertIn("duration", block)
        self.assertIn("restored", block)
        self.assertNotIn("service: script.emhass_push_alert", block)


class C1FailPvSensorGateTests(unittest.TestCase):
    """C1 must consume shared envoy gate; no elevation heuristic."""

    def test_c1_consumes_shared_envoy_gate_not_elevation(self) -> None:
        text = monitoring_text()
        marker = "unique_id: emhass_fail_pv_sensor\n"
        self.assertIn(marker, text)
        block = text.split(marker, 1)[1]
        next_sensor = block.find("\n      - name:")
        if next_sensor != -1:
            block = block[:next_sensor]
        self.assertIn("binary_sensor.emhass_envoy_required", block)
        self.assertNotIn("elevation", block)
        self.assertIn("binary_sensor.emhass_fail_pv_sensor", monitoring_text())
        # Phone path still uses fail_pv_sensor escalate automations (no new alert id).
        self.assertIn("emhass_escalate_C1_pv", text)
        self.assertIn("emhass_notify_C1_pv", text)


class C2aFailHouseLoadSensorTests(unittest.TestCase):
    """C2a must consume binary_sensor.emhass_house_load_valid."""

    def test_c2a_consumes_emhass_house_load_valid(self) -> None:
        text = monitoring_text()
        marker = "unique_id: emhass_fail_house_load\n"
        self.assertIn(marker, text)
        block = text.split(marker, 1)[1]
        next_sensor = block.find("\n      - name:")
        if next_sensor != -1:
            block = block[:next_sensor]
        self.assertIn("binary_sensor.emhass_house_load_valid", block)
        self.assertIn("is_state('binary_sensor.emhass_house_load_valid', 'off')", block)


class AutomationIdentityTests(unittest.TestCase):
    def test_monitoring_automation_ids_are_unique(self) -> None:
        ids = re.findall(r"^  - id: (\S+)$", monitoring_text(), flags=re.M)
        self.assertGreaterEqual(len(ids), 10)
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        self.assertEqual(duplicates, [])


class RecoveryStallRoutingTests(unittest.TestCase):
    def test_d3_keeps_deadlines_and_deduplicates_phone_action(self) -> None:
        block = automation_block("emhass_notify_D3_recovery_stalled")
        self.assertIn("incident_id: D3", block)
        self.assertRegex(block, r"hours:\s*2")
        self.assertRegex(block, r"hours:\s*6")
        self.assertIn("input_boolean.emhass_d3_actionable_notified", block)
        self.assertIn("phase: actionable", block)
        self.assertIn("phase: suppressed_duplicate", block)
        self.assertIn("service: script.emhass_push_alert", block)
        self.assertIn("action_message:", block)
        self.assertIn("Check Sigenergy mode, limits, and SOC.", block)
        self.assertIn("Check MPC plans and prices.", block)
        condition = block.split("condition:", 1)[1].split("action:", 1)[0]
        self.assertIn("input_boolean.emhass_auto_control_master", condition)
        self.assertIn('state: "on"', condition)
        # Notify first; mark delivered only after successful push (no continue_on_error).
        choose = block.split("- choose:", 1)[1]
        first_branch = choose.split("default:", 1)[0]
        push_pos = first_branch.index("service: script.emhass_push_alert")
        flag_pos = first_branch.index("input_boolean.turn_on")
        self.assertLess(push_pos, flag_pos)
        self.assertIn(
            "entity_id: input_boolean.emhass_d3_actionable_notified",
            first_branch[flag_pos:],
        )
        push_chunk = first_branch[
            first_branch.rfind("- service:", 0, push_pos) : flag_pos
        ]
        self.assertNotIn("continue_on_error:", push_chunk)

    def test_d3_recovery_logs_duration_and_never_phones(self) -> None:
        block = automation_block("emhass_log_D3_recovery")
        self.assertIn("incident_id: D3", block)
        self.assertIn("phase: resolved", block)
        self.assertIn("phase: recovering", block)
        self.assertIn("both_clear", block)
        self.assertIn("duration", block)
        self.assertNotIn("service: script.emhass_push_alert", block)
        # Full resolve only when recovery inactive and SOC at/above floor.
        self.assertIn("emhass_recovery_active", block)
        self.assertIn("emhass_soc_min", block)
        resolved_branch = block.split("phase: resolved", 1)[0]
        self.assertIn("both_clear", resolved_branch)

    def test_c2a_and_e1_suppress_phone_when_root_owns_incident(self) -> None:
        for escalate_id in (
            "emhass_escalate_C2a_house_load",
            "emhass_escalate_E1_p1",
        ):
            block = automation_block(escalate_id)
            with self.subTest(escalate_id=escalate_id):
                self.assertIn(
                    "input_boolean.emhass_load_history_root_active",
                    block,
                )
                self.assertIn(
                    "binary_sensor.emhass_fail_plan_stale",
                    block,
                )
                self.assertIn(
                    "input_boolean.emhass_d2_actionable_notified",
                    block,
                )
                self.assertNotIn(
                    "input_number.emhass_d2_failure_count",
                    block,
                )
                self.assertIn("phase: suppressed_duplicate", block)
                self.assertEqual(
                    block.count("service: script.emhass_push_alert"),
                    1,
                )

    def test_load_history_root_helper_is_declared(self) -> None:
        text = monitoring_text()
        self.assertIn("  emhass_load_history_root_active:\n", text)

    def test_d3_below_floor_duration_uses_detection_timestamp(self) -> None:
        detected = automation_block("emhass_log_D3_detected")
        self.assertIn("input_datetime.set_datetime", detected)
        self.assertIn(
            "entity_id: input_datetime.emhass_d3_below_floor_detected",
            detected,
        )
        self.assertIn("waiting_below_floor", detected)

        recovery = automation_block("emhass_log_D3_recovery")
        self.assertIn(
            "input_datetime.emhass_d3_below_floor_detected",
            recovery,
        )
        self.assertIn("waiting_below_floor", recovery)
        duration = recovery.split("duration_minutes:", 1)[1].split(
            "restored_function:", 1
        )[0]
        self.assertIn("timestamp", duration)
        self.assertIn("waiting_below_floor", duration)
        self.assertRegex(
            duration,
            r"emhass_d3_below_floor_detected",
        )

    def test_d3_clears_notified_flag_on_ha_start_when_healthy(self) -> None:
        block = automation_block("emhass_clear_D3_notified_on_ha_start")
        self.assertIn("platform: homeassistant", block)
        self.assertIn("event: start", block)
        condition = block.split("condition:", 1)[1].split("action:", 1)[0]
        self.assertIn("input_boolean.emhass_d3_actionable_notified", condition)
        self.assertIn('state: "on"', condition)
        self.assertIn("input_boolean.emhass_recovery_active", condition)
        self.assertIn('state: "off"', condition)
        self.assertIn("input_number.emhass_soc_min", condition)
        action = block.split("action:", 1)[1]
        self.assertIn("input_boolean.turn_off", action)
        self.assertIn(
            "entity_id: input_boolean.emhass_d3_actionable_notified",
            action,
        )
        self.assertNotIn("service: script.emhass_push_alert", action)

    def test_d3_dedup_helper_is_declared(self) -> None:
        text = monitoring_text()
        self.assertIn("input_boolean:\n", text)
        self.assertIn("  emhass_d3_actionable_notified:\n", text)
        helper = text.split("  emhass_d3_actionable_notified:\n", 1)[1]
        self.assertNotIn("initial:", helper)

    def test_d3_below_floor_detection_helper_is_declared(self) -> None:
        text = monitoring_text()
        self.assertIn("input_datetime:\n", text)
        self.assertIn("  emhass_d3_below_floor_detected:\n", text)
        helper = text.split("  emhass_d3_below_floor_detected:\n", 1)[1]
        self.assertIn("has_date: true", helper)
        self.assertIn("has_time: true", helper)


if __name__ == "__main__":
    unittest.main()
