#!/usr/bin/env python3
"""
Energy host health checks (.135). On failure, push notify.notify to Home Assistant (mobile app).

Runs via supercronic in energy-monitor container (docker.sock + /share read-only).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from weather_cache_stale import (
    docker_available,
    read_prediction_horizon_steps,
    validate_cache_max_age_config,
    weather_cache_stale_reason,
)

DEFAULT_SECRETS = Path("/secrets/secrets_emhass.yaml")
DEFAULT_SHARE = Path(os.environ.get("EMAHASS_SHARE", "/share"))
DEFAULT_EMAHASS_BASE = os.environ.get("EMAHASS_BASE_URL", "http://emhass:5000").rstrip("/")
DEFAULT_NOTIFY = os.environ.get("HA_NOTIFY_SERVICE", "notify.notify")
DEFAULT_COOLDOWN_SEC = int(os.environ.get("ALERT_COOLDOWN_SEC", "7200"))
DEFAULT_FAILURE_THRESHOLD = int(os.environ.get("ALERT_FAILURE_THRESHOLD", "2"))
TZ = ZoneInfo(os.environ.get("TZ", "Europe/Amsterdam"))

B5_SUSTAINED_IDS = frozenset({"B5_cache", "B5_zero_fill"})

CONTAINERS = ("emhass", "emhass-cron")
EMHASS_CONTAINER = os.environ.get("EMHASS_CONTAINER", "emhass")
PRICE_STALE_H = 26
RUN_STALE_MPC_MIN = 40
RUN_STALE_PRICES_H = 26  # price CSV refresh (per-MPC via HA)
MPC_WEATHER_KEYWORDS = (
    "weather",
    "open-meteo",
    "open_meteo",
    "forecast-cache",
    "pv",
)


def _parse_yaml_value(text: str, key: str) -> str | None:
    m = re.search(rf"^{re.escape(key)}:\s*['\"]?(.*?)['\"]?\s*$", text, re.M)
    return m.group(1).strip() if m else None


def load_ha_config(secrets_path: Path) -> tuple[str, str]:
    text = secrets_path.read_text(encoding="utf-8")
    url = _parse_yaml_value(text, "hass_url") or ""
    token = _parse_yaml_value(text, "long_lived_token") or ""
    if not url or not token:
        raise ValueError(f"Missing hass_url or long_lived_token in {secrets_path}")
    if not url.endswith("/"):
        url += "/"
    return url, token


def notify_ha(
    base_url: str,
    token: str,
    service: str,
    title: str,
    message: str,
) -> None:
    domain, _, name = service.partition(".")
    url = f"{base_url}api/services/{domain}/{name}"
    body = json.dumps({"title": title, "message": message}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def _dedup_path(share: Path) -> Path:
    return share / "monitor_alert_state.json"


@dataclass
class AlertState:
    last_notify: float = 0.0
    consecutive_failures: int = 0


def _parse_alert_entry(val: object) -> AlertState:
    if isinstance(val, (int, float)):
        return AlertState(last_notify=float(val))
    if isinstance(val, dict):
        return AlertState(
            last_notify=float(val.get("last_notify", 0) or 0),
            consecutive_failures=int(val.get("consecutive_failures", 0) or 0),
        )
    return AlertState()


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def load_alert_state(share: Path) -> dict[str, AlertState]:
    path = _dedup_path(share)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): _parse_alert_entry(v) for k, v in raw.items()}


def persist_alert_state(share: Path, state: dict[str, AlertState]) -> None:
    raw: dict[str, object] = {}
    for key, entry in state.items():
        if key in B5_SUSTAINED_IDS:
            raw[key] = {
                "last_notify": entry.last_notify,
                "consecutive_failures": entry.consecutive_failures,
            }
        elif entry.last_notify:
            raw[key] = entry.last_notify
    _atomic_write_text(_dedup_path(share), json.dumps(raw, indent=2))


def record_b5_outcomes(state: dict[str, AlertState], failing_ids: set[str]) -> None:
    for alert_id in B5_SUSTAINED_IDS:
        entry = state.setdefault(alert_id, AlertState())
        if alert_id in failing_ids:
            entry.consecutive_failures += 1
        else:
            entry.consecutive_failures = 0


def can_notify(
    entry: AlertState,
    alert_id: str,
    *,
    cooldown_sec: int,
    failure_threshold: int,
    now: float,
) -> tuple[bool, str | None]:
    if alert_id in B5_SUSTAINED_IDS and entry.consecutive_failures < failure_threshold:
        return (
            False,
            f"consecutive_failures={entry.consecutive_failures}/{failure_threshold}",
        )
    if entry.last_notify and now - entry.last_notify < cooldown_sec:
        return False, "cooldown"
    return True, None


def check_emhass_http(base: str) -> str | None:
    try:
        urllib.request.urlopen(f"{base}/", timeout=8)
        return None
    except Exception as exc:
        return f"EMHASS not reachable at {base}: {exc}"


def _docker_available() -> bool:
    return Path("/var/run/docker.sock").exists()


def _docker_exec_emhass(*args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "exec", EMHASS_CONTAINER, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _block_is_weather_failure(block: dict) -> bool:
    if block.get("failure_kind") == "weather":
        return True
    err = str(block.get("error", "")).lower()
    return any(k in err for k in MPC_WEATHER_KEYWORDS)


def check_weather_cache_stale(share: Path) -> str | None:
    """B5 — same stale rules as emhass-cron weather_cache_stale_reason."""
    if not docker_available():
        return None
    horizon = read_prediction_horizon_steps(share)
    stale = weather_cache_stale_reason(share, horizon, force=False)
    if stale is None:
        return None
    if "missing or unreadable" in stale:
        return "weather_forecast_data.pkl missing in emhass (PV forecast unavailable)"
    return stale


def _runs_ok_within(share: Path, keys: tuple[str, ...], max_age_min: float) -> bool:
    path = share / "last_enever_run.json"
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    now = datetime.now(TZ)
    for key in keys:
        block = data.get(key)
        if not isinstance(block, dict) or block.get("ok") is not True:
            return False
        at_s = block.get("at")
        if not at_s:
            return False
        try:
            at = datetime.fromisoformat(str(at_s).replace("Z", "+00:00"))
            if at.tzinfo is None:
                at = at.replace(tzinfo=TZ)
        except ValueError:
            return False
        age_min = (now - at.astimezone(TZ)).total_seconds() / 60
        if age_min > max_age_min:
            return False
    return True


def _latest_mpc_pv_peak_w() -> float | None:
    """Max P_PV (W) in emhass opt_res_latest.csv — None if unreadable."""
    if not _docker_available():
        return None
    script = (
        "import pandas as pd\n"
        "df = pd.read_csv('/data/opt_res_latest.csv', index_col=0, parse_dates=True)\n"
        "print(float(df['P_PV'].max()) if 'P_PV' in df.columns and len(df) else 0)\n"
    )
    result = _docker_exec_emhass("python3", "-c", script, timeout=30)
    if result.returncode != 0:
        return None
    try:
        return float((result.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def check_emhass_zero_filled_pv_logs(share: Path) -> str | None:
    """B5 — EMHASS zero-filled weather only when MPC/weather runs failed or PV plan is flat."""
    if not _docker_available():
        return None
    if _runs_ok_within(share, ("mpc", "weather"), max_age_min=RUN_STALE_MPC_MIN):
        return None
    result = subprocess.run(
        [
            "docker",
            "logs",
            EMHASS_CONTAINER,
            "--since",
            "45m",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        return None
    blob = f"{result.stdout}\n{result.stderr}".lower()
    if "zero-filled" not in blob or "cache does not fully cover" not in blob:
        return None
    # EMHASS may log zero-fill for partial gaps while the LP still gets usable PV.
    if check_weather_cache_stale(share) is None:
        peak_pv = _latest_mpc_pv_peak_w()
        if peak_pv is not None and peak_pv >= 200:
            return None
    return (
        "EMHASS logged zero-filled Open-Meteo data and the latest MPC plan has "
        "low/no PV (check weather cache and p_pv_forecast)"
    )


def check_weather_failure_in_last_mpc(share: Path) -> str | None:
    """B4 — alert when last MPC failed due to weather/Open-Meteo (no extra API call)."""
    path = share / "last_enever_run.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    mpc = data.get("mpc")
    if not isinstance(mpc, dict) or mpc.get("ok") is not False:
        return None
    if not _block_is_weather_failure(mpc):
        return None
    return f"Last MPC failed (weather/PV): {mpc.get('error', '')[:200]}"


def check_docker_container(name: str) -> str | None:
    try:
        out = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except FileNotFoundError:
        return "docker CLI not available in monitor container"
    if out.returncode != 0 or out.stdout.strip().lower() != "true":
        return f"Container {name} is not running"
    return None


def check_prices_fetched(share: Path) -> str | None:
    path = share / "prices_fetched_at.txt"
    if not path.is_file():
        return "prices_fetched_at.txt missing (HA price fetch never succeeded?)"
    text = path.read_text(encoding="utf-8").strip()
    try:
        # ISO-ish timestamp written by fetch script
        fetched = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=TZ)
    except ValueError:
        return f"Invalid prices_fetched_at.txt: {text[:80]}"
    age_h = (datetime.now(TZ) - fetched.astimezone(TZ)).total_seconds() / 3600
    if age_h > PRICE_STALE_H:
        return f"Price CSV stale ({age_h:.1f}h old, limit {PRICE_STALE_H}h)"
    return None


def check_last_run(share: Path) -> list[tuple[str, str]]:
    """Return list of (alert_id, message) from last_enever_run.json."""
    path = share / "last_enever_run.json"
    if not path.is_file():
        return [("B2", "No last_enever_run.json — emhass-cron has not recorded a run yet")]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return [("B2", "Corrupt last_enever_run.json")]
    problems: list[tuple[str, str]] = []
    for key in ("prices", "weather", "dayahead", "mpc", "soc_refresh"):
        block = data.get(key)
        if not isinstance(block, dict):
            continue
        if block.get("ok") is False:
            problems.append(
                (
                    f"B2_{key}",
                    f"Last {key} failed at {block.get('at')}: {block.get('error', 'unknown')}",
                )
            )
            continue
        at_s = block.get("at")
        if not at_s:
            continue
        try:
            at = datetime.fromisoformat(str(at_s).replace("Z", "+00:00"))
            if at.tzinfo is None:
                at = at.replace(tzinfo=TZ)
        except ValueError:
            continue
        age_min = (datetime.now(TZ) - at.astimezone(TZ)).total_seconds() / 60
        if key == "mpc" and age_min > RUN_STALE_MPC_MIN:
            problems.append(
                (
                    "B3",
                    f"Last MPC success {age_min:.0f} min ago (expected every 30 min)",
                )
            )
        # Price freshness: `prices` key from MPC / --write-only. Do not stale-check
        # legacy `dayahead` — MPC replaced dayahead-optim; old timestamps linger in JSON.
        if key == "prices" and age_min / 60 > RUN_STALE_PRICES_H:
            problems.append(
                (
                    "B2_prices_stale",
                    f"Last price CSV fetch success {age_min/60:.1f}h ago",
                )
            )
    return problems


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    secrets = Path(os.environ.get("EMAHASS_SECRETS", DEFAULT_SECRETS))
    share = Path(os.environ.get("EMAHASS_SHARE", DEFAULT_SHARE))
    notify_svc = os.environ.get("HA_NOTIFY_SERVICE", DEFAULT_NOTIFY)
    cooldown = int(os.environ.get("ALERT_COOLDOWN_SEC", str(DEFAULT_COOLDOWN_SEC)))
    failure_threshold = int(
        os.environ.get("ALERT_FAILURE_THRESHOLD", str(DEFAULT_FAILURE_THRESHOLD))
    )

    try:
        base_url, token = load_ha_config(secrets)
    except ValueError as exc:
        logging.error("%s", exc)
        return 1

    if config_err := validate_cache_max_age_config(share):
        logging.error("Invalid EMHASS weather cache config: %s", config_err)

    failures: list[tuple[str, str, str]] = []

    if msg := check_emhass_http(DEFAULT_EMAHASS_BASE):
        failures.append(("A6", "EMHASS down", msg))
    if msg := check_weather_failure_in_last_mpc(share):
        failures.append(("B4", "Open-Meteo / PV", msg))
    if msg := check_weather_cache_stale(share):
        failures.append(("B5_cache", "Open-Meteo cache", msg))
    if msg := check_emhass_zero_filled_pv_logs(share):
        failures.append(("B5_zero_fill", "Open-Meteo zero-fill", msg))

    for c in CONTAINERS:
        if msg := check_docker_container(c):
            failures.append(("B1", f"Docker: {c}", msg))

    if msg := check_prices_fetched(share):
        failures.append(("A4", "Stale prices", msg))

    for alert_id, msg in check_last_run(share):
        title = "EMHASS cron / MPC"
        failures.append((alert_id, title, msg))

    # One weather root cause → one push (B4); avoid B2_mpc + B2_weather duplicates.
    failing_ids_pre = {alert_id for alert_id, _, _ in failures}
    if "B4" in failing_ids_pre:
        failures = [
            (aid, title, msg)
            for aid, title, msg in failures
            if aid not in ("B2_mpc", "B2_weather")
        ]

    failing_ids = {alert_id for alert_id, _, _ in failures}
    alert_state = load_alert_state(share)
    record_b5_outcomes(alert_state, failing_ids)

    now = time.time()
    sent = 0
    for alert_id, title, message in failures:
        entry = alert_state.setdefault(alert_id, AlertState())
        ok, skip_reason = can_notify(
            entry,
            alert_id,
            cooldown_sec=cooldown,
            failure_threshold=failure_threshold,
            now=now,
        )
        if not ok:
            if skip_reason == "cooldown":
                logging.info("Skip duplicate alert %s (cooldown)", alert_id)
            else:
                logging.info("Skip alert %s: %s", alert_id, skip_reason)
            continue
        full_title = f"Energy: {title}"
        try:
            notify_ha(base_url, token, notify_svc, full_title, message)
            entry.last_notify = now
            logging.info("Notified HA: %s — %s", alert_id, message[:120])
            sent += 1
        except urllib.error.HTTPError as exc:
            logging.error("HA notify failed %s: %s %s", alert_id, exc.code, exc.read()[:200])
        except Exception as exc:
            logging.error("HA notify failed %s: %s", alert_id, exc)

    persist_alert_state(share, alert_state)

    if not failures:
        logging.info("All energy-host checks OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
