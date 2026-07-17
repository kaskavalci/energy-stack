#!/usr/bin/env python3
"""Validate site-specific coordinates before deploying EMHASS."""

from __future__ import annotations

import math
import os
import sys
from collections.abc import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


BOUNDS = {
    "LAT": (-90.0, 90.0),
    "LON": (-180.0, 180.0),
    "ALT": (-500.0, 10000.0),
}


def validate_site_location(env: Mapping[str, str]) -> list[str]:
    errors: list[str] = []
    for name, (lower, upper) in BOUNDS.items():
        raw = env.get(name, "").strip()
        if not raw:
            errors.append(f"{name} is required")
            continue
        try:
            value = float(raw)
        except ValueError:
            errors.append(f"{name} must be numeric")
            continue
        if not math.isfinite(value):
            errors.append(f"{name} must be finite")
        elif not lower <= value <= upper:
            errors.append(f"{name} must be between {lower:g} and {upper:g}")

    timezone = env.get("TIME_ZONE", "").strip()
    if not timezone:
        errors.append("TIME_ZONE is required")
    else:
        try:
            ZoneInfo(timezone)
        except (ValueError, ZoneInfoNotFoundError):
            errors.append("TIME_ZONE must name an installed IANA timezone")

    tz = env.get("TZ", "").strip()
    if not tz:
        errors.append("TZ is required")
    elif timezone and tz != timezone:
        errors.append("TZ must equal TIME_ZONE")
    return errors


def main() -> int:
    errors = validate_site_location(os.environ)
    for error in errors:
        print(f"Invalid EMHASS site location: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
