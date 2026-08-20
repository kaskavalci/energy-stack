from __future__ import annotations

import unittest
from pathlib import Path

from ruamel.yaml import YAML


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker-compose.yml"

EXPECTED = {
    "emhass": "ghcr.io/davidusb-geek/emhass:v0.18.1",
    "sigenergy2mqtt": "ghcr.io/seud0nym/sigenergy2mqtt:2026.8.8",
    "mosquitto": "eclipse-mosquitto:2.1.2-alpine",
    "mqtt4dsmr": "ghcr.io/antonijn/mqtt4dsmr:v0.9.7",
}


class ComposeImagePinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with COMPOSE.open(encoding="utf-8") as stream:
            cls.compose = YAML(typ="safe").load(stream)

    def test_four_services_use_fixed_versions(self) -> None:
        for service, expected in EXPECTED.items():
            with self.subTest(service=service):
                image = self.compose["services"][service]["image"]
                self.assertEqual(image, expected)
                self.assertNotIn(":latest", image)
                self.assertNotIn("@sha256:", image)
                self.assertNotIn("Floating :latest", image)

    def test_floating_latest_comments_removed(self) -> None:
        text = COMPOSE.read_text(encoding="utf-8")
        self.assertNotIn("Floating :latest is intentional", text)


if __name__ == "__main__":
    unittest.main()
