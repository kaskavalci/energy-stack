#!/usr/bin/env python3
"""Validate the WUD environment file without exposing secret values."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


KEY_PATTERN = re.compile(r"[A-Z_][A-Z0-9_]*\Z")
TELEGRAM_FIELDS = ("WUD_TELEGRAM_BOT_TOKEN", "WUD_TELEGRAM_CHAT_ID")


def load_wud_env(path: Path) -> dict[str, str]:
    """Load a strict KEY=VALUE environment file."""
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"line {line_number}: expected KEY=VALUE")

        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        if not KEY_PATTERN.fullmatch(key):
            raise ValueError(f"line {line_number}: invalid key")
        if key in values:
            raise ValueError(f"line {line_number}: duplicate key: {key}")
        values[key] = value.strip()
    return values


def validate_wud_env(values: Mapping[str, str]) -> list[str]:
    """Return validation errors without including secret values."""
    errors: list[str] = []
    for name in TELEGRAM_FIELDS:
        value = values.get(name, "").strip()
        if not value:
            errors.append(f"{name} is required")
        elif any(character.isspace() for character in value):
            errors.append(f"{name} must not contain whitespace")

    timezone = values.get("TZ", "").strip()
    if not timezone:
        errors.append("TZ is required")
    else:
        try:
            ZoneInfo(timezone)
        except (ValueError, ZoneInfoNotFoundError):
            errors.append("TZ must name an installed IANA timezone")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a WUD environment file.")
    parser.add_argument("path", type=Path, nargs="?", default=Path("config/wud.env"))
    arguments = parser.parse_args()

    try:
        values = load_wud_env(arguments.path)
    except FileNotFoundError:
        print("WUD environment file is missing", file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError) as error:
        print(f"WUD environment file cannot be parsed: {error}", file=sys.stderr)
        return 2

    errors = validate_wud_env(values)
    for error in errors:
        print(f"Invalid WUD environment: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
