from __future__ import annotations

import re
import unittest
from pathlib import Path

from ruamel.yaml import YAML


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker-compose.yml"
WRAPPER = REPO_ROOT / "scripts" / "verify-emhass-load-gap-contract.sh"
PINNED_IMAGE = (
    "ghcr.io/davidusb-geek/emhass:v0.17.5@sha256:"
    "0aa7c9062ed269c138b6099bb2b6d7428f640d5c00cdbf31b7e0067345779b8e"
)
PIN_PATTERN = re.compile(
    r"^ghcr\.io/davidusb-geek/emhass:(?!latest(?:@|$))[^@\s]+"
    r"@sha256:[0-9a-f]{64}$"
)


class EmhassImagePinTests(unittest.TestCase):
    def test_compose_uses_verified_release_and_digest(self) -> None:
        with COMPOSE.open(encoding="utf-8") as stream:
            compose = YAML(typ="safe").load(stream)
        image = compose["services"]["emhass"]["image"]
        self.assertEqual(image, PINNED_IMAGE)
        self.assertRegex(image, PIN_PATTERN)

    def test_contract_wrapper_defaults_to_compose_image(self) -> None:
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertIn("docker-compose.yml", text)
        self.assertIn(
            'compose["services"]["emhass"]["image"]',
            text,
        )
        self.assertIn("emhass_load_gap_contract.py", text)
        self.assertNotIn("config/site.env", text)
        self.assertNotIn("secrets_emhass", text)


if __name__ == "__main__":
    unittest.main()
