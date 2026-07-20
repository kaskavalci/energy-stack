import importlib.util
import json
import math
import sys
import unittest
from datetime import datetime
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import MagicMock, call, patch
from urllib.error import HTTPError, URLError


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "lib"))

MODULE_PATH = REPO_ROOT / "emhass-cron" / "fetch_prices_emhass.py"
SPEC = importlib.util.spec_from_file_location("fetch_prices_emhass", MODULE_PATH)
fetch_prices_emhass = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(fetch_prices_emhass)


class LoadForecastTests(unittest.TestCase):
    def test_constant_load_forecast_has_first_slot_and_rest_300w(self):
        forecast = fetch_prices_emhass.build_load_power_forecast(25, 450.0)

        self.assertEqual(len(forecast), 25)
        self.assertTrue(all(isinstance(value, float) for value in forecast))
        self.assertEqual(forecast[0], 450.0)
        self.assertEqual(set(forecast[1:]), {300.0})

    def test_constant_load_forecast_rejects_zero_horizon(self):
        with self.assertRaises(ValueError):
            fetch_prices_emhass.build_load_power_forecast(0, 300.0)

    def test_constant_load_forecast_rejects_invalid_horizon(self):
        with self.assertRaises(ValueError):
            fetch_prices_emhass.build_load_power_forecast("32", 300.0)


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


class FetchCurrentLoadTests(unittest.TestCase):
    def setUp(self):
        self.secrets = Path("/tmp/test_secrets_emhass.yaml")
        self.sensor = "sensor.electricity_meter_power_consumption"

    @patch.object(fetch_prices_emhass, "fetch_ha_state")
    def test_http_error_falls_back_to_300w(self, mock_fetch):
        mock_fetch.side_effect = HTTPError(
            url="http://ha/api/states/x",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=None,
        )
        self.assertEqual(
            fetch_prices_emhass.fetch_current_load(self.secrets, self.sensor),
            300.0,
        )

    @patch.object(fetch_prices_emhass, "fetch_ha_state")
    def test_unavailable_state_falls_back_to_300w(self, mock_fetch):
        mock_fetch.return_value = {"state": "unavailable"}
        self.assertEqual(
            fetch_prices_emhass.fetch_current_load(self.secrets, self.sensor),
            300.0,
        )

    @patch.object(fetch_prices_emhass, "fetch_ha_state")
    def test_unknown_state_falls_back_to_300w(self, mock_fetch):
        mock_fetch.return_value = {"state": "unknown"}
        self.assertEqual(
            fetch_prices_emhass.fetch_current_load(self.secrets, self.sensor),
            300.0,
        )

    @patch.object(fetch_prices_emhass, "fetch_ha_state")
    def test_nan_state_falls_back_to_300w(self, mock_fetch):
        mock_fetch.return_value = {"state": str(math.nan)}
        self.assertEqual(
            fetch_prices_emhass.fetch_current_load(self.secrets, self.sensor),
            300.0,
        )

    @patch.object(fetch_prices_emhass, "fetch_ha_state")
    def test_happy_path_returns_rounded_watts(self, mock_fetch):
        mock_fetch.return_value = {"state": "450.678"}
        self.assertEqual(
            fetch_prices_emhass.fetch_current_load(self.secrets, self.sensor),
            450.68,
        )


class NaiveMpcPayloadTests(unittest.TestCase):
    def test_naive_mpc_payload_includes_constant_load_forecast_and_soc(self):
        payload = fetch_prices_emhass.build_naive_mpc_payload(
            prediction_horizon=3,
            first_slot_load=450.0,
            soc_init=0.42,
            soc_final=0.6,
        )

        self.assertEqual(
            payload,
            {
                "weather_forecast_cache_only": True,
                "soc_init": 0.42,
                "soc_final": 0.6,
                "prediction_horizon": 3,
                "load_power_forecast": [450.0, 300.0, 300.0],
                "alpha": 1.0,
                "beta": 0.0,
            },
        )

    def test_naive_mpc_payload_keeps_soc_optional(self):
        payload = fetch_prices_emhass.build_naive_mpc_payload(
            prediction_horizon=2,
            first_slot_load=300.0,
            soc_init=None,
            soc_final=None,
        )

        self.assertEqual(
            payload,
            {
                "weather_forecast_cache_only": True,
                "prediction_horizon": 2,
                "load_power_forecast": [300.0, 300.0],
                "alpha": 1.0,
                "beta": 0.0,
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
                "load_forecast_mode": "constant_300w",
                "load_forecast_constant_w": 300.0,
                "plan_generated_at": self.completed_at.isoformat(),
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
        )

        self.assertFalse(ok)
        mock_status.assert_called_once_with(
            self.share,
            "mpc",
            ok=False,
            error="HA successful-MPC marker failed: HA unavailable",
            failure_kind="status_publish",
        )


if __name__ == "__main__":
    unittest.main()
