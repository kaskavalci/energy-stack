from __future__ import annotations

import re
import unittest
from pathlib import Path

from ruamel.yaml import YAML


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker-compose.yml"
WRAPPER = REPO_ROOT / "scripts" / "verify-emhass-load-gap-contract.sh"
PINNED_IMAGE = "ghcr.io/davidusb-geek/emhass:v0.18.1"
PIN_PATTERN = re.compile(
    r"^ghcr\.io/davidusb-geek/emhass:(?!latest$)v?[0-9][^@\s]+$"
)


class EmhassImagePinTests(unittest.TestCase):
    def test_compose_uses_fixed_release_version_only(self) -> None:
        with COMPOSE.open(encoding="utf-8") as stream:
            compose = YAML(typ="safe").load(stream)
        image = compose["services"]["emhass"]["image"]
        self.assertEqual(image, PINNED_IMAGE)
        self.assertRegex(image, PIN_PATTERN)
        self.assertNotIn("@sha256:", image)
        self.assertNotIn(":latest", image)

    def test_contract_wrapper_requires_digest_candidate_arg(self) -> None:
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertIn("docker-compose.yml", text)
        self.assertIn(
            'compose["services"]["emhass"]["image"]',
            text,
        )
        self.assertIn("emhass_load_gap_contract.py", text)
        self.assertIn("@sha256:", text)
        self.assertNotIn("config/site.env", text)
        self.assertNotIn("secrets_emhass", text)
        # Wrapper must not treat version-only Compose as a pullable candidate.
        self.assertRegex(
            text,
            r"must use repository:release@sha256:digest|Candidate must use",
        )


if __name__ == "__main__":
    unittest.main()
