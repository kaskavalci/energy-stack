import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "lib"))

import weather_cache_stale as wcs  # noqa: E402


class WeatherCacheThresholdTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.share = Path(self.tmp.name)
        (self.share / "config.json").write_text(
            json.dumps(
                {
                    "open_meteo_cache_max_age": 150,
                    "optimization_time_step": 30,
                    "prediction_horizon": 32,
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _bounds_ok(self):
        tz = ZoneInfo("Europe/Amsterdam")
        now = datetime.now(tz)
        start = now - timedelta(minutes=30)
        end = now + timedelta(hours=48)
        return start, end

    def test_default_max_age_is_150(self):
        self.assertEqual(wcs.DEFAULT_OPEN_METEO_CACHE_MAX_AGE_MIN, 150)

    def test_refresh_age_is_max_age_minus_two_steps(self):
        self.assertEqual(wcs.read_open_meteo_refresh_age_min(self.share), 90)

    def test_soft_triggers_at_refresh_age_not_before(self):
        start, end = self._bounds_ok()
        with (
            patch.object(wcs, "docker_available", return_value=True),
            patch.object(wcs, "inspect_weather_cache_bounds", return_value=(start, end)),
            patch.object(wcs, "open_meteo_json_age_min", return_value=89.0),
        ):
            self.assertIsNone(
                wcs.weather_cache_refresh_reason(self.share, 32, force=False)
            )
        with (
            patch.object(wcs, "docker_available", return_value=True),
            patch.object(wcs, "inspect_weather_cache_bounds", return_value=(start, end)),
            patch.object(wcs, "open_meteo_json_age_min", return_value=90.0),
        ):
            msg = wcs.weather_cache_refresh_reason(self.share, 32, force=False)
            self.assertIsNotNone(msg)
            self.assertIn("refresh at 90", msg)
            self.assertIn("hard limit 150", msg)

    def test_hard_triggers_at_max_age_not_soft(self):
        start, end = self._bounds_ok()
        with (
            patch.object(wcs, "docker_available", return_value=True),
            patch.object(wcs, "inspect_weather_cache_bounds", return_value=(start, end)),
            patch.object(wcs, "open_meteo_json_age_min", return_value=149.0),
        ):
            self.assertIsNone(
                wcs.weather_cache_stale_reason(self.share, 32, force=False)
            )
            soft = wcs.weather_cache_refresh_reason(self.share, 32, force=False)
            self.assertIsNotNone(soft)
        with (
            patch.object(wcs, "docker_available", return_value=True),
            patch.object(wcs, "inspect_weather_cache_bounds", return_value=(start, end)),
            patch.object(wcs, "open_meteo_json_age_min", return_value=150.0),
        ):
            hard = wcs.weather_cache_stale_reason(self.share, 32, force=False)
            self.assertIsNotNone(hard)
            self.assertIn("limit 150", hard)
            self.assertNotIn("refresh at", hard)

    def test_validate_rejects_soft_not_greater_than_step(self):
        (self.share / "config.json").write_text(
            json.dumps(
                {
                    "open_meteo_cache_max_age": 90,
                    "optimization_time_step": 30,
                }
            ),
            encoding="utf-8",
        )
        err = wcs.validate_cache_max_age_config(self.share)
        self.assertIsNotNone(err)
        self.assertIn("refresh", err.lower())


if __name__ == "__main__":
    unittest.main()
