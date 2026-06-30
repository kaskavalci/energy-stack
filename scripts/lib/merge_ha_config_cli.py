#!/usr/bin/env python3
"""CLI: merge owned keys into Home Assistant configuration.yaml."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ha_config_merge import (
    ENERGY_STACK_MANIFEST,
    MONOREPO_MANIFEST,
    RETERMINAL_MANIFEST,
    merge_configuration_file,
)

MANIFESTS = {
    "reterminal": RETERMINAL_MANIFEST,
    "energy-stack": ENERGY_STACK_MANIFEST,
    "monorepo": MONOREPO_MANIFEST,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        choices=sorted(MANIFESTS),
        required=True,
        help="Which repo manifest to apply",
    )
    parser.add_argument("--live", type=Path, required=True, help="Live configuration.yaml")
    parser.add_argument("--out", type=Path, required=True, help="Merged output path")
    args = parser.parse_args()
    merge_configuration_file(args.live, MANIFESTS[args.manifest], args.out)
    print(f"merged configuration.yaml ({args.manifest})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
