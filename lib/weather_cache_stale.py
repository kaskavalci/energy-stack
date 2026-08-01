"""Shared Open-Meteo cache stale checks for emhass-cron and energy-monitor."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

WEATHER_PKL = "/data/weather_forecast_data.pkl"
OPEN_METEO_JSON = "/data/cached-open-meteo-forecast-b.json"
DEFAULT_OPEN_METEO_CACHE_MAX_AGE_MIN = 150
DEFAULT_OPTIMIZATION_TIME_STEP_MIN = 30
DEFAULT_PREDICTION_HORIZON = 48


def tzinfo() -> ZoneInfo:
    return ZoneInfo(os.environ.get("TZ", "Europe/Amsterdam"))


def emhass_container() -> str:
    return os.environ.get("EMHASS_CONTAINER", "emhass")


def read_config_json(share_dir: Path) -> dict:
    config_path = share_dir / "config.json"
    if not config_path.is_file():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def read_open_meteo_cache_max_age_min(share_dir: Path) -> int:
    val = read_config_json(share_dir).get("open_meteo_cache_max_age")
    if val is None:
        return DEFAULT_OPEN_METEO_CACHE_MAX_AGE_MIN
    try:
        age = int(val)
    except (TypeError, ValueError):
        return DEFAULT_OPEN_METEO_CACHE_MAX_AGE_MIN
    return age if age > 0 else DEFAULT_OPEN_METEO_CACHE_MAX_AGE_MIN


def read_optimization_time_step_min(share_dir: Path) -> int:
    val = read_config_json(share_dir).get("optimization_time_step")
    if val is None:
        return DEFAULT_OPTIMIZATION_TIME_STEP_MIN
    try:
        step = int(val)
    except (TypeError, ValueError):
        return DEFAULT_OPTIMIZATION_TIME_STEP_MIN
    return step if step > 0 else DEFAULT_OPTIMIZATION_TIME_STEP_MIN


def read_prediction_horizon_steps(share_dir: Path) -> int:
    val = read_config_json(share_dir).get("prediction_horizon")
    if val is None:
        return DEFAULT_PREDICTION_HORIZON
    try:
        horizon = int(val)
    except (TypeError, ValueError):
        return DEFAULT_PREDICTION_HORIZON
    return horizon if horizon > 0 else DEFAULT_PREDICTION_HORIZON


def read_open_meteo_refresh_age_min(share_dir: Path) -> int:
    """Soft clear/refetch age: max_age - 2 * optimization_time_step."""
    max_age = read_open_meteo_cache_max_age_min(share_dir)
    step = read_optimization_time_step_min(share_dir)
    return max_age - (2 * step)


def validate_cache_max_age_config(share_dir: Path) -> str | None:
    """Return error when open_meteo_cache_max_age is not greater than MPC cadence."""
    max_age = read_open_meteo_cache_max_age_min(share_dir)
    step = read_optimization_time_step_min(share_dir)
    if max_age <= step:
        return (
            f"open_meteo_cache_max_age ({max_age} min) must be > "
            f"optimization_time_step ({step} min)"
        )
    refresh_age = max_age - (2 * step)
    if refresh_age <= step:
        return (
            f"open_meteo refresh age ({refresh_age} min = max_age - 2*step) "
            f"must be > optimization_time_step ({step} min)"
        )
    return None


def docker_available() -> bool:
    return Path("/var/run/docker.sock").exists()


def docker_exec_emhass(*args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "exec", emhass_container(), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def parse_iso_dt(value: str, tz: ZoneInfo) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def current_mpc_slot_start(step_min: int, tz: ZoneInfo) -> datetime:
    now = datetime.now(tz).replace(second=0, microsecond=0)
    slot = (now.minute // step_min) * step_min
    return now.replace(minute=slot)


def inspect_weather_cache_bounds() -> tuple[datetime | None, datetime | None]:
    if not docker_available():
        return None, None
    script = (
        "import pickle\n"
        "from pathlib import Path\n"
        "p = Path('/data/weather_forecast_data.pkl')\n"
        "if not p.is_file():\n"
        "    print('MISSING')\n"
        "else:\n"
        "    d = pickle.loads(p.read_bytes())\n"
        "    print('OK', d.index.min().isoformat(), d.index.max().isoformat())\n"
    )
    result = docker_exec_emhass("python3", "-c", script, timeout=30)
    if result.returncode != 0:
        return None, None
    line = (result.stdout or "").strip().splitlines()[-1] if result.stdout else ""
    if line == "MISSING" or not line.startswith("OK "):
        return None, None
    parts = line.split()
    tz = tzinfo()
    try:
        return parse_iso_dt(parts[1], tz), parse_iso_dt(parts[2], tz)
    except (IndexError, ValueError):
        return None, None


def open_meteo_json_age_min() -> float | None:
    if not docker_available():
        return None
    result = docker_exec_emhass("stat", "-c", "%Y", OPEN_METEO_JSON, timeout=10)
    if result.returncode != 0:
        return None
    try:
        mtime = float((result.stdout or "").strip())
    except ValueError:
        return None
    return (datetime.now().timestamp() - mtime) / 60.0


def _weather_cache_bounds_reason(
    share_dir: Path,
    prediction_horizon: int,
) -> tuple[str | None, datetime | None]:
    """Return (bounds error, cache_end). cache_end is None when pkl is missing."""
    tz = tzinfo()
    step_min = read_optimization_time_step_min(share_dir)
    if config_err := validate_cache_max_age_config(share_dir):
        logging.error("Weather cache config invalid: %s", config_err)
    need_until = datetime.now(tz) + timedelta(minutes=prediction_horizon * step_min)
    cache_start, cache_end = inspect_weather_cache_bounds()
    if cache_end is None:
        return "weather_forecast_data.pkl missing or unreadable", None
    if cache_end < need_until:
        return (
            f"cache ends {cache_end.isoformat()} before MPC window "
            f"{need_until.isoformat()}"
        ), cache_end
    need_from = current_mpc_slot_start(step_min, tz) - timedelta(minutes=step_min)
    if cache_start and cache_start < need_from:
        return (
            f"cache starts {cache_start.isoformat()} before previous MPC slot "
            f"{need_from.isoformat()}"
        ), cache_end
    return None, cache_end


def weather_cache_refresh_reason(
    share_dir: Path,
    prediction_horizon: int,
    *,
    force: bool = False,
) -> str | None:
    if force:
        return "forced refresh"
    if not docker_available():
        logging.warning("Docker socket unavailable; cannot verify Open-Meteo cache files")
        return None
    bounds_reason, cache_end = _weather_cache_bounds_reason(share_dir, prediction_horizon)
    if bounds_reason:
        return bounds_reason
    refresh_age = read_open_meteo_refresh_age_min(share_dir)
    max_age = read_open_meteo_cache_max_age_min(share_dir)
    json_age = open_meteo_json_age_min()
    if json_age is not None and json_age >= refresh_age:
        return (
            f"Open-Meteo JSON is {json_age:.0f} min old "
            f"(refresh at {refresh_age} min; hard limit {max_age} min)"
        )
    if cache_end and cache_end < datetime.now(tzinfo()):
        return f"cache ends in the past ({cache_end.isoformat()})"
    return None


def weather_cache_stale_reason(
    share_dir: Path,
    prediction_horizon: int,
    *,
    force: bool = False,
) -> str | None:
    if force:
        return "forced refresh"
    if not docker_available():
        logging.warning("Docker socket unavailable; cannot verify Open-Meteo cache files")
        return None
    bounds_reason, cache_end = _weather_cache_bounds_reason(share_dir, prediction_horizon)
    if bounds_reason:
        return bounds_reason
    max_age = read_open_meteo_cache_max_age_min(share_dir)
    json_age = open_meteo_json_age_min()
    # >= avoids pass-then-fail race when age crosses max_age between checks.
    if json_age is not None and json_age >= max_age:
        return f"Open-Meteo JSON is {json_age:.0f} min old (limit {max_age} min)"
    if cache_end and cache_end < datetime.now(tzinfo()):
        return f"cache ends in the past ({cache_end.isoformat()})"
    return None


def clear_weather_cache_files() -> None:
    if not docker_available():
        logging.warning("Skip cache clear: docker socket unavailable")
        return
    result = docker_exec_emhass("rm", "-f", OPEN_METEO_JSON, WEATHER_PKL, timeout=15)
    if result.returncode != 0:
        err = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Failed to clear Open-Meteo cache in {emhass_container()}: {err}")
