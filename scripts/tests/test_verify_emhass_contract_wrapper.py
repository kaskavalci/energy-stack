from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "scripts" / "verify-emhass-load-gap-contract.sh"


class VerifyEmhassContractWrapperTests(unittest.TestCase):
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
        self.assertRegex(
            text,
            r"must use repository:release@sha256:digest|Candidate must use",
        )


if __name__ == "__main__":
    unittest.main()
