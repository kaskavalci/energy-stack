from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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
    def test_success_persists_and_same_gap_deduplicates(
        self,
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
            self.assertFalse(
                self.deliver(reloaded, DEGRADED, now=101.0)
            )
        mock_notify.assert_called_once()

    @patch.object(
        health_notify,
        "notify_ha",
        side_effect=RuntimeError("HA unavailable"),
    )
    def test_failed_delivery_does_not_persist_and_retries(
        self,
        mock_notify,
    ):
        state = {}
        self.assertFalse(self.deliver(state, DEGRADED, now=100.0))
        incident = state[health_notify.LOAD_HISTORY_STATE_KEY]
        self.assertIsNone(incident.incident_mode)
        self.assertIsNone(incident.incident_signature)
        self.assertFalse(self.deliver(state, DEGRADED, now=101.0))
        self.assertEqual(mock_notify.call_count, 2)

    @patch.object(health_notify, "notify_ha")
    def test_healthy_after_degraded_delivers_one_recovery(
        self,
        mock_notify,
    ):
        incident = health_notify.AlertState(
            incident_mode="degraded",
            incident_signature=health_notify.load_history_signature(
                DEGRADED
            ),
        )
        state = {health_notify.LOAD_HISTORY_STATE_KEY: incident}
        self.assertTrue(self.deliver(state, HEALTHY, now=200.0))
        self.assertEqual(incident.incident_mode, "healthy")
        self.assertIsNone(incident.incident_signature)
        self.assertFalse(self.deliver(state, HEALTHY, now=201.0))
        mock_notify.assert_called_once()

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


if __name__ == "__main__":
    unittest.main()
