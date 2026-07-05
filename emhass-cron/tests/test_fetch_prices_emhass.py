import importlib.util
import sys
import unittest
from pathlib import Path


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
            },
        )


if __name__ == "__main__":
    unittest.main()
