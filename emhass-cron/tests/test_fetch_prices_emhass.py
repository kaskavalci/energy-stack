import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timedelta, timezone
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import MagicMock, call, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlsplit
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "lib"))

MODULE_PATH = REPO_ROOT / "emhass-cron" / "fetch_prices_emhass.py"
SPEC = importlib.util.spec_from_file_location("fetch_prices_emhass", MODULE_PATH)
fetch_prices_emhass = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(fetch_prices_emhass)


class EmhassConfigExampleTests(unittest.TestCase):
    def test_native_naive_load_history_uses_sampled_house_load(self):
        config = json.loads(
            (REPO_ROOT / "emhass/share/config.json.example").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(config["load_forecast_method"], "naive")
        self.assertEqual(
            config["sensor_power_load_no_var_loads"],
            "sensor.emhass_house_load_sample_w",
        )
        self.assertEqual(
            config["var_model"],
            "sensor.emhass_house_load_sample_w",
        )
        self.assertIn(
            "sensor.emhass_house_load_sample_w",
            config["sensor_linear_interp"],
        )
        self.assertNotIn(
            "sensor.emhass_house_load_sample_w",
            config["sensor_replace_zero"],
        )
        self.assertFalse(config["set_zero_min"])

    def test_open_meteo_cache_max_age_is_150(self):
        config = json.loads(
            (REPO_ROOT / "emhass/share/config.json.example").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(config["open_meteo_cache_max_age"], 150)


class NativeLoadHistoryValidationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 26, 15, 34, tzinfo=ZoneInfo("Europe/Amsterdam"))

    @staticmethod
    def record(state, last_updated):
        return {
            "entity_id": "sensor.emhass_house_load_sample_w",
            "state": state,
            "last_changed": last_updated,
            "last_updated": last_updated,
            "attributes": {},
        }

    def records_with_missing_bins(
        self,
        *,
        horizon: int,
        missing_indices: set[int],
    ) -> list[dict]:
        newest = datetime.fromisoformat("2026-07-26T15:32:00+02:00")
        first = newest - timedelta(minutes=30 * (horizon - 1))
        return [
            self.record(
                str(100 + index * 10),
                (first + timedelta(minutes=30 * index)).isoformat(),
            )
            for index in range(horizon)
            if index not in missing_indices
        ]

    def test_complete_history_is_healthy(self):
        summary = fetch_prices_emhass.validate_load_history_records(
            self.records_with_missing_bins(horizon=32, missing_indices=set()),
            prediction_horizon=32,
            step_min=30,
            now=self.now,
        )
        self.assertEqual(summary["mode"], "healthy")
        self.assertEqual(summary["covered_bins"], 32)
        self.assertEqual(summary["missing_bin_count"], 0)
        self.assertEqual(summary["missing_bins"], [])
        self.assertIsNone(summary["interpolation_method"])

    def test_six_bin_bracketed_interior_gap_is_degraded(self):
        summary = fetch_prices_emhass.validate_load_history_records(
            self.records_with_missing_bins(
                horizon=32,
                missing_indices=set(range(10, 16)),
            ),
            prediction_horizon=32,
            step_min=30,
            now=self.now,
        )
        self.assertEqual(summary["mode"], "degraded")
        self.assertEqual(summary["covered_bins"], 26)
        self.assertEqual(summary["missing_bin_count"], 6)
        self.assertEqual(summary["gap_duration_minutes"], 180)
        self.assertEqual(
            summary["interpolation_method"],
            "emhass_native_linear",
        )
        self.assertEqual(
            datetime.fromisoformat(summary["gap_end_exclusive"])
            - datetime.fromisoformat(summary["gap_start"]),
            timedelta(minutes=180),
        )

    def test_seven_bin_gap_is_unsafe(self):
        with self.assertRaises(fetch_prices_emhass.LoadHistoryError) as raised:
            fetch_prices_emhass.validate_load_history_records(
                self.records_with_missing_bins(
                    horizon=32,
                    missing_indices=set(range(10, 17)),
                ),
                prediction_horizon=32,
                step_min=30,
                now=self.now,
            )
        self.assertEqual(raised.exception.summary["mode"], "unsafe")
        self.assertEqual(raised.exception.summary["missing_bin_count"], 7)

    def test_stale_newest_sample_is_unsafe_with_diagnostics(self):
        with self.assertRaises(fetch_prices_emhass.LoadHistoryError) as raised:
            fetch_prices_emhass.validate_load_history_records(
                self.records_with_missing_bins(
                    horizon=32,
                    missing_indices=set(),
                ),
                prediction_horizon=32,
                step_min=30,
                now=datetime.fromisoformat(
                    "2026-07-26T15:40:00+02:00"
                ),
            )
        self.assertEqual(raised.exception.summary["mode"], "unsafe")
        self.assertEqual(
            raised.exception.summary["newest_sample_age_minutes"],
            8.0,
        )
        self.assertEqual(raised.exception.summary["missing_bins"], [])

    def test_multiple_gaps_are_unsafe(self):
        with self.assertRaises(fetch_prices_emhass.LoadHistoryError) as raised:
            fetch_prices_emhass.validate_load_history_records(
                self.records_with_missing_bins(
                    horizon=32,
                    missing_indices={5, 6, 20},
                ),
                prediction_horizon=32,
                step_min=30,
                now=self.now,
            )
        self.assertEqual(raised.exception.summary["mode"], "unsafe")

    def test_oldest_and_newest_edge_gaps_are_unsafe(self):
        for missing_indices in ({0}, {31}):
            with self.subTest(missing_indices=missing_indices):
                with self.assertRaises(fetch_prices_emhass.LoadHistoryError):
                    fetch_prices_emhass.validate_load_history_records(
                        self.records_with_missing_bins(
                            horizon=32,
                            missing_indices=missing_indices,
                        ),
                        prediction_horizon=32,
                        step_min=30,
                        now=self.now,
                    )

    def test_six_bins_remain_absolute_for_twelve_step_horizon(self):
        summary = fetch_prices_emhass.validate_load_history_records(
            self.records_with_missing_bins(
                horizon=12,
                missing_indices=set(range(3, 9)),
            ),
            prediction_horizon=12,
            step_min=30,
            now=self.now,
        )
        self.assertEqual(summary["mode"], "degraded")
        self.assertEqual(summary["missing_bin_count"], 6)

    def test_each_allowed_gap_length_is_degraded(self):
        for gap_length in range(1, 7):
            with self.subTest(gap_length=gap_length):
                start = 10
                summary = fetch_prices_emhass.validate_load_history_records(
                    self.records_with_missing_bins(
                        horizon=32,
                        missing_indices=set(
                            range(start, start + gap_length)
                        ),
                    ),
                    prediction_horizon=32,
                    step_min=30,
                    now=self.now,
                )
                self.assertEqual(summary["mode"], "degraded")
                self.assertEqual(
                    summary["missing_bin_count"],
                    gap_length,
                )

    def test_accepts_complete_valid_half_hour_bins(self):
        records = [
            self.record("125", "2026-07-26T14:31:00+02:00"),
            self.record("250.5", "2026-07-26T15:01:00+02:00"),
            self.record("375", "2026-07-26T15:32:00+02:00"),
        ]

        summary = fetch_prices_emhass.validate_load_history_records(
            records,
            prediction_horizon=3,
            step_min=30,
            now=self.now,
        )

        self.assertEqual(
            summary,
            {
                "mode": "healthy",
                "required_bins": 3,
                "covered_bins": 3,
                "missing_bin_count": 0,
                "missing_bins": [],
                "interpolation_method": None,
                "newest_sample_at": "2026-07-26T13:32:00+00:00",
                "newest_sample_age_minutes": 2.0,
            },
        )

    def test_at_2200_before_first_current_bin_sample_uses_newest_bin(self):
        now = datetime.fromisoformat("2026-07-27T22:00:02+02:00")
        records = [
            self.record("100", "2026-07-27T20:31:00+02:00"),
            self.record("100", "2026-07-27T21:01:00+02:00"),
            self.record("100", "2026-07-27T21:59:00+02:00"),
        ]

        summary = fetch_prices_emhass.validate_load_history_records(
            records,
            prediction_horizon=3,
            step_min=30,
            now=now,
        )

        self.assertEqual(summary["mode"], "healthy")
        self.assertEqual(summary["covered_bins"], 3)
        self.assertEqual(
            summary["newest_sample_at"],
            "2026-07-27T19:59:00+00:00",
        )

    def test_at_2200_after_first_current_bin_sample_advances_newest_bin(self):
        now = datetime.fromisoformat("2026-07-27T22:00:02+02:00")
        records = [
            self.record("100", "2026-07-27T21:01:00+02:00"),
            self.record("100", "2026-07-27T21:31:00+02:00"),
            self.record("100", "2026-07-27T22:00:01+02:00"),
        ]

        summary = fetch_prices_emhass.validate_load_history_records(
            records,
            prediction_horizon=3,
            step_min=30,
            now=now,
        )

        self.assertEqual(summary["mode"], "healthy")
        self.assertEqual(summary["covered_bins"], 3)
        self.assertEqual(
            summary["newest_sample_at"],
            "2026-07-27T20:00:01+00:00",
        )

    def test_at_2230_before_first_current_bin_sample_uses_newest_bin(self):
        now = datetime.fromisoformat("2026-07-27T22:30:02+02:00")
        records = [
            self.record("100", "2026-07-27T21:01:00+02:00"),
            self.record("100", "2026-07-27T21:31:00+02:00"),
            self.record("100", "2026-07-27T22:29:00+02:00"),
        ]

        summary = fetch_prices_emhass.validate_load_history_records(
            records,
            prediction_horizon=3,
            step_min=30,
            now=now,
        )

        self.assertEqual(summary["mode"], "healthy")
        self.assertEqual(summary["covered_bins"], 3)
        self.assertEqual(
            summary["newest_sample_at"],
            "2026-07-27T20:29:00+00:00",
        )

    def test_at_2230_after_first_current_bin_sample_advances_newest_bin(self):
        now = datetime.fromisoformat("2026-07-27T22:30:02+02:00")
        records = [
            self.record("100", "2026-07-27T21:31:00+02:00"),
            self.record("100", "2026-07-27T22:01:00+02:00"),
            self.record("100", "2026-07-27T22:30:01+02:00"),
        ]

        summary = fetch_prices_emhass.validate_load_history_records(
            records,
            prediction_horizon=3,
            step_min=30,
            now=now,
        )

        self.assertEqual(summary["mode"], "healthy")
        self.assertEqual(summary["covered_bins"], 3)
        self.assertEqual(
            summary["newest_sample_at"],
            "2026-07-27T20:30:01+00:00",
        )

    def test_boundary_rejects_fresh_history_with_missing_older_bin(self):
        now = datetime.fromisoformat("2026-07-27T22:00:02+02:00")
        records = [
            self.record("100", "2026-07-27T21:01:00+02:00"),
            self.record("100", "2026-07-27T21:59:00+02:00"),
        ]

        with self.assertRaisesRegex(
            fetch_prices_emhass.LoadHistoryError,
            r"2026-07-27T20:30:00\+02:00",
        ) as raised:
            fetch_prices_emhass.validate_load_history_records(
                records,
                prediction_horizon=3,
                step_min=30,
                now=now,
            )
        self.assertEqual(raised.exception.summary["mode"], "unsafe")

    def test_boundary_rejects_stale_newest_sample_before_anchoring(self):
        now = datetime.fromisoformat("2026-07-27T22:00:02+02:00")
        records = [
            self.record("100", "2026-07-27T20:31:00+02:00"),
            self.record("100", "2026-07-27T21:01:00+02:00"),
            self.record("100", "2026-07-27T21:54:00+02:00"),
        ]

        with self.assertRaisesRegex(
            fetch_prices_emhass.LoadHistoryError,
            r"newest finite sample is stale \(6\.0 minutes old; maximum 5\)",
        ):
            fetch_prices_emhass.validate_load_history_records(
                records,
                prediction_horizon=3,
                step_min=30,
                now=now,
            )

    def test_accepts_fresh_sample_in_first_autumn_fold(self):
        now = datetime(
            2026,
            10,
            25,
            2,
            34,
            tzinfo=ZoneInfo("Europe/Amsterdam"),
            fold=0,
        )

        summary = fetch_prices_emhass.validate_load_history_records(
            [self.record("100", "2026-10-25T02:31:00+02:00")],
            prediction_horizon=1,
            step_min=30,
            now=now,
        )

        self.assertEqual(
            summary,
            {
                "mode": "healthy",
                "required_bins": 1,
                "covered_bins": 1,
                "missing_bin_count": 0,
                "missing_bins": [],
                "interpolation_method": None,
                "newest_sample_at": "2026-10-25T00:31:00+00:00",
                "newest_sample_age_minutes": 3.0,
            },
        )

    def test_second_autumn_fold_rejects_first_fold_sample_as_stale(self):
        now = datetime(
            2026,
            10,
            25,
            2,
            34,
            tzinfo=ZoneInfo("Europe/Amsterdam"),
            fold=1,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            r"newest finite sample is stale \(63\.0 minutes old; maximum 5\)",
        ):
            fetch_prices_emhass.validate_load_history_records(
                [self.record("100", "2026-10-25T02:31:00+02:00")],
                prediction_horizon=1,
                step_min=30,
                now=now,
            )

    def test_autumn_repeated_hour_has_four_distinct_half_hour_bins(self):
        now = datetime(
            2026,
            10,
            25,
            2,
            34,
            tzinfo=ZoneInfo("Europe/Amsterdam"),
            fold=1,
        )
        records = [
            self.record("100", "2026-10-25T02:01:00+02:00"),
            self.record("100", "2026-10-25T02:31:00+02:00"),
            self.record("100", "2026-10-25T02:01:00+01:00"),
            self.record("100", "2026-10-25T02:31:00+01:00"),
        ]

        summary = fetch_prices_emhass.validate_load_history_records(
            records,
            prediction_horizon=4,
            step_min=30,
            now=now,
        )

        self.assertEqual(
            summary,
            {
                "mode": "healthy",
                "required_bins": 4,
                "covered_bins": 4,
                "missing_bin_count": 0,
                "missing_bins": [],
                "interpolation_method": None,
                "newest_sample_at": "2026-10-25T01:31:00+00:00",
                "newest_sample_age_minutes": 3.0,
            },
        )

    def test_autumn_fold_rejects_missing_older_utc_bin(self):
        now = datetime(
            2026,
            10,
            25,
            2,
            34,
            tzinfo=ZoneInfo("Europe/Amsterdam"),
            fold=1,
        )
        records = [
            self.record("100", "2026-10-25T02:01:00+02:00"),
            self.record("100", "2026-10-25T02:31:00+02:00"),
            self.record("100", "2026-10-25T02:01:00+01:00"),
            self.record("100", "2026-10-25T02:29:00+01:00"),
        ]

        with self.assertRaisesRegex(
            RuntimeError,
            r"2026-10-25T01:30:00\+02:00",
        ):
            fetch_prices_emhass.validate_load_history_records(
                records,
                prediction_horizon=4,
                step_min=30,
                now=now,
            )

    def test_spring_forward_accepts_thirty_two_real_utc_bins(self):
        now = datetime(
            2026,
            3,
            29,
            3,
            34,
            tzinfo=ZoneInfo("Europe/Amsterdam"),
        )
        timestamps = [
            "2026-03-28T11:01:00+01:00",
            "2026-03-28T11:31:00+01:00",
            "2026-03-28T12:01:00+01:00",
            "2026-03-28T12:31:00+01:00",
            "2026-03-28T13:01:00+01:00",
            "2026-03-28T13:31:00+01:00",
            "2026-03-28T14:01:00+01:00",
            "2026-03-28T14:31:00+01:00",
            "2026-03-28T15:01:00+01:00",
            "2026-03-28T15:31:00+01:00",
            "2026-03-28T16:01:00+01:00",
            "2026-03-28T16:31:00+01:00",
            "2026-03-28T17:01:00+01:00",
            "2026-03-28T17:31:00+01:00",
            "2026-03-28T18:01:00+01:00",
            "2026-03-28T18:31:00+01:00",
            "2026-03-28T19:01:00+01:00",
            "2026-03-28T19:31:00+01:00",
            "2026-03-28T20:01:00+01:00",
            "2026-03-28T20:31:00+01:00",
            "2026-03-28T21:01:00+01:00",
            "2026-03-28T21:31:00+01:00",
            "2026-03-28T22:01:00+01:00",
            "2026-03-28T22:31:00+01:00",
            "2026-03-28T23:01:00+01:00",
            "2026-03-28T23:31:00+01:00",
            "2026-03-29T00:01:00+01:00",
            "2026-03-29T00:31:00+01:00",
            "2026-03-29T01:01:00+01:00",
            "2026-03-29T01:31:00+01:00",
            "2026-03-29T03:01:00+02:00",
            "2026-03-29T03:31:00+02:00",
        ]

        summary = fetch_prices_emhass.validate_load_history_records(
            [self.record("100", timestamp) for timestamp in timestamps],
            prediction_horizon=32,
            step_min=30,
            now=now,
        )

        self.assertEqual(
            summary,
            {
                "mode": "healthy",
                "required_bins": 32,
                "covered_bins": 32,
                "missing_bin_count": 0,
                "missing_bins": [],
                "interpolation_method": None,
                "newest_sample_at": "2026-03-29T01:31:00+00:00",
                "newest_sample_age_minutes": 3.0,
            },
        )

    def test_offsetless_ha_timestamp_cannot_satisfy_history(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "no finite nonnegative samples",
        ):
            fetch_prices_emhass.validate_load_history_records(
                [self.record("100", "2026-07-26T15:32:00")],
                prediction_horizon=1,
                step_min=30,
                now=self.now,
            )

    def test_rejects_http_200_history_with_all_unavailable_states(self):
        records = [
            self.record("unavailable", "2026-07-26T14:31:00+02:00"),
            self.record("unknown", "2026-07-26T15:01:00+02:00"),
            self.record("", "2026-07-26T15:32:00+02:00"),
        ]

        with self.assertRaisesRegex(RuntimeError, "no finite nonnegative samples"):
            fetch_prices_emhass.validate_load_history_records(
                records,
                prediction_horizon=3,
                step_min=30,
                now=self.now,
            )

    def test_rejects_missing_trailing_current_bin(self):
        records = [
            self.record("125", "2026-07-26T14:31:00+02:00"),
            self.record("250", "2026-07-26T15:01:00+02:00"),
        ]

        with self.assertRaisesRegex(RuntimeError, "newest finite sample is stale"):
            fetch_prices_emhass.validate_load_history_records(
                records,
                prediction_horizon=3,
                step_min=30,
                now=self.now,
            )

    def test_rejects_stale_thirty_two_values_from_older_bins(self):
        first = datetime(2026, 7, 25, 23, 31, tzinfo=self.now.tzinfo)
        records = [
            self.record(str(100 + index), (first + timedelta(minutes=30 * index)).isoformat())
            for index in range(32)
        ]

        with self.assertRaisesRegex(RuntimeError, "newest finite sample is stale"):
            fetch_prices_emhass.validate_load_history_records(
                records,
                prediction_horizon=32,
                step_min=30,
                now=self.now,
            )

    def test_accepts_valid_zero_watts(self):
        summary = fetch_prices_emhass.validate_load_history_records(
            [self.record("0", "2026-07-26T15:32:00+02:00")],
            prediction_horizon=1,
            step_min=30,
            now=self.now,
        )

        self.assertEqual(summary["mode"], "healthy")
        self.assertEqual(summary["covered_bins"], 1)
        self.assertEqual(summary["newest_sample_age_minutes"], 2.0)

    def test_rejects_nonfinite_and_negative_samples(self):
        for state in ("nan", "inf", "-1"):
            with self.subTest(state=state):
                with self.assertRaisesRegex(
                    RuntimeError, "no finite nonnegative samples"
                ):
                    fetch_prices_emhass.validate_load_history_records(
                        [self.record(state, "2026-07-26T15:32:00+02:00")],
                        prediction_horizon=1,
                        step_min=30,
                        now=self.now,
                    )


class NativeLoadHistoryConfigTests(unittest.TestCase):
    def test_reads_load_entity_and_thirty_minute_step_from_live_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            (share / "config.json").write_text(
                json.dumps(
                    {
                        "sensor_power_load_no_var_loads":
                            "sensor.emhass_house_load_sample_w",
                        "optimization_time_step": 30,
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                fetch_prices_emhass.read_load_history_config(share),
                ("sensor.emhass_house_load_sample_w", 30),
            )

    def test_rejects_malformed_live_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            (share / "config.json").write_text("{not json", encoding="utf-8")

            with self.assertRaisesRegex(
                fetch_prices_emhass.LoadHistoryError,
                "Invalid EMHASS config",
            ):
                fetch_prices_emhass.read_load_history_config(share)

    def test_normalizes_invalid_utf8_live_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            (share / "config.json").write_bytes(b"\xff")

            with self.assertRaises(
                fetch_prices_emhass.LoadHistoryError
            ) as raised:
                fetch_prices_emhass.read_load_history_config(share)

        self.assertIsInstance(raised.exception.__cause__, UnicodeDecodeError)


class FetchNativeLoadHistoryTests(unittest.TestCase):
    @staticmethod
    def response(payload):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(payload).encode("utf-8")
        return response

    @staticmethod
    def write_secrets(root: Path) -> Path:
        secrets = root / "secrets.yaml"
        secrets.write_text(
            "hass_url: http://ha.local:8123\n"
            "long_lived_token: history-token\n",
            encoding="utf-8",
        )
        return secrets

    @patch.object(fetch_prices_emhass, "urlopen")
    def test_uses_existing_secrets_and_home_assistant_history_endpoint(
        self, mock_urlopen
    ):
        record = {
            "entity_id": "sensor.emhass_house_load_sample_w",
            "state": "250",
            "last_changed": "2026-07-26T15:31:00+02:00",
            "last_updated": "2026-07-26T15:31:00+02:00",
            "attributes": {},
        }
        mock_urlopen.return_value = self.response([[record]])

        with tempfile.TemporaryDirectory() as tmp:
            secrets = Path(tmp) / "secrets.yaml"
            secrets.write_text(
                "hass_url: http://ha.local:8123\n"
                "long_lived_token: history-token\n",
                encoding="utf-8",
            )
            start = datetime.fromisoformat("2026-07-26T14:30:00+02:00")
            end = datetime.fromisoformat("2026-07-26T15:34:00+02:00")

            records = fetch_prices_emhass.fetch_load_history_from_ha(
                secrets,
                "sensor.emhass_house_load_sample_w",
                start=start,
                end=end,
            )

        self.assertEqual(records, [record])
        request = mock_urlopen.call_args.args[0]
        parsed = urlsplit(request.full_url)
        self.assertEqual(
            unquote(parsed.path),
            "/api/history/period/2026-07-26T14:30:00+02:00",
        )
        self.assertEqual(
            parse_qs(parsed.query),
            {
                "filter_entity_id": ["sensor.emhass_house_load_sample_w"],
                "end_time": ["2026-07-26T15:34:00+02:00"],
                "no_attributes": ["true"],
            },
        )
        self.assertEqual(
            request.get_header("Authorization"),
            "Bearer history-token",
        )

    @patch.object(Path, "read_text", side_effect=PermissionError("denied"))
    @patch.object(Path, "is_file", return_value=True)
    def test_normalizes_secrets_read_oserror(
        self, _mock_is_file, _mock_read_text
    ):
        with self.assertRaises(
            fetch_prices_emhass.LoadHistoryError
        ) as raised:
            fetch_prices_emhass.fetch_load_history_from_ha(
                Path("/secrets/secrets_emhass.yaml"),
                "sensor.emhass_house_load_sample_w",
                start=datetime(2026, 7, 26, 12, 30, tzinfo=timezone.utc),
                end=datetime(2026, 7, 26, 13, 34, tzinfo=timezone.utc),
            )

        self.assertIsInstance(raised.exception.__cause__, PermissionError)

    @patch.object(
        Path,
        "read_text",
        side_effect=UnicodeDecodeError(
            "utf-8",
            b"\xff",
            0,
            1,
            "invalid start byte",
        ),
    )
    @patch.object(Path, "is_file", return_value=True)
    def test_normalizes_invalid_utf8_secrets(
        self, _mock_is_file, _mock_read_text
    ):
        with self.assertRaises(
            fetch_prices_emhass.LoadHistoryError
        ) as raised:
            fetch_prices_emhass.fetch_load_history_from_ha(
                Path("/secrets/secrets_emhass.yaml"),
                "sensor.emhass_house_load_sample_w",
                start=datetime(2026, 7, 26, 12, 30, tzinfo=timezone.utc),
                end=datetime(2026, 7, 26, 13, 34, tzinfo=timezone.utc),
            )

        self.assertIsInstance(raised.exception.__cause__, UnicodeDecodeError)

    @patch.object(fetch_prices_emhass, "Request")
    def test_normalizes_request_value_error(self, mock_request):
        mock_request.side_effect = ValueError("invalid URL")
        with tempfile.TemporaryDirectory() as tmp:
            secrets = self.write_secrets(Path(tmp))

            with self.assertRaises(
                fetch_prices_emhass.LoadHistoryError
            ) as raised:
                fetch_prices_emhass.fetch_load_history_from_ha(
                    secrets,
                    "sensor.emhass_house_load_sample_w",
                    start=datetime(2026, 7, 26, 12, 30, tzinfo=timezone.utc),
                    end=datetime(2026, 7, 26, 13, 34, tzinfo=timezone.utc),
                )

        self.assertIsInstance(raised.exception.__cause__, ValueError)

    def test_normalizes_http_url_and_incomplete_read_failures(self):
        failures = [
            HTTPError(
                url="http://ha.local/history",
                code=500,
                msg="Internal Server Error",
                hdrs=None,
                fp=None,
            ),
            URLError("HA unavailable"),
            IncompleteRead(b"partial", 100),
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with tempfile.TemporaryDirectory() as tmp:
                    secrets = self.write_secrets(Path(tmp))
                    with patch.object(
                        fetch_prices_emhass,
                        "urlopen",
                        side_effect=failure,
                    ):
                        with self.assertRaises(
                            fetch_prices_emhass.LoadHistoryError
                        ) as raised:
                            fetch_prices_emhass.fetch_load_history_from_ha(
                                secrets,
                                "sensor.emhass_house_load_sample_w",
                                start=datetime(
                                    2026,
                                    7,
                                    26,
                                    12,
                                    30,
                                    tzinfo=timezone.utc,
                                ),
                                end=datetime(
                                    2026,
                                    7,
                                    26,
                                    13,
                                    34,
                                    tzinfo=timezone.utc,
                                ),
                            )

                self.assertIs(raised.exception.__cause__, failure)
                if isinstance(failure, HTTPError):
                    failure.close()

    @patch.object(fetch_prices_emhass, "urlopen")
    def test_normalizes_invalid_utf8_response(self, mock_urlopen):
        response = self.response([])
        response.read.return_value = b"\xff"
        mock_urlopen.return_value = response
        with tempfile.TemporaryDirectory() as tmp:
            secrets = self.write_secrets(Path(tmp))

            with self.assertRaises(
                fetch_prices_emhass.LoadHistoryError
            ) as raised:
                fetch_prices_emhass.fetch_load_history_from_ha(
                    secrets,
                    "sensor.emhass_house_load_sample_w",
                    start=datetime(2026, 7, 26, 12, 30, tzinfo=timezone.utc),
                    end=datetime(2026, 7, 26, 13, 34, tzinfo=timezone.utc),
                )

        self.assertIsInstance(raised.exception.__cause__, UnicodeDecodeError)

    @patch.object(fetch_prices_emhass, "urlopen")
    def test_normalizes_malformed_json_response(self, mock_urlopen):
        response = self.response([])
        response.read.return_value = b"{not-json"
        mock_urlopen.return_value = response
        with tempfile.TemporaryDirectory() as tmp:
            secrets = self.write_secrets(Path(tmp))

            with self.assertRaises(
                fetch_prices_emhass.LoadHistoryError
            ) as raised:
                fetch_prices_emhass.fetch_load_history_from_ha(
                    secrets,
                    "sensor.emhass_house_load_sample_w",
                    start=datetime(2026, 7, 26, 12, 30, tzinfo=timezone.utc),
                    end=datetime(2026, 7, 26, 13, 34, tzinfo=timezone.utc),
                )

        self.assertIsInstance(raised.exception.__cause__, json.JSONDecodeError)

    @patch.object(fetch_prices_emhass, "urlopen")
    def test_normalizes_invalid_payload_shapes(self, mock_urlopen):
        for payload in ({}, [{}], [[1]]):
            with self.subTest(payload=payload):
                mock_urlopen.return_value = self.response(payload)
                with tempfile.TemporaryDirectory() as tmp:
                    secrets = self.write_secrets(Path(tmp))

                    with self.assertRaisesRegex(
                        fetch_prices_emhass.LoadHistoryError,
                        "Malformed Home Assistant load-history response",
                    ):
                        fetch_prices_emhass.fetch_load_history_from_ha(
                            secrets,
                            "sensor.emhass_house_load_sample_w",
                            start=datetime(
                                2026,
                                7,
                                26,
                                12,
                                30,
                                tzinfo=timezone.utc,
                            ),
                            end=datetime(
                                2026,
                                7,
                                26,
                                13,
                                34,
                                tzinfo=timezone.utc,
                            ),
                        )

    @patch.object(fetch_prices_emhass, "fetch_load_history_from_ha")
    def test_validation_reads_live_config_and_queries_effective_window(
        self, mock_fetch_history
    ):
        now = datetime(2026, 7, 26, 15, 34, tzinfo=ZoneInfo("Europe/Amsterdam"))
        mock_fetch_history.return_value = [
            NativeLoadHistoryValidationTests.record(
                "125", "2026-07-26T14:31:00+02:00"
            ),
            NativeLoadHistoryValidationTests.record(
                "250", "2026-07-26T15:01:00+02:00"
            ),
            NativeLoadHistoryValidationTests.record(
                "375", "2026-07-26T15:32:00+02:00"
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            (share / "config.json").write_text(
                json.dumps(
                    {
                        "sensor_power_load_no_var_loads":
                            "sensor.emhass_house_load_sample_w",
                        "optimization_time_step": 30,
                    }
                ),
                encoding="utf-8",
            )
            secrets = share / "secrets.yaml"

            summary = fetch_prices_emhass.validate_native_load_history(
                secrets,
                share,
                prediction_horizon=3,
                tz_name="Europe/Amsterdam",
                now=now,
                load_history_config=(
                    "sensor.emhass_house_load_sample_w",
                    30,
                ),
            )

        self.assertEqual(summary["covered_bins"], 3)
        mock_fetch_history.assert_called_once_with(
            secrets,
            "sensor.emhass_house_load_sample_w",
            start=datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc),
            end=datetime(2026, 7, 26, 13, 34, tzinfo=timezone.utc),
        )

    @patch.object(fetch_prices_emhass, "urlopen")
    def test_validation_reuses_preflighted_credentials_without_secrets_reread(
        self,
        mock_urlopen,
    ):
        now = datetime(2026, 7, 26, 15, 34, tzinfo=ZoneInfo("Europe/Amsterdam"))
        mock_urlopen.return_value = self.response(
            [[
                NativeLoadHistoryValidationTests.record(
                    "375",
                    "2026-07-26T15:32:00+02:00",
                )
            ]]
        )
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            missing_secrets = share / "must-not-be-read.yaml"

            try:
                summary = fetch_prices_emhass.validate_native_load_history(
                    missing_secrets,
                    share,
                    prediction_horizon=1,
                    tz_name="Europe/Amsterdam",
                    now=now,
                    load_history_config=(
                        "sensor.emhass_house_load_sample_w",
                        30,
                    ),
                    load_history_credentials=(
                        "http://ha.local:8123/",
                        "preflight-token",
                    ),
                )
            except TypeError as exc:
                self.fail(f"preflighted credentials are not reusable: {exc}")

        self.assertEqual(summary["covered_bins"], 1)
        request = mock_urlopen.call_args.args[0]
        self.assertEqual(
            request.get_header("Authorization"),
            "Bearer preflight-token",
        )

    @patch.object(fetch_prices_emhass, "validate_load_history_records")
    @patch.object(fetch_prices_emhass, "fetch_load_history_from_ha")
    def test_validation_queries_literal_utc_windows_across_dst(
        self,
        mock_fetch_history,
        mock_validate_records,
    ):
        mock_fetch_history.return_value = []
        mock_validate_records.return_value = {
            "mode": "healthy",
            "required_bins": 32,
            "covered_bins": 32,
            "missing_bin_count": 0,
            "missing_bins": [],
            "interpolation_method": None,
            "newest_sample_at": "unused",
            "newest_sample_age_minutes": 1.0,
        }
        cases = [
            (
                datetime(
                    2026,
                    10,
                    25,
                    2,
                    34,
                    tzinfo=ZoneInfo("Europe/Amsterdam"),
                    fold=0,
                ),
                datetime(2026, 10, 24, 8, 30, tzinfo=timezone.utc),
                datetime(2026, 10, 25, 0, 34, tzinfo=timezone.utc),
            ),
            (
                datetime(
                    2026,
                    10,
                    25,
                    2,
                    34,
                    tzinfo=ZoneInfo("Europe/Amsterdam"),
                    fold=1,
                ),
                datetime(2026, 10, 24, 9, 30, tzinfo=timezone.utc),
                datetime(2026, 10, 25, 1, 34, tzinfo=timezone.utc),
            ),
            (
                datetime(
                    2026,
                    3,
                    29,
                    3,
                    34,
                    tzinfo=ZoneInfo("Europe/Amsterdam"),
                ),
                datetime(2026, 3, 28, 9, 30, tzinfo=timezone.utc),
                datetime(2026, 3, 29, 1, 34, tzinfo=timezone.utc),
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            secrets = share / "secrets.yaml"
            for now, expected_start, expected_end in cases:
                with self.subTest(now=now.isoformat(), fold=now.fold):
                    mock_fetch_history.reset_mock()
                    fetch_prices_emhass.validate_native_load_history(
                        secrets,
                        share,
                        prediction_horizon=32,
                        tz_name="Europe/Amsterdam",
                        now=now,
                        load_history_config=(
                            "sensor.emhass_house_load_sample_w",
                            30,
                        ),
                    )
                    mock_fetch_history.assert_called_once_with(
                        secrets,
                        "sensor.emhass_house_load_sample_w",
                        start=expected_start,
                        end=expected_end,
                    )


class MpcLoadHistoryFailureTests(unittest.TestCase):
    VALID_CONFIG = {
        "prediction_horizon": 3,
        "optimization_time_step": 30,
        "sensor_power_load_no_var_loads":
            "sensor.emhass_house_load_sample_w",
    }

    @staticmethod
    def degraded_summary() -> dict:
        return {
            "mode": "degraded",
            "required_bins": 3,
            "covered_bins": 2,
            "missing_bin_count": 1,
            "missing_bins": ["2026-07-26T13:00:00+00:00"],
            "gap_start": "2026-07-26T13:00:00+00:00",
            "gap_end_exclusive": "2026-07-26T13:30:00+00:00",
            "gap_duration_minutes": 30,
            "interpolation_method": "emhass_native_linear",
            "newest_sample_at": "2026-07-26T13:32:00+00:00",
            "newest_sample_age_minutes": 2.0,
        }

    @staticmethod
    def response_bytes(body: bytes):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = body
        return response

    def write_valid_inputs(self, share: Path) -> Path:
        (share / "config.json").write_text(
            json.dumps(self.VALID_CONFIG),
            encoding="utf-8",
        )
        secrets = share / "secrets.yaml"
        secrets.write_text(
            "hass_url: http://ha.local:8123\n"
            "long_lived_token: main-history-token\n",
            encoding="utf-8",
        )
        return secrets

    def invoke_main(self, share: Path, secrets: Path) -> int:
        prices = {
            f"2026-07-26T{hour:02d}:00:00+02:00": 0.20
            for hour in range(10, 22)
        }
        argv = [
            "fetch_prices_emhass.py",
            "--action",
            "mpc",
            "--share-dir",
            str(share),
            "--secrets",
            str(secrets),
        ]
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    fetch_prices_emhass,
                    "fetch_soc_init",
                    return_value=0.5,
                )
            )
            stack.enter_context(
                patch.object(
                    fetch_prices_emhass,
                    "fetch_soc_limit",
                    return_value=0.05,
                )
            )
            stack.enter_context(
                patch.object(
                    fetch_prices_emhass,
                    "price_fetch_summary",
                    return_value={"prediction_horizon_effective": 3},
                )
            )
            stack.enter_context(
                patch.object(
                    fetch_prices_emhass,
                    "refresh_price_csvs_from_ha",
                    return_value=(
                        prices,
                        {},
                        Path("/tmp/load.csv"),
                        Path("/tmp/prod.csv"),
                    ),
                )
            )
            stack.enter_context(
                patch.object(
                    fetch_prices_emhass,
                    "ensure_weather_forecast_cache",
                )
            )
            self.emhass_action = stack.enter_context(
                patch.object(fetch_prices_emhass, "call_emhass_action")
            )
            self.finalizer = stack.enter_context(
                patch.object(
                    fetch_prices_emhass,
                    "finalize_successful_mpc_run",
                    return_value=True,
                )
            )
            stack.enter_context(patch.object(sys, "argv", argv))
            return fetch_prices_emhass.main()

    @staticmethod
    def read_status(share: Path):
        return json.loads(
            (share / "last_enever_run.json").read_text(encoding="utf-8")
        )

    def assert_load_history_abort(self, share: Path, exit_code: int):
        status = self.read_status(share)
        self.assertEqual(exit_code, 1)
        self.assertFalse(status["mpc"]["ok"])
        self.assertEqual(status["mpc"]["failure_kind"], "load_history")
        self.emhass_action.assert_not_called()
        self.finalizer.assert_not_called()
        return status

    def test_abort_mpc_load_history_constructs_fallback_unsafe_summary_when_summary_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            fetch_prices_emhass._abort_mpc_load_history(
                share,
                "HA REST request failed",
                summary=None,
                prediction_horizon=32,
            )
            status = self.read_status(share)
            summary = status["mpc"]["load_history"]
            self.assertEqual(summary["mode"], "unsafe")
            self.assertEqual(summary["required_bins"], 32)
            self.assertEqual(summary["missing_bin_count"], 32)

    def test_dry_run_handles_load_history_error_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            secrets = self.write_valid_inputs(share)
            argv = [
                "fetch_prices_emhass.py",
                "--action",
                "mpc",
                "--dry-run",
                "--share-dir",
                str(share),
                "--secrets",
                str(secrets),
            ]
            stdout = io.StringIO()
            with (
                patch.object(
                    fetch_prices_emhass,
                    "fetch_soc_init",
                    return_value=0.5,
                ),
                patch.object(
                    fetch_prices_emhass,
                    "fetch_soc_limit",
                    return_value=0.05,
                ),
                patch.object(
                    fetch_prices_emhass,
                    "price_fetch_summary",
                    return_value={"prediction_horizon_effective": 3},
                ),
                patch.object(
                    fetch_prices_emhass,
                    "validate_native_load_history",
                    side_effect=fetch_prices_emhass.LoadHistoryError(
                        "HA unavailable"
                    ),
                ),
                patch.object(sys, "argv", argv),
                redirect_stdout(stdout),
            ):
                exit_code = fetch_prices_emhass.main()

            output = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertEqual(output["load_history"]["mode"], "unsafe")
        self.assertEqual(output["load_history"]["error"], "HA unavailable")

    def test_dry_run_with_missing_credentials_does_not_write_status_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            missing_secrets = share / "missing_secrets.yaml"
            argv = [
                "fetch_prices_emhass.py",
                "--action",
                "mpc",
                "--dry-run",
                "--share-dir",
                str(share),
                "--secrets",
                str(missing_secrets),
            ]
            stdout = io.StringIO()
            with patch.object(sys, "argv", argv), redirect_stdout(stdout):
                exit_code = fetch_prices_emhass.main()

            output = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 1)
            self.assertEqual(output["load_history"]["mode"], "unsafe")
            self.assertFalse((share / "last_enever_run.json").is_file())

    def test_degraded_history_runs_identical_native_payload(self):
        summary = self.degraded_summary()
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            secrets = self.write_valid_inputs(share)
            with patch.object(
                fetch_prices_emhass,
                "validate_native_load_history",
                return_value=summary,
            ):
                exit_code = self.invoke_main(share, secrets)

        self.assertEqual(exit_code, 0)
        optim_calls = [
            item for item in self.emhass_action.call_args_list
            if item.args[1] == "naive-mpc-optim"
        ]
        self.assertEqual(len(optim_calls), 1)
        payload = optim_calls[0].args[2]
        self.assertEqual(payload["prediction_horizon"], 3)
        self.assertNotIn("load_power_forecast", payload)
        self.assertNotIn("alpha", payload)
        self.assertNotIn("beta", payload)
        self.assertEqual(
            self.finalizer.call_args.kwargs["load_history"],
            summary,
        )

    def test_unsafe_history_persists_summary_and_skips_emhass(self):
        unsafe_summary = {
            "mode": "unsafe",
            "required_bins": 3,
            "covered_bins": 1,
            "missing_bin_count": 2,
            "missing_bins": [
                "2026-07-26T12:30:00+00:00",
                "2026-07-26T13:00:00+00:00",
            ],
            "interpolation_method": None,
            "newest_sample_at": "2026-07-26T13:32:00+00:00",
            "newest_sample_age_minutes": 2.0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            secrets = self.write_valid_inputs(share)
            with patch.object(
                fetch_prices_emhass,
                "validate_native_load_history",
                side_effect=fetch_prices_emhass.LoadHistoryError(
                    "unsafe gap",
                    summary=unsafe_summary,
                ),
            ):
                exit_code = self.invoke_main(share, secrets)
            status = self.assert_load_history_abort(share, exit_code)

        self.assertEqual(status["mpc"]["load_history"], unsafe_summary)

    def test_dry_run_fetches_history_and_reports_mode_without_writes(self):
        summary = self.degraded_summary()
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            secrets = self.write_valid_inputs(share)
            argv = [
                "fetch_prices_emhass.py",
                "--action",
                "mpc",
                "--dry-run",
                "--share-dir",
                str(share),
                "--secrets",
                str(secrets),
            ]
            stdout = io.StringIO()
            with (
                patch.object(
                    fetch_prices_emhass,
                    "fetch_soc_init",
                    return_value=0.5,
                ),
                patch.object(
                    fetch_prices_emhass,
                    "fetch_soc_limit",
                    return_value=0.05,
                ),
                patch.object(
                    fetch_prices_emhass,
                    "price_fetch_summary",
                    return_value={"prediction_horizon_effective": 3},
                ),
                patch.object(
                    fetch_prices_emhass,
                    "validate_native_load_history",
                    return_value=summary,
                ),
                patch.object(
                    fetch_prices_emhass,
                    "refresh_price_csvs_from_ha",
                ) as refresh_prices,
                patch.object(
                    fetch_prices_emhass,
                    "run_mpc_chain",
                ) as run_chain,
                patch.object(
                    fetch_prices_emhass,
                    "write_run_status",
                ) as write_status,
                patch.object(
                    fetch_prices_emhass,
                    "finalize_successful_mpc_run",
                ) as finalizer,
                patch.object(sys, "argv", argv),
                redirect_stdout(stdout),
            ):
                exit_code = fetch_prices_emhass.main()

            output = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertEqual(output["load_history"], summary)
        refresh_prices.assert_not_called()
        run_chain.assert_not_called()
        write_status.assert_not_called()
        finalizer.assert_not_called()

    def test_optional_soc_contains_protocol_decode_and_invalid_value_failures(self):
        failures = [
            IncompleteRead(b"partial", 100),
            self.response_bytes(b"\xff"),
            self.response_bytes(b"[]"),
            self.response_bytes(b"null"),
            self.response_bytes(json.dumps({"state": "nan"}).encode("utf-8")),
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                urlopen_result = (
                    {"side_effect": failure}
                    if isinstance(failure, BaseException)
                    else {"return_value": failure}
                )
                with patch.object(
                    fetch_prices_emhass,
                    "urlopen",
                    **urlopen_result,
                ):
                    try:
                        result = fetch_prices_emhass.fetch_soc_init(
                            Path("/must-not-be-read.yaml"),
                            "sensor.test_soc",
                            credentials=(
                                "http://ha.local:8123/",
                                "preflight-token",
                            ),
                        )
                    except (AttributeError, IncompleteRead, UnicodeError) as exc:
                        self.fail(
                            f"optional SOC failure escaped its boundary: {exc}"
                        )

                self.assertIsNone(result)

    def test_history_domain_failure_aborts_without_action_or_finalizer(self):
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            secrets = self.write_valid_inputs(share)
            with patch.object(
                fetch_prices_emhass,
                "validate_native_load_history",
                side_effect=fetch_prices_emhass.LoadHistoryError(
                    "current bin missing"
                ),
            ):
                exit_code = self.invoke_main(share, secrets)
            status = self.assert_load_history_abort(share, exit_code)

        self.assertIn("current bin missing", status["mpc"]["error"])

    def test_malformed_json_response_records_load_history_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            secrets = self.write_valid_inputs(share)
            response = self.response_bytes(b"{not-json")
            with patch.object(
                fetch_prices_emhass,
                "urlopen",
                return_value=response,
            ):
                exit_code = self.invoke_main(share, secrets)
            status = self.assert_load_history_abort(share, exit_code)

        self.assertIn("malformed JSON", status["mpc"]["error"])
        self.assertNotIn("{not-json", status["mpc"]["error"])
        self.assertNotIn("main-history-token", status["mpc"]["error"])

    def test_incomplete_read_records_load_history_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            secrets = self.write_valid_inputs(share)
            response = self.response_bytes(b"")
            response.read.side_effect = IncompleteRead(b"partial", 100)
            with patch.object(
                fetch_prices_emhass,
                "urlopen",
                return_value=response,
            ):
                exit_code = self.invoke_main(share, secrets)
            status = self.assert_load_history_abort(share, exit_code)

        self.assertIn("request failed", status["mpc"]["error"])
        self.assertNotIn("partial", status["mpc"]["error"])
        self.assertNotIn("main-history-token", status["mpc"]["error"])

    def test_malformed_config_root_is_classified_before_permissive_readers(self):
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            secrets = share / "secrets.yaml"
            (share / "config.json").write_text("[]", encoding="utf-8")

            exit_code = self.invoke_main(share, secrets)
            status = self.assert_load_history_abort(share, exit_code)

        self.assertIn("root must be an object", status["mpc"]["error"])

    def test_invalid_utf8_config_is_classified_before_permissive_readers(self):
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            secrets = share / "secrets.yaml"
            (share / "config.json").write_bytes(b"\xff")

            exit_code = self.invoke_main(share, secrets)
            status = self.assert_load_history_abort(share, exit_code)

        self.assertIn("Invalid EMHASS config", status["mpc"]["error"])

    def test_secrets_permission_error_is_classified_before_soc_and_price_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            secrets = self.write_valid_inputs(share)
            argv = [
                "fetch_prices_emhass.py",
                "--action",
                "mpc",
                "--share-dir",
                str(share),
                "--secrets",
                str(secrets),
            ]
            original_read_text = Path.read_text

            def read_text(path, *args, **kwargs):
                if path == secrets:
                    raise PermissionError("secrets denied")
                return original_read_text(path, *args, **kwargs)

            with (
                patch.object(Path, "read_text", autospec=True, side_effect=read_text),
                patch.object(
                    fetch_prices_emhass,
                    "call_emhass_action",
                ) as emhass_action,
                patch.object(
                    fetch_prices_emhass,
                    "finalize_successful_mpc_run",
                ) as finalizer,
                patch.object(sys, "argv", argv),
            ):
                try:
                    exit_code = fetch_prices_emhass.main()
                except PermissionError as exc:
                    self.fail(f"secrets PermissionError escaped main: {exc}")

            status = self.read_status(share)

        self.assertEqual(exit_code, 1)
        self.assertFalse(status["mpc"]["ok"])
        self.assertEqual(status["mpc"]["failure_kind"], "load_history")
        self.assertNotIn("secrets denied", status["mpc"]["error"])
        emhass_action.assert_not_called()
        finalizer.assert_not_called()

    def test_invalid_credentials_abort_before_any_home_assistant_request(self):
        cases = [
            ("missing file", None),
            ("invalid UTF-8", b"\xff"),
            ("missing token", b"hass_url: http://ha.local:8123\n"),
            (
                "unsupported URL",
                b"hass_url: ftp://ha.local\nlong_lived_token: secret-token\n",
            ),
            (
                "malformed URL",
                b"hass_url: http://[bad\nlong_lived_token: secret-token\n",
            ),
            (
                "nonnumeric port",
                (
                    b"hass_url: http://ha.local:notaport\n"
                    b"long_lived_token: secret-token\n"
                ),
            ),
            (
                "out-of-range port",
                (
                    b"hass_url: http://ha.local:99999\n"
                    b"long_lived_token: secret-token\n"
                ),
            ),
            (
                "invalid header token",
                (
                    "hass_url: http://ha.local:8123\n"
                    "long_lived_token: token-\N{SNOWMAN}\n"
                ).encode("utf-8"),
            ),
        ]
        for label, secrets_content in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                share = Path(tmp)
                (share / "config.json").write_text(
                    json.dumps(self.VALID_CONFIG),
                    encoding="utf-8",
                )
                secrets = share / "secrets.yaml"
                if secrets_content is not None:
                    secrets.write_bytes(secrets_content)
                argv = [
                    "fetch_prices_emhass.py",
                    "--action",
                    "mpc",
                    "--share-dir",
                    str(share),
                    "--secrets",
                    str(secrets),
                ]
                with (
                    patch.object(fetch_prices_emhass, "urlopen") as mock_urlopen,
                    patch.object(
                        fetch_prices_emhass,
                        "call_emhass_action",
                    ) as emhass_action,
                    patch.object(
                        fetch_prices_emhass,
                        "finalize_successful_mpc_run",
                    ) as finalizer,
                    patch.object(sys, "argv", argv),
                ):
                    try:
                        exit_code = fetch_prices_emhass.main()
                    except (TypeError, UnicodeError, ValueError) as exc:
                        self.fail(
                            f"{label} escaped credential preflight: {exc}"
                        )

                status = self.read_status(share)

                self.assertEqual(exit_code, 1)
                self.assertFalse(status["mpc"]["ok"])
                self.assertEqual(
                    status["mpc"].get("failure_kind"),
                    "load_history",
                )
                self.assertNotIn("secret-token", status["mpc"]["error"])
                mock_urlopen.assert_not_called()
                emhass_action.assert_not_called()
                finalizer.assert_not_called()

    def test_price_endpoint_error_remains_a_price_failure_after_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            secrets = self.write_valid_inputs(share)
            argv = [
                "fetch_prices_emhass.py",
                "--action",
                "mpc",
                "--share-dir",
                str(share),
                "--secrets",
                str(secrets),
            ]
            with (
                patch.object(
                    fetch_prices_emhass,
                    "fetch_soc_init",
                    return_value=0.5,
                ),
                patch.object(
                    fetch_prices_emhass,
                    "fetch_soc_limit",
                    return_value=0.05,
                ),
                patch.object(
                    fetch_prices_emhass,
                    "price_fetch_summary",
                    side_effect=URLError("price endpoint unavailable"),
                ),
                patch.object(
                    fetch_prices_emhass,
                    "call_emhass_action",
                ) as emhass_action,
                patch.object(
                    fetch_prices_emhass,
                    "finalize_successful_mpc_run",
                ) as finalizer,
                patch.object(sys, "argv", argv),
            ):
                exit_code = fetch_prices_emhass.main()

            status = self.read_status(share)

        self.assertEqual(exit_code, 1)
        self.assertFalse(status["prices"]["ok"])
        self.assertFalse(status["mpc"]["ok"])
        self.assertIn("price fetch failed", status["mpc"]["error"])
        self.assertNotEqual(
            status["mpc"].get("failure_kind"),
            "load_history",
        )
        emhass_action.assert_not_called()
        finalizer.assert_not_called()

    def test_malformed_price_state_shapes_use_price_abort(self):
        payloads = [
            [],
            None,
            {"state": "0.20", "attributes": []},
            {"state": "0.20", "attributes": None},
        ]
        for payload in payloads:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as tmp:
                share = Path(tmp)
                secrets = self.write_valid_inputs(share)
                argv = [
                    "fetch_prices_emhass.py",
                    "--action",
                    "mpc",
                    "--share-dir",
                    str(share),
                    "--secrets",
                    str(secrets),
                ]
                response = self.response_bytes(
                    json.dumps(payload).encode("utf-8")
                )
                with (
                    patch.object(
                        fetch_prices_emhass,
                        "fetch_soc_init",
                        return_value=0.5,
                    ),
                    patch.object(
                        fetch_prices_emhass,
                        "fetch_soc_limit",
                        return_value=0.05,
                    ),
                    patch.object(
                        fetch_prices_emhass,
                        "urlopen",
                        return_value=response,
                    ),
                    patch.object(
                        fetch_prices_emhass,
                        "validate_native_load_history",
                    ) as validate_history,
                    patch.object(
                        fetch_prices_emhass,
                        "run_mpc_chain",
                    ) as run_mpc_chain,
                    patch.object(
                        fetch_prices_emhass,
                        "finalize_successful_mpc_run",
                    ) as finalizer,
                    patch.object(sys, "argv", argv),
                ):
                    try:
                        exit_code = fetch_prices_emhass.main()
                    except AttributeError as exc:
                        self.fail(
                            f"malformed price state escaped its boundary: {exc}"
                        )

                status = self.read_status(share)

                self.assertEqual(exit_code, 1)
                self.assertFalse(status["prices"]["ok"])
                self.assertFalse(status["mpc"]["ok"])
                self.assertIn("price fetch failed", status["mpc"]["error"])
                self.assertNotEqual(
                    status["mpc"].get("failure_kind"),
                    "load_history",
                )
                validate_history.assert_not_called()
                run_mpc_chain.assert_not_called()
                finalizer.assert_not_called()

    def test_price_materialization_oserror_remains_a_price_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            secrets = self.write_valid_inputs(share)
            argv = [
                "fetch_prices_emhass.py",
                "--action",
                "mpc",
                "--share-dir",
                str(share),
                "--secrets",
                str(secrets),
            ]
            with (
                patch.object(
                    fetch_prices_emhass,
                    "fetch_soc_init",
                    return_value=0.5,
                ),
                patch.object(
                    fetch_prices_emhass,
                    "fetch_soc_limit",
                    return_value=0.05,
                ),
                patch.object(
                    fetch_prices_emhass,
                    "price_fetch_summary",
                    return_value={"prediction_horizon_effective": 3},
                ),
                patch.object(
                    fetch_prices_emhass,
                    "refresh_price_csvs_from_ha",
                    side_effect=PermissionError("price CSV denied"),
                ),
                patch.object(
                    fetch_prices_emhass,
                    "validate_native_load_history",
                ) as validate_history,
                patch.object(
                    fetch_prices_emhass,
                    "call_emhass_action",
                ) as emhass_action,
                patch.object(
                    fetch_prices_emhass,
                    "finalize_successful_mpc_run",
                ) as finalizer,
                patch.object(sys, "argv", argv),
            ):
                try:
                    exit_code = fetch_prices_emhass.main()
                except PermissionError as exc:
                    self.fail(f"price materialization OSError escaped: {exc}")

            status = self.read_status(share)

        self.assertEqual(exit_code, 1)
        self.assertFalse(status["prices"]["ok"])
        self.assertFalse(status["mpc"]["ok"])
        self.assertIn("price fetch failed", status["mpc"]["error"])
        self.assertNotEqual(
            status["mpc"].get("failure_kind"),
            "load_history",
        )
        validate_history.assert_not_called()
        emhass_action.assert_not_called()
        finalizer.assert_not_called()

    def test_success_path_reuses_one_snapshot_through_success_finalization(self):
        frozen_now = datetime(
            2026,
            7,
            27,
            15,
            34,
            tzinfo=ZoneInfo("Europe/Amsterdam"),
        )

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return frozen_now.replace(tzinfo=None)
                return frozen_now.astimezone(tz)

        forecast = [
            {
                "datetime": (
                    frozen_now.replace(minute=0, second=0, microsecond=0)
                    + timedelta(hours=index)
                ).isoformat(),
                "electricity_price": 2_000_000,
            }
            for index in range(13)
        ]
        price_state = {
            "entity_id": "sensor.test_price",
            "state": "0.20",
            "last_changed": frozen_now.isoformat(),
            "last_updated": frozen_now.isoformat(),
            "attributes": {"forecast": forecast},
        }
        history = [[
            NativeLoadHistoryValidationTests.record(
                "125",
                "2026-07-27T14:31:00+02:00",
            ),
            NativeLoadHistoryValidationTests.record(
                "250",
                "2026-07-27T15:01:00+02:00",
            ),
            NativeLoadHistoryValidationTests.record(
                "375",
                "2026-07-27T15:32:00+02:00",
            ),
        ]]

        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            secrets = self.write_valid_inputs(share)
            argv = [
                "fetch_prices_emhass.py",
                "--action",
                "mpc",
                "--share-dir",
                str(share),
                "--secrets",
                str(secrets),
                "--soc-sensor",
                "sensor.test_soc",
                "--soc-min-sensor",
                "sensor.test_soc_min",
                "--soc-max-sensor",
                "sensor.test_soc_max",
                "--price-sensor",
                "sensor.test_price",
            ]
            original_read_text = Path.read_text
            secrets_reads = 0

            def read_text(path, *args, **kwargs):
                nonlocal secrets_reads
                if path == secrets:
                    secrets_reads += 1
                return original_read_text(path, *args, **kwargs)

            soc_min_response = self.response_bytes(
                json.dumps({"state": "5"}).encode("utf-8")
            )
            soc_max_response = self.response_bytes(
                json.dumps({"state": "95"}).encode("utf-8")
            )
            price_response = self.response_bytes(
                json.dumps(price_state).encode("utf-8")
            )
            history_response = self.response_bytes(
                json.dumps(history).encode("utf-8")
            )
            marker_response = self.response_bytes(b"[]")
            with (
                patch.object(Path, "read_text", autospec=True, side_effect=read_text),
                patch.object(
                    fetch_prices_emhass,
                    "datetime",
                    FrozenDateTime,
                ),
                patch.object(
                    fetch_prices_emhass,
                    "urlopen",
                    side_effect=[
                        URLError("optional SOC unavailable"),
                        soc_min_response,
                        soc_max_response,
                        price_response,
                        price_response,
                        price_response,
                        history_response,
                        marker_response,
                    ],
                ) as mock_urlopen,
                patch.object(
                    fetch_prices_emhass,
                    "run_mpc_chain",
                ) as run_mpc_chain,
                patch.object(sys, "argv", argv),
            ):
                exit_code = fetch_prices_emhass.main()

            request_paths = [
                urlsplit(call.args[0].full_url).path
                for call in mock_urlopen.call_args_list
            ]
            request_authorizations = [
                call.args[0].get_header("Authorization")
                for call in mock_urlopen.call_args_list
            ]
            request_hosts = [
                urlsplit(call.args[0].full_url).netloc
                for call in mock_urlopen.call_args_list
            ]
            status = self.read_status(share)

        self.assertEqual(exit_code, 0)
        self.assertEqual(secrets_reads, 1)
        self.assertEqual(
            request_paths,
            [
                "/api/states/sensor.test_soc",
                "/api/states/sensor.test_soc_min",
                "/api/states/sensor.test_soc_max",
                "/api/states/sensor.test_price",
                "/api/states/sensor.test_price",
                "/api/states/sensor.test_price",
                "/api/history/period/2026-07-27T12%3A00%3A00%2B00%3A00",
                "/api/services/input_datetime/set_datetime",
            ],
        )
        self.assertEqual(
            request_authorizations,
            ["Bearer main-history-token"] * 8,
        )
        self.assertEqual(request_hosts, ["ha.local:8123"] * 8)
        self.assertIsNone(run_mpc_chain.call_args.kwargs["soc_init"])
        run_mpc_chain.assert_called_once()
        self.assertTrue(status["mpc"]["ok"])

    def test_unexpected_runtime_error_is_not_reclassified(self):
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            secrets = self.write_valid_inputs(share)
            with patch.object(
                fetch_prices_emhass,
                "validate_native_load_history",
                side_effect=RuntimeError("programmer bug"),
            ):
                with self.assertRaisesRegex(RuntimeError, "programmer bug"):
                    self.invoke_main(share, secrets)

            status = self.read_status(share)

        self.assertNotIn("mpc", status)
        self.emhass_action.assert_not_called()
        self.finalizer.assert_not_called()


class ResolveSocFinalTests(unittest.TestCase):
    def test_uses_ui_min_when_available(self):
        self.assertEqual(
            fetch_prices_emhass.resolve_soc_final(
                omit=False,
                cli_override=None,
                soc_min=0.05,
                share_dir=Path("/nonexistent"),
            ),
            0.05,
        )

    def test_cli_override_wins_over_ui_min(self):
        self.assertEqual(
            fetch_prices_emhass.resolve_soc_final(
                omit=False,
                cli_override=0.6,
                soc_min=0.05,
                share_dir=Path("/nonexistent"),
            ),
            0.6,
        )

    def test_omit_returns_none(self):
        self.assertIsNone(
            fetch_prices_emhass.resolve_soc_final(
                omit=True,
                cli_override=0.6,
                soc_min=0.05,
                share_dir=Path("/nonexistent"),
            ),
        )


class EnsureWeatherSoftRefreshTests(unittest.TestCase):
    def test_ensure_clears_on_soft_refresh_reason_posts_hard(self):
        share = Path(tempfile.mkdtemp())
        try:
            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(
                        fetch_prices_emhass,
                        "weather_cache_refresh_reason",
                        side_effect=["soft age", None],
                    )
                )
                stack.enter_context(
                    patch.object(
                        fetch_prices_emhass,
                        "weather_cache_stale_reason",
                        return_value=None,
                    )
                )
                clear = stack.enter_context(
                    patch.object(fetch_prices_emhass, "clear_weather_cache_files")
                )
                stack.enter_context(
                    patch.object(
                        fetch_prices_emhass,
                        "call_emhass_action_with_retry",
                        return_value=None,
                    )
                )
                fetch_prices_emhass.ensure_weather_forecast_cache(
                    "http://emhass:5000",
                    share,
                    32,
                    force=False,
                )
                clear.assert_called_once()
        finally:
            import shutil

            shutil.rmtree(share, ignore_errors=True)

    def test_ensure_does_not_raise_when_hard_ok(self):
        share = Path(tempfile.mkdtemp())
        try:
            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(
                        fetch_prices_emhass,
                        "weather_cache_refresh_reason",
                        return_value=None,
                    )
                )
                stack.enter_context(
                    patch.object(
                        fetch_prices_emhass,
                        "weather_cache_stale_reason",
                        return_value=None,
                    )
                )
                stack.enter_context(
                    patch.object(fetch_prices_emhass, "clear_weather_cache_files")
                )
                stack.enter_context(
                    patch.object(
                        fetch_prices_emhass,
                        "call_emhass_action_with_retry",
                        return_value=None,
                    )
                )
                fetch_prices_emhass.ensure_weather_forecast_cache(
                    "http://emhass:5000",
                    share,
                    32,
                )
        finally:
            import shutil

            shutil.rmtree(share, ignore_errors=True)


class NaiveMpcPayloadTests(unittest.TestCase):
    def test_payload_uses_configured_native_load_forecast(self):
        payload = fetch_prices_emhass.build_naive_mpc_payload(
            prediction_horizon=3,
            soc_init=0.42,
            soc_final=0.6,
        )

        self.assertEqual(
            payload,
            {
                "weather_forecast_cache_only": True,
                "prediction_horizon": 3,
                "soc_init": 0.42,
                "soc_final": 0.6,
            },
        )
        self.assertNotIn("load_power_forecast", payload)
        self.assertNotIn("alpha", payload)
        self.assertNotIn("beta", payload)

    def test_payload_keeps_soc_optional(self):
        payload = fetch_prices_emhass.build_naive_mpc_payload(
            prediction_horizon=2,
            soc_init=None,
            soc_final=None,
        )

        self.assertEqual(
            payload,
            {
                "weather_forecast_cache_only": True,
                "prediction_horizon": 2,
            },
        )


class PublishHaInputDatetimeTests(unittest.TestCase):
    def setUp(self):
        self.secrets = Path("/tmp/test_publish_mpc_secrets.yaml")
        self.secrets.write_text(
            "hass_url: http://ha.local:8123\nlong_lived_token: test-token\n",
            encoding="utf-8",
        )
        self.completed_at = datetime.fromisoformat("2026-07-19T10:30:12+02:00")

    def tearDown(self):
        self.secrets.unlink(missing_ok=True)

    @staticmethod
    def response():
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"[]"
        return response

    @patch.object(fetch_prices_emhass, "urlopen")
    def test_posts_timestamp_to_input_datetime_service(self, mock_urlopen):
        mock_urlopen.return_value = self.response()

        fetch_prices_emhass.publish_ha_input_datetime(
            self.secrets,
            "input_datetime.emhass_last_successful_mpc",
            self.completed_at,
        )

        request = mock_urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "http://ha.local:8123/api/services/input_datetime/set_datetime",
        )
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {
                "entity_id": "input_datetime.emhass_last_successful_mpc",
                "timestamp": self.completed_at.timestamp(),
            },
        )

    @patch.object(fetch_prices_emhass.time, "sleep")
    @patch.object(fetch_prices_emhass, "urlopen")
    def test_retries_twice_with_same_timestamp(self, mock_urlopen, mock_sleep):
        mock_urlopen.side_effect = [
            URLError("first"),
            URLError("second"),
            self.response(),
        ]

        fetch_prices_emhass.publish_ha_input_datetime(
            self.secrets,
            "input_datetime.emhass_last_successful_mpc",
            self.completed_at,
        )

        self.assertEqual(mock_urlopen.call_count, 3)
        mock_sleep.assert_has_calls([call(2.0), call(2.0)])
        payloads = [
            json.loads(item.args[0].data.decode("utf-8"))
            for item in mock_urlopen.call_args_list
        ]
        self.assertEqual(payloads, [payloads[0], payloads[0], payloads[0]])

    @patch.object(fetch_prices_emhass.time, "sleep")
    @patch.object(fetch_prices_emhass, "urlopen")
    def test_retries_response_read_io_failure(self, mock_urlopen, mock_sleep):
        failed_response = self.response()
        failed_response.read.side_effect = ConnectionResetError("connection reset")
        mock_urlopen.side_effect = [failed_response, self.response()]

        fetch_prices_emhass.publish_ha_input_datetime(
            self.secrets,
            "input_datetime.emhass_last_successful_mpc",
            self.completed_at,
        )

        self.assertEqual(mock_urlopen.call_count, 2)
        mock_sleep.assert_called_once_with(2.0)
        expected_payload = {
            "entity_id": "input_datetime.emhass_last_successful_mpc",
            "timestamp": self.completed_at.timestamp(),
        }
        payloads = [
            json.loads(item.args[0].data.decode("utf-8"))
            for item in mock_urlopen.call_args_list
        ]
        self.assertEqual(payloads, [expected_payload, expected_payload])

    @patch.object(fetch_prices_emhass.time, "sleep")
    @patch.object(fetch_prices_emhass, "urlopen")
    def test_normalizes_exhausted_response_protocol_failures(
        self, mock_urlopen, mock_sleep
    ):
        responses = [self.response() for _ in range(3)]
        for response in responses:
            response.read.side_effect = IncompleteRead(b"partial")
        mock_urlopen.side_effect = responses

        with self.assertRaises(RuntimeError) as raised:
            fetch_prices_emhass.publish_ha_input_datetime(
                self.secrets,
                "input_datetime.emhass_last_successful_mpc",
                self.completed_at,
            )

        self.assertIsInstance(raised.exception.__cause__, IncompleteRead)
        self.assertEqual(mock_urlopen.call_count, 3)
        self.assertEqual(mock_sleep.call_args_list, [call(2.0), call(2.0)])

    @patch.object(fetch_prices_emhass.time, "sleep")
    @patch.object(fetch_prices_emhass, "urlopen")
    def test_raises_after_three_failed_attempts(self, mock_urlopen, mock_sleep):
        mock_urlopen.side_effect = URLError("HA unavailable")

        with self.assertRaisesRegex(
            RuntimeError, "Could not update input_datetime.emhass_last_successful_mpc"
        ):
            fetch_prices_emhass.publish_ha_input_datetime(
                self.secrets,
                "input_datetime.emhass_last_successful_mpc",
                self.completed_at,
            )

        self.assertEqual(mock_urlopen.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    def test_rejects_naive_completion_timestamp(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            fetch_prices_emhass.publish_ha_input_datetime(
                self.secrets,
                "input_datetime.emhass_last_successful_mpc",
                datetime(2026, 7, 19, 10, 30),
            )


class FinalizeSuccessfulMpcRunTests(unittest.TestCase):
    def setUp(self):
        self.secrets = Path("/tmp/finalize_mpc_secrets.yaml")
        self.share = Path("/tmp/finalize_mpc_share")
        self.completed_at = datetime.fromisoformat("2026-07-19T11:00:03+02:00")
        self.load_history = {
            "mode": "degraded",
            "required_bins": 32,
            "covered_bins": 26,
            "missing_bin_count": 6,
            "missing_bins": [
                "2026-07-29T02:30:00+00:00",
                "2026-07-29T03:00:00+00:00",
                "2026-07-29T03:30:00+00:00",
                "2026-07-29T04:00:00+00:00",
                "2026-07-29T04:30:00+00:00",
                "2026-07-29T05:00:00+00:00",
            ],
            "gap_start": "2026-07-29T02:30:00+00:00",
            "gap_end_exclusive": "2026-07-29T05:30:00+00:00",
            "gap_duration_minutes": 180,
            "interpolation_method": "emhass_native_linear",
            "newest_sample_at": "2026-07-29T05:49:00+00:00",
            "newest_sample_age_minutes": 1.0,
        }

    @patch.object(fetch_prices_emhass, "write_run_status")
    @patch.object(fetch_prices_emhass, "publish_ha_input_datetime")
    def test_success_marker_precedes_ok_status(self, mock_publish, mock_status):
        parent = MagicMock()
        parent.attach_mock(mock_publish, "publish")
        parent.attach_mock(mock_status, "status")

        ok = fetch_prices_emhass.finalize_successful_mpc_run(
            self.secrets,
            self.share,
            self.completed_at,
            prediction_horizon=26,
            load_history=self.load_history,
        )

        self.assertTrue(ok)
        self.assertEqual(parent.mock_calls[0][0], "publish")
        mock_publish.assert_called_once_with(
            self.secrets,
            fetch_prices_emhass.MPC_SUCCESS_ENTITY_ID,
            self.completed_at,
        )
        mock_status.assert_any_call(
            self.share,
            "mpc",
            ok=True,
            metadata={
                "prediction_horizon": 26,
                "plan_generated_at": self.completed_at.isoformat(),
                "load_history": self.load_history,
            },
        )
        mock_status.assert_any_call(self.share, "weather", ok=True)

    @patch.object(fetch_prices_emhass, "write_run_status")
    @patch.object(fetch_prices_emhass, "publish_ha_input_datetime")
    def test_marker_failure_records_pipeline_failure(self, mock_publish, mock_status):
        mock_publish.side_effect = RuntimeError("HA unavailable")

        ok = fetch_prices_emhass.finalize_successful_mpc_run(
            self.secrets,
            self.share,
            self.completed_at,
            prediction_horizon=26,
            load_history=self.load_history,
        )

        self.assertFalse(ok)
        mock_status.assert_called_once_with(
            self.share,
            "mpc",
            ok=False,
            error="HA successful-MPC marker failed: HA unavailable",
            failure_kind="status_publish",
            metadata={"load_history": self.load_history},
        )


class MpcChainFailureClassificationTests(unittest.TestCase):
    def setUp(self):
        self.share = Path("/tmp/mpc-chain-failure-share")
        self.degraded = {
            "mode": "degraded",
            "missing_bin_count": 1,
            "missing_bins": ["2026-07-29T03:00:00+00:00"],
        }
        self.healthy = {
            "mode": "healthy",
            "missing_bin_count": 0,
            "missing_bins": [],
        }

    @patch.object(fetch_prices_emhass, "write_run_status")
    def test_degraded_load_forecast_error_is_classified(
        self,
        mock_status,
    ):
        error = "naive-mpc-optim get_mix_forecast failed"
        fetch_prices_emhass.write_mpc_chain_failure(
            self.share,
            error,
            load_history=self.degraded,
        )
        mock_status.assert_called_once_with(
            self.share,
            "mpc",
            ok=False,
            error=error,
            failure_kind="load_interpolation",
            metadata={"load_history": self.degraded},
        )

    @patch.object(fetch_prices_emhass, "write_run_status")
    def test_weather_error_wins_over_degraded_load_classification(
        self,
        mock_status,
    ):
        error = "Open-Meteo get_mix_forecast request failed"
        fetch_prices_emhass.write_mpc_chain_failure(
            self.share,
            error,
            load_history=self.degraded,
        )
        self.assertEqual(
            mock_status.call_args_list,
            [
                call(
                    self.share,
                    "mpc",
                    ok=False,
                    error=error,
                    failure_kind="weather",
                    metadata={"load_history": self.degraded},
                ),
                call(
                    self.share,
                    "weather",
                    ok=False,
                    error=error,
                    failure_kind="weather",
                ),
            ],
        )

    @patch.object(fetch_prices_emhass, "write_run_status")
    def test_degraded_generic_solver_error_gets_no_invented_kind(
        self,
        mock_status,
    ):
        error = "optimizer returned infeasible"
        fetch_prices_emhass.write_mpc_chain_failure(
            self.share,
            error,
            load_history=self.degraded,
        )
        mock_status.assert_called_once_with(
            self.share,
            "mpc",
            ok=False,
            error=error,
            failure_kind=None,
            metadata={"load_history": self.degraded},
        )

    @patch.object(fetch_prices_emhass, "write_run_status")
    def test_healthy_load_error_is_not_degraded_interpolation(
        self,
        mock_status,
    ):
        error = "get_load_forecast failed"
        fetch_prices_emhass.write_mpc_chain_failure(
            self.share,
            error,
            load_history=self.healthy,
        )
        mock_status.assert_called_once_with(
            self.share,
            "mpc",
            ok=False,
            error=error,
            failure_kind=None,
            metadata={"load_history": self.healthy},
        )


if __name__ == "__main__":
    unittest.main()
