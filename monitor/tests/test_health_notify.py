from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "lib"))
MODULE_PATH = REPO_ROOT / "monitor" / "health_notify.py"
SPEC = importlib.util.spec_from_file_location("health_notify", MODULE_PATH)
health_notify = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = health_notify
SPEC.loader.exec_module(health_notify)


DEGRADED = {
    "ok": True,
    "failure_kind": None,
    "load_history": {
        "mode": "degraded",
        "missing_bins": [
            "2026-07-29T02:30:00+00:00",
            "2026-07-29T03:00:00+00:00",
        ],
        "missing_bin_count": 2,
        "gap_duration_minutes": 60,
    },
}
HEALTHY = {
    "ok": True,
    "load_history": {
        "mode": "healthy",
        "missing_bins": [],
        "missing_bin_count": 0,
    },
}
UNSAFE = {
    "ok": False,
    "failure_kind": "load_history",
    "error": "load history validation failed: unbracketed gap",
    "load_history": {
        "mode": "unsafe",
        "missing_bins": [
            "2026-07-29T02:30:00+00:00",
            "2026-07-29T03:00:00+00:00",
        ],
        "missing_bin_count": 2,
    },
}


class LoadHistoryIncidentTests(unittest.TestCase):
    def test_first_degraded_mode_creates_one_transition(self):
        state = health_notify.AlertState()
        transition = health_notify.detect_load_history_transition(
            DEGRADED,
            state,
        )
        self.assertEqual(transition.mode, "degraded")
        health_notify.apply_load_history_transition(
            state,
            transition,
            notified_at=100.0,
        )
        self.assertIsNone(
            health_notify.detect_load_history_transition(DEGRADED, state)
        )

    def test_different_gap_is_a_new_incident(self):
        state = health_notify.AlertState(
            incident_mode="degraded",
            incident_signature=health_notify.load_history_signature(DEGRADED),
        )
        changed = json.loads(json.dumps(DEGRADED))
        changed["load_history"]["missing_bins"] = [
            "2026-07-30T02:30:00+00:00"
        ]
        self.assertIsNotNone(
            health_notify.detect_load_history_transition(changed, state)
        )

    def test_healthy_after_degraded_creates_one_recovery(self):
        state = health_notify.AlertState(
            incident_mode="degraded",
            incident_signature=health_notify.load_history_signature(DEGRADED),
        )
        transition = health_notify.detect_load_history_transition(
            HEALTHY,
            state,
        )
        self.assertEqual(transition.mode, "healthy")
        health_notify.apply_load_history_transition(
            state,
            transition,
            notified_at=200.0,
        )
        self.assertIsNone(
            health_notify.detect_load_history_transition(HEALTHY, state)
        )

    def test_unsafe_gap_is_primary_and_suppresses_derivatives(self):
        failures = [
            ("B2_mpc", "cron", "failed"),
            ("B3", "cron", "stale"),
            ("B5_cache", "weather", "cache stale"),
            ("B2_weather", "weather", "refresh failed"),
        ]
        filtered = health_notify.suppress_load_history_derivatives(
            failures,
            UNSAFE,
        )
        self.assertNotIn("B2_mpc", {item[0] for item in filtered})
        self.assertNotIn("B3", {item[0] for item in filtered})
        self.assertNotIn("B5_cache", {item[0] for item in filtered})
        self.assertIn("B2_weather", {item[0] for item in filtered})

    def test_old_numeric_state_file_still_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            (share / "monitor_alert_state.json").write_text(
                json.dumps({"B2_mpc": 123.0}),
                encoding="utf-8",
            )
            state = health_notify.load_alert_state(share)
        self.assertEqual(state["B2_mpc"].last_notify, 123.0)


class LoadHistoryDeliveryTests(unittest.TestCase):
    def deliver(self, state, mpc, *, now):
        return health_notify.process_load_history_transition(
            state,
            mpc,
            base_url="http://ha/",
            token="token",
            service="notify.notify",
            now=now,
        )

    @patch.object(health_notify, "notify_ha")
    @patch.object(health_notify, "log_ha_event", create=True, return_value=True)
    def test_success_persists_and_same_gap_deduplicates(
        self,
        mock_log,
        mock_notify,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            state = {}
            self.assertTrue(self.deliver(state, DEGRADED, now=100.0))
            health_notify.persist_alert_state(share, state)
            reloaded = health_notify.load_alert_state(share)
            incident = reloaded[health_notify.LOAD_HISTORY_STATE_KEY]
            self.assertEqual(incident.incident_mode, "degraded")
            self.assertEqual(
                incident.incident_signature,
                health_notify.load_history_signature(DEGRADED),
            )
            b6 = reloaded["B6_load_history_degraded"]
            self.assertEqual(b6.phase, "detected")
            self.assertFalse(
                self.deliver(reloaded, DEGRADED, now=101.0)
            )
        mock_notify.assert_not_called()
        self.assertEqual(mock_log.call_args.kwargs["phase"], "detected")

    @patch.object(health_notify, "notify_ha")
    @patch.object(health_notify, "log_ha_event", create=True, return_value=False)
    def test_logbook_failure_still_persists_load_history_incident(
        self,
        mock_log,
        mock_notify,
    ):
        state = {}
        self.assertTrue(self.deliver(state, DEGRADED, now=100.0))
        incident = state[health_notify.LOAD_HISTORY_STATE_KEY]
        self.assertEqual(incident.incident_mode, "degraded")
        self.assertEqual(
            incident.incident_signature,
            health_notify.load_history_signature(DEGRADED),
        )
        b6 = state["B6_load_history_degraded"]
        self.assertEqual(b6.phase, "detected")
        mock_notify.assert_not_called()
        mock_log.assert_called()

    @patch.object(health_notify, "notify_ha")
    @patch.object(health_notify, "log_ha_event", create=True, return_value=True)
    def test_healthy_after_degraded_delivers_one_recovery(
        self,
        mock_log,
        mock_notify,
    ):
        incident = health_notify.AlertState(
            incident_mode="degraded",
            incident_signature=health_notify.load_history_signature(
                DEGRADED
            ),
        )
        b6 = health_notify.AlertState(
            phase="detected",
            first_detected=100.0,
            notification_signature=health_notify.incident_signature(
                "B6_load_history_degraded",
                {
                    "message": (
                        "MPC remains operational with native interpolation "
                        "for 2 missing bin(s), 60 minutes."
                    )
                },
            ),
            escalation_deadline=100.0 + 3600,
        )
        state = {
            health_notify.LOAD_HISTORY_STATE_KEY: incident,
            "B6_load_history_degraded": b6,
        }
        self.assertTrue(self.deliver(state, HEALTHY, now=200.0))
        self.assertEqual(incident.incident_mode, "healthy")
        self.assertIsNone(incident.incident_signature)
        self.assertIsNone(b6.phase)
        self.assertFalse(self.deliver(state, HEALTHY, now=201.0))
        mock_notify.assert_not_called()
        self.assertEqual(mock_log.call_args.kwargs["phase"], "resolved")

    def test_unsafe_suppression_keeps_independent_weather_failure(self):
        failures = [
            ("B2_mpc", "cron", "load history failed"),
            ("B3", "cron", "stale MPC"),
            ("B5_cache", "weather", "derivative cache alert"),
            ("B2_weather", "weather", "independent refresh failed"),
        ]
        filtered = health_notify.suppress_load_history_derivatives(
            failures,
            UNSAFE,
        )
        self.assertEqual(
            filtered,
            [
                (
                    "B2_weather",
                    "weather",
                    "independent refresh failed",
                )
            ],
        )

    def test_suppress_load_history_derivatives_bypassed_if_no_valid_load_history_dict(self):
        failures = [
            ("B2_mpc", "cron", "failed"),
            ("B3", "cron", "stale"),
        ]
        mpc_no_history = {
            "ok": False,
            "failure_kind": "load_history",
            "error": "HA REST fetch failed",
        }
        filtered = health_notify.suppress_load_history_derivatives(
            failures,
            mpc_no_history,
        )
        self.assertEqual(filtered, failures)

    def test_stale_sample_age_progression_does_not_retrigger_b6_notification(self):
        state = health_notify.AlertState()
        stale_6min = {
            "ok": False,
            "failure_kind": "load_history",
            "error": (
                "load history validation failed: Load history newest finite "
                "sample is stale (6.2 minutes old; maximum 5)"
            ),
            "load_history": {
                "mode": "unsafe",
                "missing_bins": [],
                "missing_bin_count": 0,
            },
        }
        stale_36min = {
            "ok": False,
            "failure_kind": "load_history",
            "error": (
                "load history validation failed: Load history newest finite "
                "sample is stale (36.2 minutes old; maximum 5)"
            ),
            "load_history": {
                "mode": "unsafe",
                "missing_bins": [],
                "missing_bin_count": 0,
            },
        }
        trans1 = health_notify.detect_load_history_transition(stale_6min, state)
        self.assertIsNotNone(trans1)
        health_notify.apply_load_history_transition(
            state, trans1, notified_at=100.0
        )

        trans2 = health_notify.detect_load_history_transition(stale_36min, state)
        self.assertIsNone(trans2)


STALE_SAMPLE_UNSAFE = {
    "ok": False,
    "failure_kind": "load_history",
    "error": (
        "load history validation failed: Load history newest finite "
        "sample is stale (36.2 minutes old; maximum 5)"
    ),
    "load_history": {
        "mode": "unsafe",
        "missing_bins": [],
        "missing_bin_count": 0,
    },
}

MISSING_BINS_UNSAFE = {
    "ok": False,
    "failure_kind": "load_history",
    "error": "load history validation failed: unbracketed gap",
    "load_history": {
        "mode": "unsafe",
        "missing_bins": [
            "2026-07-29T02:30:00+00:00",
            "2026-07-29T03:00:00+00:00",
        ],
        "missing_bin_count": 2,
    },
}


class RecoverableIncidentEscalationTests(unittest.TestCase):
    def test_mpc_escalation_deadline_is_sixty_minutes(self) -> None:
        self.assertEqual(health_notify.MPC_ESCALATION_SEC, 60 * 60)

    @patch.object(health_notify, "notify_ha")
    @patch.object(health_notify, "log_ha_event", create=True)
    def test_first_recoverable_detection_logs_without_phone(
        self,
        mock_log,
        mock_notify,
    ) -> None:
        state: dict[str, health_notify.AlertState] = {}
        health_notify.record_recoverable_incident(
            state,
            alert_id="B3",
            context={"message": "Last MPC success 45 min ago"},
            now=1000.0,
            base_url="http://ha/",
            token="token",
        )
        mock_log.assert_called_once()
        self.assertEqual(mock_log.call_args.kwargs["phase"], "detected")
        mock_notify.assert_not_called()
        entry = state["B3"]
        self.assertEqual(entry.phase, "detected")
        self.assertEqual(entry.first_detected, 1000.0)

    @patch.object(health_notify, "notify_ha")
    @patch.object(health_notify, "log_ha_event", create=True)
    def test_recoverable_incident_escalates_after_sixty_minutes(
        self,
        mock_log,
        mock_notify,
    ) -> None:
        state: dict[str, health_notify.AlertState] = {}
        health_notify.record_recoverable_incident(
            state,
            alert_id="B3",
            context={"message": "Last MPC success 45 min ago"},
            now=1000.0,
            base_url="http://ha/",
            token="token",
        )
        mock_log.reset_mock()
        mock_notify.reset_mock()
        self.assertFalse(
            health_notify.should_escalate_recoverable(
                state["B3"],
                alert_id="B3",
                now=1000.0 + 3599,
            )
        )
        self.assertTrue(
            health_notify.process_recoverable_escalation(
                state,
                alert_id="B3",
                title="EMHASS cron / MPC",
                message="Last MPC success 45 min ago",
                now=1000.0 + 3600,
                base_url="http://ha/",
                token="token",
                service="notify.notify",
            )
        )
        mock_notify.assert_called_once()
        self.assertEqual(state["B3"].phase, "actionable")

    @patch.object(health_notify, "notify_ha")
    @patch.object(health_notify, "log_ha_event", create=True)
    def test_same_signature_does_not_repeat_phone_notification(
        self,
        mock_log,
        mock_notify,
    ) -> None:
        signature = health_notify.incident_signature(
            "B3",
            {"message": "Last MPC success 45 min ago"},
        )
        state = {
            "B3": health_notify.AlertState(
                phase="actionable",
                first_detected=1000.0,
                notification_signature=signature,
                last_notify=5000.0,
            )
        }
        self.assertFalse(
            health_notify.process_recoverable_escalation(
                state,
                alert_id="B3",
                title="EMHASS cron / MPC",
                message="Last MPC success 45 min ago",
                now=6000.0,
                base_url="http://ha/",
                token="token",
                service="notify.notify",
            )
        )
        mock_notify.assert_not_called()

    @patch.object(health_notify, "notify_ha")
    @patch.object(health_notify, "log_ha_event", create=True)
    def test_recovery_logs_without_phone_notification(
        self,
        mock_log,
        mock_notify,
    ) -> None:
        state = {
            "B3": health_notify.AlertState(
                phase="detected",
                first_detected=1000.0,
                notification_signature=health_notify.incident_signature(
                    "B3",
                    {"message": "Last MPC success 45 min ago"},
                ),
            )
        }
        health_notify.process_recoverable_recovery(
            state,
            alert_id="B3",
            message="MPC success restored",
            now=5000.0,
            base_url="http://ha/",
            token="token",
        )
        mock_log.assert_called_once()
        self.assertEqual(mock_log.call_args.kwargs["phase"], "resolved")
        mock_notify.assert_not_called()
        self.assertIsNone(state["B3"].phase)

    def test_b6_unsafe_stale_sample_message_distinguishes_stale_data(self) -> None:
        transition = health_notify.detect_load_history_transition(
            STALE_SAMPLE_UNSAFE,
            health_notify.AlertState(),
        )
        self.assertIsNotNone(transition)
        message = transition.message.lower()
        self.assertIn("stale", message)
        self.assertNotIn("missing bin", message)

    def test_b6_unsafe_missing_bins_message_reports_missing_data(self) -> None:
        transition = health_notify.detect_load_history_transition(
            MISSING_BINS_UNSAFE,
            health_notify.AlertState(),
        )
        self.assertIsNotNone(transition)
        message = transition.message.lower()
        self.assertIn("missing", message)
        self.assertNotIn("stale sample", message)

    @patch.object(health_notify, "notify_ha")
    @patch.object(health_notify, "log_ha_event", create=True, return_value=False)
    def test_logbook_failure_does_not_clear_incident_state(
        self,
        mock_log,
        mock_notify,
    ) -> None:
        state: dict[str, health_notify.AlertState] = {}
        result = health_notify.record_recoverable_incident(
            state,
            alert_id="B3",
            context={"message": "Last MPC success 45 min ago"},
            now=1000.0,
            base_url="http://ha/",
            token="token",
        )
        mock_log.assert_called_once()
        mock_notify.assert_not_called()
        self.assertFalse(result)
        entry = state["B3"]
        self.assertEqual(entry.phase, "detected")
        self.assertEqual(entry.first_detected, 1000.0)
        self.assertEqual(entry.escalation_deadline, 1000.0 + 3600)

    @patch.object(health_notify, "notify_ha")
    @patch.object(health_notify, "log_ha_event", create=True, return_value=True)
    def test_age_bearing_message_does_not_reset_escalation_deadline(
        self,
        mock_log,
        mock_notify,
    ) -> None:
        state: dict[str, health_notify.AlertState] = {}
        health_notify.record_recoverable_incident(
            state,
            alert_id="B3",
            context={"message": "Last MPC success 45 min ago"},
            now=1000.0,
            base_url="http://ha/",
            token="token",
        )
        deadline = state["B3"].escalation_deadline
        first_detected = state["B3"].first_detected
        health_notify.record_recoverable_incident(
            state,
            alert_id="B3",
            context={"message": "Last MPC success 75 min ago"},
            now=1000.0 + 1800,
            base_url="http://ha/",
            token="token",
        )
        self.assertEqual(state["B3"].first_detected, first_detected)
        self.assertEqual(state["B3"].escalation_deadline, deadline)
        self.assertEqual(
            health_notify.incident_signature(
                "B3",
                {"message": "Last MPC success 45 min ago"},
            ),
            health_notify.incident_signature(
                "B3",
                {"message": "Last MPC success 105 min ago"},
            ),
        )
        mock_log.reset_mock()
        mock_notify.reset_mock()
        self.assertTrue(
            health_notify.process_recoverable_escalation(
                state,
                alert_id="B3",
                title="EMHASS cron / MPC",
                message="Last MPC success 105 min ago",
                now=1000.0 + 3600,
                base_url="http://ha/",
                token="token",
                service="notify.notify",
            )
        )
        mock_notify.assert_called_once()
        self.assertFalse(
            health_notify.process_recoverable_escalation(
                state,
                alert_id="B3",
                title="EMHASS cron / MPC",
                message="Last MPC success 115 min ago",
                now=1000.0 + 4200,
                base_url="http://ha/",
                token="token",
                service="notify.notify",
            )
        )
        mock_notify.assert_called_once()

    @patch.object(health_notify, "notify_ha")
    @patch.object(health_notify, "log_ha_event", create=True, return_value=True)
    def test_legacy_load_history_root_backfills_and_escalates(
        self,
        mock_log,
        mock_notify,
    ) -> None:
        incident = health_notify.AlertState(
            incident_mode="unsafe",
            incident_signature=health_notify.load_history_signature(UNSAFE),
            last_notify=1000.0,
        )
        state = {health_notify.LOAD_HISTORY_STATE_KEY: incident}
        health_notify.process_load_history_transition(
            state,
            UNSAFE,
            base_url="http://ha/",
            token="token",
            service="notify.notify",
            now=1000.0 + 100,
        )
        mock_notify.assert_not_called()
        b6 = state["B6_load_history_unsafe"]
        self.assertEqual(b6.phase, "detected")
        self.assertEqual(b6.first_detected, 1000.0)
        self.assertEqual(b6.escalation_deadline, 1000.0 + 3600)
        self.assertTrue(
            health_notify.process_load_history_transition(
                state,
                UNSAFE,
                base_url="http://ha/",
                token="token",
                service="notify.notify",
                now=1000.0 + 3600,
            )
        )
        mock_notify.assert_called_once()
        self.assertEqual(state["B6_load_history_unsafe"].phase, "actionable")

    @patch.object(health_notify, "notify_ha")
    @patch.object(health_notify, "log_ha_event", create=True, return_value=False)
    def test_failed_b6_recovery_log_keeps_root_incident_open(
        self,
        mock_log,
        mock_notify,
    ) -> None:
        incident = health_notify.AlertState(
            incident_mode="degraded",
            incident_signature=health_notify.load_history_signature(DEGRADED),
        )
        b6 = health_notify.AlertState(
            phase="detected",
            first_detected=100.0,
            notification_signature=health_notify.incident_signature(
                "B6_load_history_degraded",
                {"kind": "degraded"},
            ),
            escalation_deadline=100.0 + 3600,
        )
        state = {
            health_notify.LOAD_HISTORY_STATE_KEY: incident,
            "B6_load_history_degraded": b6,
        }
        self.assertFalse(
            health_notify.process_load_history_transition(
                state,
                HEALTHY,
                base_url="http://ha/",
                token="token",
                service="notify.notify",
                now=200.0,
            )
        )
        self.assertEqual(incident.incident_mode, "degraded")
        self.assertEqual(
            incident.incident_signature,
            health_notify.load_history_signature(DEGRADED),
        )
        self.assertEqual(b6.phase, "detected")
        mock_notify.assert_not_called()
        mock_log.assert_called()
        self.assertEqual(mock_log.call_args.kwargs["phase"], "resolved")

    @patch.object(health_notify, "notify_ha")
    @patch.object(health_notify, "log_ha_event", create=True, return_value=True)
    def test_b5_first_fail_records_deadline_before_consecutive_threshold(
        self,
        mock_log,
        mock_notify,
    ) -> None:
        """First B5 fail must log + start 60 min clock; no early consecutive phone."""
        state: dict[str, health_notify.AlertState] = {
            "B5_cache": health_notify.AlertState(consecutive_failures=1),
        }
        health_notify.record_recoverable_incident(
            state,
            alert_id="B5_cache",
            context={"message": "cache stale"},
            now=1000.0,
            base_url="http://ha/",
            token="token",
        )
        mock_log.assert_called_once()
        self.assertEqual(mock_log.call_args.kwargs["phase"], "detected")
        mock_notify.assert_not_called()
        entry = state["B5_cache"]
        self.assertEqual(entry.phase, "detected")
        self.assertEqual(entry.first_detected, 1000.0)
        self.assertEqual(entry.escalation_deadline, 1000.0 + 3600)
        self.assertEqual(entry.consecutive_failures, 1)
        self.assertFalse(
            health_notify.should_escalate_recoverable(
                entry,
                alert_id="B5_cache",
                now=1000.0 + 30,
                failure_threshold=2,
            )
        )
        self.assertTrue(
            health_notify.should_escalate_recoverable(
                entry,
                alert_id="B5_cache",
                now=1000.0 + 3600,
                failure_threshold=2,
            )
        )

    @patch.object(health_notify, "notify_ha")
    @patch.object(health_notify, "log_ha_event", create=True, return_value=True)
    def test_b5_two_monitor_runs_do_not_phone_before_sixty_minutes(
        self,
        mock_log,
        mock_notify,
    ) -> None:
        """Two 10-minute monitor polls (~20 min) must not phone B5."""
        state: dict[str, health_notify.AlertState] = {}
        health_notify.record_recoverable_incident(
            state,
            alert_id="B5_cache",
            context={"message": "cache stale"},
            now=1_000.0,
            base_url="http://ha/",
            token="token",
        )
        entry = state["B5_cache"]
        entry.consecutive_failures = 2
        twenty_minutes = 1_000.0 + (20 * 60)
        self.assertFalse(
            health_notify.should_escalate_recoverable(
                entry,
                alert_id="B5_cache",
                now=twenty_minutes,
                failure_threshold=2,
            )
        )
        self.assertFalse(
            health_notify.process_recoverable_escalation(
                state,
                alert_id="B5_cache",
                title="Open-Meteo cache",
                message="cache stale",
                now=twenty_minutes,
                base_url="http://ha/",
                token="token",
                service="notify.notify",
                failure_threshold=2,
            )
        )
        mock_notify.assert_not_called()
        self.assertEqual(entry.phase, "detected")
        self.assertTrue(
            health_notify.process_recoverable_escalation(
                state,
                alert_id="B5_cache",
                title="Open-Meteo cache",
                message="cache stale",
                now=1_000.0 + 3600,
                base_url="http://ha/",
                token="token",
                service="notify.notify",
                failure_threshold=2,
            )
        )
        mock_notify.assert_called_once()
        self.assertEqual(entry.phase, "actionable")


    @patch.object(health_notify, "call_ha_service")
    @patch.object(health_notify, "log_ha_event", create=True, return_value=True)
    def test_b6_syncs_load_history_root_ownership_flag(
        self,
        mock_log,
        mock_service,
    ) -> None:
        state: dict[str, health_notify.AlertState] = {}
        health_notify.process_load_history_transition(
            state,
            UNSAFE,
            base_url="http://ha/",
            token="token",
            service="notify.notify",
            now=1_000.0,
        )
        mock_service.assert_any_call(
            "http://ha/",
            "token",
            "input_boolean.turn_on",
            {
                "entity_id": health_notify.LOAD_HISTORY_ROOT_ENTITY,
            },
        )
        ownership = state[health_notify.OWNERSHIP_STATE_KEY]
        self.assertTrue(ownership.ownership_desired_active)
        self.assertFalse(ownership.ownership_pending)
        mock_service.reset_mock()
        health_notify.process_load_history_transition(
            state,
            HEALTHY,
            base_url="http://ha/",
            token="token",
            service="notify.notify",
            now=1_100.0,
        )
        mock_service.assert_any_call(
            "http://ha/",
            "token",
            "input_boolean.turn_off",
            {
                "entity_id": health_notify.LOAD_HISTORY_ROOT_ENTITY,
            },
        )
        ownership = state[health_notify.OWNERSHIP_STATE_KEY]
        self.assertFalse(ownership.ownership_desired_active)
        self.assertFalse(ownership.ownership_pending)

    @patch.object(health_notify, "call_ha_service")
    @patch.object(health_notify, "log_ha_event", create=True, return_value=True)
    def test_b6_ownership_turn_off_retries_after_ha_failure(
        self,
        mock_log,
        mock_service,
    ) -> None:
        state: dict[str, health_notify.AlertState] = {}
        health_notify.process_load_history_transition(
            state,
            UNSAFE,
            base_url="http://ha/",
            token="token",
            service="notify.notify",
            now=1_000.0,
        )
        mock_service.reset_mock()
        mock_service.side_effect = [
            OSError("ha down"),
            None,
        ]
        health_notify.process_load_history_transition(
            state,
            HEALTHY,
            base_url="http://ha/",
            token="token",
            service="notify.notify",
            now=1_100.0,
        )
        ownership = state[health_notify.OWNERSHIP_STATE_KEY]
        self.assertFalse(ownership.ownership_desired_active)
        self.assertTrue(ownership.ownership_pending)
        mock_service.side_effect = None
        mock_service.reset_mock()
        # Later healthy poll with inactive root must retry turn_off.
        health_notify.process_load_history_transition(
            state,
            HEALTHY,
            base_url="http://ha/",
            token="token",
            service="notify.notify",
            now=1_200.0,
        )
        mock_service.assert_any_call(
            "http://ha/",
            "token",
            "input_boolean.turn_off",
            {
                "entity_id": health_notify.LOAD_HISTORY_ROOT_ENTITY,
            },
        )
        ownership = state[health_notify.OWNERSHIP_STATE_KEY]
        self.assertFalse(ownership.ownership_pending)

        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            health_notify.persist_alert_state(share, state)
            reloaded = health_notify.load_alert_state(share)
            owned = reloaded[health_notify.OWNERSHIP_STATE_KEY]
            self.assertFalse(owned.ownership_desired_active)
            self.assertFalse(owned.ownership_pending)


class PlannedProfitDeadlineTests(unittest.TestCase):
    CEST = ZoneInfo("Europe/Amsterdam")

    @staticmethod
    def write_lock(share: Path, target: str) -> None:
        (share / health_notify.PLANNED_PROFIT_JSON).write_text(
            json.dumps(
                {
                    "version": 1,
                    "timezone": "Europe/Amsterdam",
                    "metric": "emhass_cost_profit_covered_slots",
                    "days": {
                        target: {
                            "plan_date": target,
                            "planned_eur": 1.0,
                            "slot_count": 1,
                            "partial": True,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_before_2200_does_not_notify(self):
        share = Path(tempfile.mkdtemp())
        state = {}
        now = datetime(2026, 8, 16, 21, 30, tzinfo=self.CEST)
        with patch.object(health_notify, "notify_ha") as notify:
            sent = health_notify.process_planned_profit_deadline(
                share=share,
                state=state,
                now=now,
                base_url="http://ha/",
                token="token",
                service="notify.notify",
            )
        self.assertEqual(sent, 0)
        notify.assert_not_called()

    def test_2200_missing_sends_once(self):
        share = Path(tempfile.mkdtemp())
        state = {}
        now = datetime(2026, 8, 16, 22, 0, tzinfo=self.CEST)
        with patch.object(health_notify, "notify_ha") as notify:
            sent = health_notify.process_planned_profit_deadline(
                share=share,
                state=state,
                now=now,
                base_url="http://ha/",
                token="token",
                service="notify.notify",
            )
        self.assertEqual(sent, 1)
        notify.assert_called_once()
        entry = state[health_notify.PLANNED_PROFIT_STATE_KEY]
        self.assertEqual(
            entry.notification_signature,
            "planned_profit_missing:2026-08-17",
        )

    def test_same_target_does_not_repeat_after_success(self):
        share = Path(tempfile.mkdtemp())
        state = {
            health_notify.PLANNED_PROFIT_STATE_KEY: health_notify.AlertState(
                last_notify=100.0,
                notification_signature="planned_profit_missing:2026-08-17",
            )
        }
        now = datetime(2026, 8, 16, 22, 30, tzinfo=self.CEST)
        with patch.object(health_notify, "notify_ha") as notify:
            sent = health_notify.process_planned_profit_deadline(
                share=share,
                state=state,
                now=now,
                base_url="http://ha/",
                token="token",
                service="notify.notify",
            )
        self.assertEqual(sent, 0)
        notify.assert_not_called()

    def test_delivery_failure_retries(self):
        share = Path(tempfile.mkdtemp())
        state = {}
        now = datetime(2026, 8, 16, 22, 0, tzinfo=self.CEST)
        with patch.object(
            health_notify,
            "notify_ha",
            side_effect=urllib.error.HTTPError(
                "url", 500, "fail", hdrs=None, fp=None
            ),
        ):
            sent = health_notify.process_planned_profit_deadline(
                share=share,
                state=state,
                now=now,
                base_url="http://ha/",
                token="token",
                service="notify.notify",
            )
        self.assertEqual(sent, 0)
        entry = state[health_notify.PLANNED_PROFIT_STATE_KEY]
        self.assertFalse(entry.last_notify)

    def test_late_lock_clears_pending_without_recovery_notify(self):
        share = Path(tempfile.mkdtemp())
        self.write_lock(share, "2026-08-17")
        state = {
            health_notify.PLANNED_PROFIT_STATE_KEY: health_notify.AlertState(
                last_notify=100.0,
                notification_signature="planned_profit_missing:2026-08-17",
            )
        }
        now = datetime(2026, 8, 16, 21, 30, tzinfo=self.CEST)
        with patch.object(health_notify, "notify_ha") as notify:
            sent = health_notify.process_planned_profit_deadline(
                share=share,
                state=state,
                now=now,
                base_url="http://ha/",
                token="token",
                service="notify.notify",
            )
        self.assertEqual(sent, 0)
        notify.assert_not_called()
        entry = state[health_notify.PLANNED_PROFIT_STATE_KEY]
        self.assertIsNone(entry.notification_signature)

    def test_malformed_json_uses_invalid_message_once(self):
        share = Path(tempfile.mkdtemp())
        (share / health_notify.PLANNED_PROFIT_JSON).write_text(
            "{bad", encoding="utf-8"
        )
        state = {}
        now = datetime(2026, 8, 16, 22, 0, tzinfo=self.CEST)
        with patch.object(health_notify, "notify_ha") as notify:
            health_notify.process_planned_profit_deadline(
                share=share,
                state=state,
                now=now,
                base_url="http://ha/",
                token="token",
                service="notify.notify",
            )
        notify.assert_called_once()
        self.assertIn("invalid", notify.call_args.args[4])

    def test_dst_2200_uses_amsterdam(self):
        share = Path(tempfile.mkdtemp())
        state = {}
        now = datetime(2026, 10, 25, 22, 0, tzinfo=self.CEST)
        with patch.object(health_notify, "notify_ha") as notify:
            sent = health_notify.process_planned_profit_deadline(
                share=share,
                state=state,
                now=now,
                base_url="http://ha/",
                token="token",
                service="notify.notify",
            )
        self.assertEqual(sent, 1)
        notify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
