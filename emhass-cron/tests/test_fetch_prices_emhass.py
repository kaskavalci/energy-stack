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
    def test_constant_load_forecast_has_one_300w_value_per_horizon_slot(self):
        forecast = fetch_prices_emhass.build_load_power_forecast(25)

        self.assertEqual(len(forecast), 25)
        self.assertTrue(all(isinstance(value, float) for value in forecast))
        self.assertEqual(set(forecast), {300.0})

    def test_constant_load_forecast_rejects_zero_horizon(self):
        with self.assertRaises(ValueError):
            fetch_prices_emhass.build_load_power_forecast(0)

    def test_constant_load_forecast_rejects_invalid_horizon(self):
        with self.assertRaises(ValueError):
            fetch_prices_emhass.build_load_power_forecast("32")


class NaiveMpcPayloadTests(unittest.TestCase):
    def test_naive_mpc_payload_includes_constant_load_forecast_and_soc(self):
        payload = fetch_prices_emhass.build_naive_mpc_payload(
            prediction_horizon=3,
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
                "load_power_forecast": [300.0, 300.0, 300.0],
                "alpha": 1.0,
                "beta": 0.0,
            },
        )

    def test_naive_mpc_payload_keeps_soc_optional(self):
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
                "load_power_forecast": [300.0, 300.0],
                "alpha": 1.0,
                "beta": 0.0,
            },
        )


if __name__ == "__main__":
    unittest.main()
