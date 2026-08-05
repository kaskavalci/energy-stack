"""Tests for additive HA configuration.yaml merge."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))

from ha_config_merge import (  # noqa: E402
    ENERGY_STACK_MANIFEST,
    MONOREPO_MANIFEST,
    RETERMINAL_MANIFEST,
    assert_manifests_disjoint,
    merge_configuration,
)

FIXTURE = """default_config:

homeassistant:
  packages:
    reterminal_energy: !include ha_helpers.yaml
    third_party_custom: !include custom.yaml

lovelace:
  dashboards:
    other-dashboard:
      mode: yaml
      title: Other
      filename: dashboards/other.yaml

recorder:
  exclude:
    entity_globs:
      - sensor.*_noise
"""


class TestHaConfigMerge(unittest.TestCase):
    def test_manifests_disjoint(self) -> None:
        assert_manifests_disjoint()

    def test_reterminal_preserves_third_party_packages(self) -> None:
        out = merge_configuration(FIXTURE, RETERMINAL_MANIFEST)
        self.assertIn("third_party_custom: !include custom.yaml", out)
        self.assertIn("reterminal_p1_mqtt: !include ha_p1_mqtt_aliases.yaml", out)
        self.assertNotIn("reterminal_emhass:", out)

    def test_energy_stack_preserves_third_party_packages(self) -> None:
        out = merge_configuration(FIXTURE, ENERGY_STACK_MANIFEST)
        self.assertIn("third_party_custom: !include custom.yaml", out)
        self.assertIn("reterminal_emhass: !include ha_emhass.yaml", out)
        self.assertIn("emhass-trading:", out)
        self.assertIn("sigen-diagnostics:", out)
        self.assertIn(
            "reterminal_sigen_diagnostics: !include ha_sigen_diagnostics.yaml",
            out,
        )
        self.assertIn("filename: dashboards/sigen_diagnostics.yaml", out)
        self.assertIn("filename: dashboards/emhass_trading.yaml", out)
        self.assertIn("other-dashboard:", out)
        self.assertIn("input_number.emhass_soc_*", out)
        self.assertIn("sensor.*_noise", out)

    def test_energy_stack_manifest_owns_sigen_diagnostics_files(self) -> None:
        self.assertEqual(
            ENERGY_STACK_MANIFEST.packages["reterminal_sigen_diagnostics"],
            "!include ha_sigen_diagnostics.yaml",
        )
        dash = ENERGY_STACK_MANIFEST.lovelace_dashboards["sigen-diagnostics"]
        self.assertEqual(dash["filename"], "dashboards/sigen_diagnostics.yaml")
        self.assertEqual(dash["title"], "Sigen diagnostics")
        self.assertTrue(dash["show_in_sidebar"])

    def test_independent_deploys_preserve_union(self) -> None:
        after_rt = merge_configuration(FIXTURE, RETERMINAL_MANIFEST)
        after_es = merge_configuration(after_rt, ENERGY_STACK_MANIFEST)
        for key in MONOREPO_MANIFEST.packages:
            self.assertIn(f"{key}:", after_es)
        self.assertIn("third_party_custom:", after_es)
        self.assertIn("other-dashboard:", after_es)
        self.assertIn("emhass-trading:", after_es)
        self.assertIn("sigen-diagnostics:", after_es)
        self.assertIn(
            "reterminal_sigen_diagnostics: !include ha_sigen_diagnostics.yaml",
            after_es,
        )
        self.assertIn("filename: dashboards/sigen_diagnostics.yaml", after_es)
        self.assertIn("filename: dashboards/emhass_trading.yaml", after_es)

    def test_monorepo_manifest_matches_full_deploy(self) -> None:
        out = merge_configuration(FIXTURE, MONOREPO_MANIFEST)
        for key in MONOREPO_MANIFEST.packages:
            self.assertIn(f"{key}:", out)


if __name__ == "__main__":
    unittest.main()
