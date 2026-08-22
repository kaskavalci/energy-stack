"""Regression tests for the dual-host WUD Compose contracts."""

from __future__ import annotations

import unittest
from pathlib import Path

from ruamel.yaml import YAML


REPO_ROOT = Path(__file__).resolve().parents[2]
WUD_IMAGE = (
    "docker.io/getwud/wud:8.3.0@"
    "sha256:1cf5ecfdbd2389847427fd9685c4fd337ee8c74a0f584c52288d760d9a58811e"
)


def load_compose(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return YAML(typ="safe").load(stream)


def environment_map(environment: object) -> dict[str, str]:
    if isinstance(environment, dict):
        return environment
    if isinstance(environment, list):
        return dict(item.split("=", 1) for item in environment if "=" in item)
    return {}


class WudComposeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.energy = load_compose(REPO_ROOT / "docker-compose.yml")
        self.ha = load_compose(REPO_ROOT / "home-assistant" / "docker-compose.yml")

    def wud_service(self, compose: dict, label: str) -> dict:
        services = compose.get("services", {})
        self.assertIn("wud", services, f"{label} must define a wud service")
        return services["wud"]

    def assert_wud_base(self, compose: dict, label: str, port: str, store: str) -> dict:
        wud = self.wud_service(compose, label)
        self.assertEqual(wud.get("image"), WUD_IMAGE)
        self.assertEqual(wud.get("ports"), [port])
        volumes = wud.get("volumes", [])
        socket_mounts = [volume for volume in volumes if "/var/run/docker.sock" in volume]
        self.assertEqual(socket_mounts, ["/var/run/docker.sock:/var/run/docker.sock:ro"])
        store_mounts = [volume for volume in volumes if volume.endswith(":/store")]
        self.assertEqual(store_mounts, [f"{store}:/store"])
        self.assertIn(store, compose.get("volumes", {}), f"{label} must define {store}")
        self.assertNotIn("env_file", wud)
        environment = environment_map(wud.get("environment", {}))
        self.assertEqual(environment.get("WUD_WATCHER_LOCAL_SOCKET"), "/var/run/docker.sock")
        self.assertEqual(environment.get("WUD_WATCHER_LOCAL_WATCHALL"), "true")
        self.assertEqual(environment.get("WUD_WATCHER_LOCAL_CRON"), "0 6 * * *")
        return environment

    def assert_telegram(self, environment: dict, role: str, title: str) -> None:
        prefix = f"WUD_TRIGGER_TELEGRAM_{role}_"
        self.assertEqual(environment.get(f"{prefix}MESSAGEFORMAT"), "HTML")
        self.assertEqual(environment.get(f"{prefix}MODE"), "batch")
        self.assertEqual(environment.get(f"{prefix}THRESHOLD"), "all")
        self.assertEqual(environment.get(f"{prefix}ONCE"), "true")
        self.assertEqual(environment.get(f"{prefix}BATCHTITLE"), title)
        self.assertIn("WUD_TELEGRAM_BOT_TOKEN", environment.get(f"{prefix}BOTTOKEN", ""))
        self.assertIn("WUD_TELEGRAM_CHAT_ID", environment.get(f"{prefix}CHATID", ""))
        self.assertFalse(
            any("DOCKER" in key and "TRIGGER" in key for key in environment),
            "WUD must not configure a Docker trigger",
        )
        self.assertFalse(
            any("UPDATE" in key for key in environment),
            "WUD must not configure automatic updates",
        )

    def test_energy_wud_has_pinned_notify_only_configuration(self) -> None:
        environment = self.assert_wud_base(
            self.energy, "energy docker-compose.yml", "3000:3000", "wud-energy-store"
        )
        self.assert_telegram(environment, "ENERGY", "energy-host: $${count} update(s)")

    def test_ha_wud_has_pinned_notify_only_configuration(self) -> None:
        environment = self.assert_wud_base(
            self.ha, "HA docker-compose.yml", "3001:3000", "wud-ha-store"
        )
        self.assert_telegram(environment, "HA", "ha-node: $${count} update(s)")

    def test_local_builds_are_excluded_from_wud(self) -> None:
        for service in ("emhass-cron", "energy-monitor"):
            with self.subTest(service=service):
                labels = self.energy["services"][service].get("labels", {})
                self.assertIsInstance(labels, dict, f"{service} labels must use mapping form")
                self.assertEqual(labels.get("wud.watch"), "false")

    def test_homeassistant_keeps_host_networking(self) -> None:
        self.assertEqual(self.ha["services"]["homeassistant"].get("network_mode"), "host")

    def test_homeassistant_uses_fixed_release_version(self) -> None:
        image = self.ha["services"]["homeassistant"]["image"]
        self.assertEqual(
            image, "ghcr.io/home-assistant/home-assistant:2026.8.2"
        )
        self.assertNotIn(":stable", image)
        self.assertNotIn(":latest", image)
        self.assertNotIn("@sha256:", image)
        labels = self.ha["services"]["homeassistant"].get("labels", {})
        self.assertEqual(labels.get("wud.tag.include"), r"^\d+\.\d+\.\d+$$")


if __name__ == "__main__":
    unittest.main()
