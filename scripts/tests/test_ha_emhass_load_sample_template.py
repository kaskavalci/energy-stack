"""Live HA /api/template test for sensor.emhass_house_load_sample_w logic."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import unittest
from pathlib import Path

from ruamel.yaml import YAML

from ha_template_eval import eval_ha_template, require_ha_or_skip

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "home-assistant" / "ha_emhass_sensors.yaml"
SITE_ENV_HELPER = REPO_ROOT / "scripts" / "lib" / "source-site-env.sh"
SITE_ENTITY_KEYS = (
    "ENVOY_PV_ENTITY",
    "SIGEN_PLANT_SLUG",
    "P1_POWER_NET_ENTITY",
)


def load_site_entities() -> dict[str, str]:
    """Load entity settings through the repository's site-env helper."""
    shell_keys = " ".join(f'"${{{key}}}"' for key in SITE_ENTITY_KEYS)
    command = (
        f"source {shlex.quote(str(SITE_ENV_HELPER))}; "
        f"printf '%s\\n' {shell_keys}"
    )
    completed = subprocess.run(
        ["bash", "-c", command],
        check=True,
        capture_output=True,
        text=True,
    )
    values = completed.stdout.splitlines()
    if len(values) != len(SITE_ENTITY_KEYS) or any(not value for value in values):
        raise RuntimeError(
            "site.env must define ENVOY_PV_ENTITY, SIGEN_PLANT_SLUG, "
            "and P1_POWER_NET_ENTITY"
        )
    return dict(zip(SITE_ENTITY_KEYS, values))


def build_variable_assignment(
    name: str,
    value: str,
    replacements: dict[str, str],
) -> str:
    """Turn one HA variable template into a native Jinja assignment."""
    if not isinstance(value, str):
        raise TypeError(f"{name} variable template must be a string")
    rendered = value.strip()
    for placeholder, replacement in replacements.items():
        rendered = rendered.replace(placeholder, replacement)

    def assign_output(match: re.Match[str]) -> str:
        return f"{{% set {name} = {match.group(1).strip()} %}}"

    if "{{" in rendered:
        rendered = re.sub(r"{{(.*?)}}", assign_output, rendered, flags=re.DOTALL)
    if "{{" in rendered or "}}" in rendered:
        raise ValueError(f"could not convert {name} variable template")
    if "{%" in rendered:
        return rendered
    return f"{{% set {name} = {json.dumps(rendered)} %}}"


class EmhassLoadSampleTemplateEvalTests(unittest.TestCase):
    def setUp(self) -> None:
        require_ha_or_skip(self)
        with PACKAGE.open(encoding="utf-8") as stream:
            doc = YAML(typ="safe").load(stream)
        trigger_block = next(
            item
            for item in doc.get("template", [])
            if isinstance(item, dict)
            and item.get("sensor", [{}])[0].get("default_entity_id")
            == "sensor.emhass_house_load_sample_w"
        )
        self.variables = trigger_block["variables"]
        self.site_entities = load_site_entities()

    def build_api_template(self) -> str:
        """Build one native Jinja template from the package variable block."""
        replacements = {
            "__ENVOY_PV_ENTITY__": self.site_entities["ENVOY_PV_ENTITY"],
            "__SIGEN_PLANT_SLUG__": self.site_entities["SIGEN_PLANT_SLUG"],
            "__P1_POWER_NET_ENTITY__": self.site_entities["P1_POWER_NET_ENTITY"],
        }
        assignments = [
            build_variable_assignment(name, value, replacements)
            for name, value in self.variables.items()
            if name != "quality_and_val"
        ]
        assignments.append(
            """{{ {
  "p1_age": p1_age,
  "batt_age": batt_age,
  "sources_stale": sources_stale,
  "source_spread": source_spread,
  "load_quality": state_attr('sensor.emhass_house_load_sample_w', 'load_quality')
} | tojson }}"""
        )
        return "\n".join(assignments)

    def test_live_template_evaluates_fresh_sources(self) -> None:
        """Render the variable block through Home Assistant and verify sources are not stale."""
        template_str = self.build_api_template()
        result_raw = eval_ha_template(template_str)
        result = json.loads(result_raw)

        self.assertFalse(
            result["sources_stale"],
            f"sources_stale evaluated to True: {result}",
        )
        for age_name in ("p1_age", "batt_age"):
            age = result[age_name]
            self.assertIn(
                type(age),
                (int, float),
                f"{age_name} is not numeric: {result}",
            )
            self.assertGreater(age, 0.0, f"{age_name} is not positive: {age}")
            self.assertLess(age, 60.0, f"{age_name} is >= 60s: {age}")

        self.assertIn(
            result["load_quality"],
            ("valid", "valid_noise_clamp", "held_estimate"),
            f"load quality is not valid: {result}",
        )
