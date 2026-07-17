"""Regression tests for EMHASS site location configuration."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from validate_site_location import validate_site_location  # noqa: E402


VALID_ENV = {
    "LAT": "52.0",
    "LON": "5.0",
    "ALT": "0",
    "TIME_ZONE": "Europe/Amsterdam",
    "TZ": "Europe/Amsterdam",
}


class SiteLocationValidationTests(unittest.TestCase):
    def test_valid_location_has_no_errors(self) -> None:
        self.assertEqual(validate_site_location(VALID_ENV), [])

    def test_missing_values_are_rejected(self) -> None:
        env = dict(VALID_ENV)
        del env["LAT"]
        self.assertIn("LAT is required", validate_site_location(env))

    def test_example_placeholders_are_rejected(self) -> None:
        env = dict(VALID_ENV, LON="CHANGE_ME")
        self.assertIn("LON must be numeric", validate_site_location(env))

    def test_non_finite_values_are_rejected(self) -> None:
        env = dict(VALID_ENV, ALT="nan")
        self.assertIn("ALT must be finite", validate_site_location(env))

    def test_out_of_range_values_are_rejected(self) -> None:
        env = dict(VALID_ENV, LAT="91", LON="-181", ALT="10001")
        errors = validate_site_location(env)
        self.assertIn("LAT must be between -90 and 90", errors)
        self.assertIn("LON must be between -180 and 180", errors)
        self.assertIn("ALT must be between -500 and 10000", errors)

    def test_unknown_timezone_is_rejected(self) -> None:
        env = dict(VALID_ENV, TIME_ZONE="Europe/Not_A_Zone", TZ="Europe/Not_A_Zone")
        self.assertIn(
            "TIME_ZONE must name an installed IANA timezone",
            validate_site_location(env),
        )

    def test_malformed_timezone_keys_are_rejected(self) -> None:
        for timezone in ("/UTC", "../UTC"):
            with self.subTest(timezone=timezone):
                env = dict(VALID_ENV, TIME_ZONE=timezone, TZ=timezone)
                self.assertIn(
                    "TIME_ZONE must name an installed IANA timezone",
                    validate_site_location(env),
                )

    def test_container_timezones_must_match(self) -> None:
        env = dict(VALID_ENV, TZ="UTC")
        self.assertIn("TZ must equal TIME_ZONE", validate_site_location(env))


def service_block(text: str, service: str, next_service: str) -> str:
    return text.split(f"  {service}:\n", 1)[1].split(f"  {next_service}:\n", 1)[0]


class SiteLocationWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.deploy = (
            REPO_ROOT / "scripts" / "deploy-energy-stack.sh"
        ).read_text(encoding="utf-8")

    def test_emhass_loads_site_env(self) -> None:
        emhass = service_block(self.compose, "emhass", "emhass-cron")
        self.assertIn("    env_file:\n      - config/site.env\n", emhass)

    def test_compose_has_no_hard_coded_amsterdam_timezone(self) -> None:
        self.assertNotIn("- TZ=Europe/Amsterdam", self.compose)

    def test_deploy_validates_before_rsync(self) -> None:
        validator = self.deploy.index("validate_site_location.py")
        rsync = self.deploy.index("rsync -avz")
        self.assertLess(validator, rsync)

    def test_deploy_force_recreates_emhass(self) -> None:
        self.assertIn("docker compose up -d --force-recreate emhass", self.deploy)


if __name__ == "__main__":
    unittest.main()
