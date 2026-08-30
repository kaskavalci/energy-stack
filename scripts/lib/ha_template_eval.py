"""Evaluate Jinja via Home Assistant /api/template (real HA template engine)."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class HAUnavailable(Exception):
    """Credentials missing/invalid or HA /api/template unreachable."""


def load_ha_credentials() -> tuple[str, str]:
    path = REPO_ROOT / "emhass" / "secrets_emhass.yaml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HAUnavailable(f"cannot read {path}: {exc}") from exc
    url_m = re.search(r"(?m)^hass_url:\s*(.+)$", text)
    tok_m = re.search(r"(?m)^long_lived_token:\s*(.+)$", text)
    if not url_m or not tok_m:
        raise HAUnavailable("hass_url or long_lived_token missing in secrets_emhass.yaml")
    url = url_m.group(1).strip().strip("\"'")
    token = tok_m.group(1).strip().strip("\"'")
    if not url or not token:
        raise HAUnavailable("hass_url or long_lived_token empty")
    return url.rstrip("/"), token


def eval_ha_template(template: str, *, timeout: float = 30.0) -> str:
    try:
        base, token = load_ha_credentials()
    except HAUnavailable:
        raise
    req = urllib.request.Request(
        f"{base}/api/template",
        data=json.dumps({"template": template}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode().strip()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise HAUnavailable(f"HA /api/template HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise HAUnavailable(f"HA /api/template connection failed: {exc}") from exc
    except TimeoutError as exc:
        raise HAUnavailable(f"HA /api/template timeout: {exc}") from exc


def probe_ha_template() -> None:
    """Raise HAUnavailable if HA template API is not usable."""
    eval_ha_template("{{ 1 + 1 }}")


def require_ha_or_skip(test_case) -> None:
    """unittest helper: skip unless EMHASS_HA_TEMPLATE_TESTS=1 forces hard fail."""
    try:
        probe_ha_template()
    except HAUnavailable as exc:
        if os.environ.get("EMHASS_HA_TEMPLATE_TESTS") == "1":
            raise
        test_case.skipTest(f"HA template API unavailable: {exc}")
