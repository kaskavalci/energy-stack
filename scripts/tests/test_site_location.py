"""Regression tests for EMHASS site location configuration."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
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
        self.assertIn(
            "docker compose --env-file config/wud.env up -d --force-recreate emhass",
            self.deploy,
        )

    def test_deploy_excludes_local_artifacts_and_keeps_runtime_files(self) -> None:
        real_rsync = shutil.which("rsync")
        if real_rsync is None:
            self.skipTest("rsync is required for the deploy-filter regression test")

        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp) / "repo"
            remote_root = Path(tmp) / "remote"
            bin_dir = Path(tmp) / "bin"
            for path in (
                fixture_root / "scripts" / "lib",
                fixture_root / "config",
                remote_root,
                bin_dir,
            ):
                path.mkdir(parents=True, exist_ok=True)

            shutil.copy2(
                REPO_ROOT / "scripts" / "deploy-energy-stack.sh",
                fixture_root / "scripts" / "deploy-energy-stack.sh",
            )
            for name in (
                "source-site-env.sh",
                "validate_site_location.py",
                "validate_wud_env.py",
            ):
                shutil.copy2(
                    REPO_ROOT / "scripts" / "lib" / name,
                    fixture_root / "scripts" / "lib" / name,
                )

            (fixture_root / "config" / "site.env").write_text(
                "LAT=52\n"
                "LON=5\n"
                "ALT=0\n"
                "TIME_ZONE=Europe/Amsterdam\n"
                "TZ=Europe/Amsterdam\n"
                "ENERGY_REMOTE_HOST=test-host\n"
                "ENERGY_REMOTE_DIR=/srv/energy-stack\n",
                encoding="utf-8",
            )
            (fixture_root / "config" / "wud.env").write_text(
                "WUD_TELEGRAM_BOT_TOKEN=synthetic-token\n"
                "WUD_TELEGRAM_CHAT_ID=synthetic-chat\n"
                "TZ=Europe/Amsterdam\n",
                encoding="utf-8",
            )

            artifact_dirs = (
                ".worktrees",
                ".superpowers",
                ".rendered",
                ".bug-hunter",
                ".pytest_cache",
            )
            for name in artifact_dirs:
                path = fixture_root / name
                path.mkdir()
                (path / "local-only.txt").write_text(name, encoding="utf-8")

            artifact_files = (
                ".cursorrules",
                "emhass/share/last_enever_run.json",
            )
            for relative in artifact_files:
                path = fixture_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative, encoding="utf-8")

            runtime_files = (
                "docker-compose.yml",
                "emhass-cron/fetch_prices_emhass.py",
                "emhass/share/config.json.example",
                "monitor/health_notify.py",
            )
            for relative in runtime_files:
                path = fixture_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative, encoding="utf-8")

            fake_rsync = bin_dir / "rsync"
            fake_rsync.write_text(
                "#!/usr/bin/env python3\n"
                "import os\n"
                "import subprocess\n"
                "import sys\n"
                "args = sys.argv[1:]\n"
                "args[-1] = os.environ['TEST_RSYNC_DEST']\n"
                "raise SystemExit(subprocess.run("
                "[os.environ['REAL_RSYNC'], *args]).returncode)\n",
                encoding="utf-8",
            )
            fake_rsync.chmod(0o755)
            fake_ssh = bin_dir / "ssh"
            fake_ssh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_ssh.chmod(0o755)

            env = dict(os.environ)
            env.update(
                {
                    "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
                    "REAL_RSYNC": real_rsync,
                    "TEST_RSYNC_DEST": f"{remote_root}/",
                }
            )
            subprocess.run(
                [fixture_root / "scripts" / "deploy-energy-stack.sh"],
                cwd=fixture_root,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

            for name in artifact_dirs:
                with self.subTest(excluded=name):
                    self.assertFalse((remote_root / name).exists())
            for relative in artifact_files:
                with self.subTest(excluded=relative):
                    self.assertFalse((remote_root / relative).exists())
            for relative in runtime_files:
                with self.subTest(included=relative):
                    self.assertEqual(
                        (remote_root / relative).read_text(encoding="utf-8"),
                        relative,
                    )


if __name__ == "__main__":
    unittest.main()
