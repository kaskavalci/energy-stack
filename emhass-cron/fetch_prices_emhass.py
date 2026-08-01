#!/usr/bin/env python3
"""
Zonneplan electricity prices from Home Assistant → EMHASS CSV forecasts + MPC.

Reads sensor.zonneplan_current_electricity_tariff (Zonneplan HACS) via HA REST,
writes share CSVs, runs Policy A MPC (weather cache → naive-mpc-optim → publish).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from weather_cache_stale import (
    clear_weather_cache_files,
    docker_available,
    read_optimization_time_step_min,
    validate_cache_max_age_config,
    weather_cache_refresh_reason,
    weather_cache_stale_reason,
)

SCRIPT_DIR = Path(__file__).resolve().parent


def _default_emhass_base() -> str:
    explicit = os.environ.get("EMAHASS_BASE_URL") or os.environ.get("EMHASS_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    action_url = (
        os.environ.get("EMAHASS_URL")
        or os.environ.get("EMHASS_URL")
        or "http://127.0.0.1:5000/action/dayahead-optim"
    )
    if "/action/" in action_url:
        return action_url.split("/action/", 1)[0].rstrip("/")
    return action_url.rstrip("/")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


DEFAULT_EMAHASS_BASE = _default_emhass_base()
DEFAULT_TZ = "Europe/Amsterdam"
DEFAULT_SECRETS = SCRIPT_DIR.parent / "emhass" / "secrets_emhass.yaml"
DEFAULT_SECRETS_ENV = os.environ.get("EMAHASS_SECRETS") or os.environ.get("EMHASS_SECRETS")
DEFAULT_SOC_SENSOR = os.environ.get("EMHASS_SOC_SENSOR", "sensor.sigen_0_plant_battery_soc")
DEFAULT_PRICE_SENSOR = os.environ.get(
    "ZONNEPLAN_PRICE_SENSOR", "sensor.zonneplan_current_electricity_tariff"
)
DEFAULT_SOC_MIN_SENSOR = os.environ.get("EMHASS_SOC_MIN_SENSOR", "input_number.emhass_soc_min")
DEFAULT_SOC_MAX_SENSOR = os.environ.get("EMHASS_SOC_MAX_SENSOR", "input_number.emhass_soc_max")
DEFAULT_EMAHASS_SHARE = Path(os.environ.get("EMAHASS_SHARE", "/share"))
LOAD_COST_CSV = "data_load_cost_forecast.csv"
PROD_PRICE_CSV = "data_prod_price_forecast.csv"
DEFAULT_EXPORT_PRICE_HAIRCUT = _env_float("EXPORT_PRICE_HAIRCUT_EUR_PER_KWH", 0.04)
DEFAULT_PREDICTION_HORIZON = 32
MIN_PRICE_SLOTS = 12  # 6 h @ 30 min — below this, abort MPC
# Keep in sync with check_weather_failure_in_last_mpc in health_notify.py (B4)
WEATHER_FAILURE_KEYWORDS = (
    "weather",
    "open-meteo",
    "open_meteo",
    "forecast-cache",
    "pv",
)
WEATHER_CACHE_RETRY_BACKOFF_S = (15.0, 45.0)
MPC_SUCCESS_ENTITY_ID = "input_datetime.emhass_last_successful_mpc"
MPC_STATUS_MAX_ATTEMPTS = 3
MPC_STATUS_RETRY_DELAY_S = 2.0


MAX_DEGRADED_LOAD_GAP_BINS = 6
LOAD_HISTORY_MODE_HEALTHY = "healthy"
LOAD_HISTORY_MODE_DEGRADED = "degraded"
LOAD_HISTORY_MODE_UNSAFE = "unsafe"


class LoadHistoryError(RuntimeError):
    """Expected load-history config, retrieval, or validation failure."""

    def __init__(
        self,
        message: str,
        *,
        summary: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.summary = summary


def _parse_simple_yaml_value(text: str, key: str) -> str | None:
    prefix = f"{key}:"
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(prefix):
            return line[len(prefix) :].strip().strip('"').strip("'")
    return None


def floor_to_step(dt: datetime, step_min: int) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    discard = dt.minute % step_min
    return dt - timedelta(minutes=discard)


def parse_forecast_datetime(raw: str, tz: ZoneInfo) -> datetime | None:
    raw = raw.strip()
    try:
        if raw.endswith("Z"):
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return dt.astimezone(tz)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(raw[:19] if len(raw) > 16 else raw, fmt)
            return dt.replace(tzinfo=tz)
        except ValueError:
            continue
    return None


def fetch_ha_state(
    secrets_path: Path,
    entity_id: str,
    timeout: float = 15,
    *,
    credentials: tuple[str, str] | None = None,
) -> dict[str, Any]:
    if credentials is None:
        if not secrets_path.is_file():
            raise RuntimeError(f"Secrets file not found: {secrets_path}")
        text = secrets_path.read_text(encoding="utf-8")
        hass_url = _parse_simple_yaml_value(text, "hass_url")
        token = _parse_simple_yaml_value(text, "long_lived_token")
        if not hass_url or not token:
            raise RuntimeError("hass_url or long_lived_token missing in secrets")
        if not hass_url.endswith("/"):
            hass_url += "/"
    else:
        hass_url, token = credentials
    url = f"{hass_url}api/states/{entity_id}"
    req = Request(url, headers={"Authorization": f"Bearer {token}"})
    with urlopen(req, timeout=timeout) as response:
        state = json.loads(response.read().decode("utf-8"))
    if not isinstance(state, dict):
        raise RuntimeError(
            "Malformed Home Assistant state response: root must be an object"
        )
    if "attributes" in state and not isinstance(state["attributes"], dict):
        raise RuntimeError(
            "Malformed Home Assistant state response: attributes must be an object"
        )
    return state


def read_load_history_credentials(secrets_path: Path) -> tuple[str, str]:
    if not secrets_path.is_file():
        raise LoadHistoryError(f"Secrets file not found: {secrets_path}")
    try:
        text = secrets_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise LoadHistoryError("Could not read Home Assistant secrets") from exc
    hass_url = _parse_simple_yaml_value(text, "hass_url")
    token = _parse_simple_yaml_value(text, "long_lived_token")
    if not hass_url or not token:
        raise LoadHistoryError("hass_url or long_lived_token missing in secrets")
    if not hass_url.endswith("/"):
        hass_url += "/"
    try:
        parsed_url = urlsplit(hass_url)
        _ = parsed_url.port
    except (ValueError, UnicodeError) as exc:
        raise LoadHistoryError(
            "Invalid Home Assistant load-history request configuration"
        ) from exc
    if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
        raise LoadHistoryError(
            "Invalid Home Assistant load-history request configuration"
        )
    try:
        authorization = f"Bearer {token}"
        authorization.encode("latin-1")
        if "\r" in authorization or "\n" in authorization:
            raise ValueError("invalid authorization header")
        Request(
            f"{hass_url}api/history/period/1970-01-01T00%3A00%3A00%2B00%3A00",
            headers={"Authorization": authorization},
        )
    except (ValueError, UnicodeError) as exc:
        raise LoadHistoryError(
            "Invalid Home Assistant load-history request configuration"
        ) from exc
    return hass_url, token


def fetch_load_history_from_ha(
    secrets_path: Path,
    entity_id: str,
    *,
    start: datetime,
    end: datetime,
    timeout: float = 15,
    credentials: tuple[str, str] | None = None,
) -> list[dict[str, Any]]:
    if credentials is None:
        hass_url, token = read_load_history_credentials(secrets_path)
    else:
        hass_url, token = credentials
    try:
        query = urlencode(
            {
                "filter_entity_id": entity_id,
                "end_time": end.isoformat(),
                "no_attributes": "true",
            }
        )
        url = (
            f"{hass_url}api/history/period/{quote(start.isoformat(), safe='')}"
            f"?{query}"
        )
        request = Request(url, headers={"Authorization": f"Bearer {token}"})
        with urlopen(request, timeout=timeout) as response:
            raw_body = response.read()
    except (OSError, HTTPException, ValueError, UnicodeError) as exc:
        raise LoadHistoryError(
            f"Home Assistant load-history request failed "
            f"({type(exc).__name__})"
        ) from exc

    try:
        response_text = raw_body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LoadHistoryError(
            "Home Assistant load-history response is not valid UTF-8"
        ) from exc
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise LoadHistoryError(
            "Home Assistant load-history response contains malformed JSON"
        ) from exc

    if not isinstance(payload, list):
        raise LoadHistoryError("Malformed Home Assistant load-history response")
    records: list[dict[str, Any]] = []
    for entity_history in payload:
        if not isinstance(entity_history, list):
            raise LoadHistoryError("Malformed Home Assistant load-history response")
        for record in entity_history:
            if not isinstance(record, dict):
                raise LoadHistoryError(
                    "Malformed Home Assistant load-history response"
                )
            records.append(record)
    return records


def validate_load_history_records(
    records: list[dict[str, Any]],
    *,
    prediction_horizon: int,
    step_min: int,
    now: datetime,
) -> dict[str, Any]:
    if type(prediction_horizon) is not int or prediction_horizon <= 0:
        raise LoadHistoryError(
            "Effective prediction horizon must be a positive integer"
        )
    if type(step_min) is not int or step_min != 30:
        raise LoadHistoryError(
            "Native load-history validation requires a 30-minute step"
        )
    if now.tzinfo is None or now.utcoffset() is None:
        raise LoadHistoryError(
            "Load-history validation time must be timezone-aware"
        )

    local_tz = now.tzinfo
    now_utc = now.astimezone(timezone.utc)
    finite_samples_utc: list[datetime] = []
    for record in records:
        try:
            value = float(record.get("state", "nan"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value < 0:
            continue

        raw_timestamp = record.get("last_updated") or record.get("last_changed")
        if raw_timestamp is None:
            continue
        timestamp_text = str(raw_timestamp).strip()
        try:
            if timestamp_text.endswith("Z"):
                timestamp = datetime.fromisoformat(
                    timestamp_text.replace("Z", "+00:00")
                )
            else:
                timestamp = datetime.fromisoformat(timestamp_text)
        except ValueError:
            continue
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            continue
        timestamp_utc = timestamp.astimezone(timezone.utc)
        if timestamp_utc > now_utc:
            continue
        finite_samples_utc.append(timestamp_utc)

    if not finite_samples_utc:
        raise LoadHistoryError("Load history has no finite nonnegative samples")

    newest_utc = max(finite_samples_utc)
    newest_age_min = (now_utc - newest_utc).total_seconds() / 60.0

    newest_bin_utc = floor_to_step(newest_utc, step_min)
    first_required_bin_utc = newest_bin_utc - timedelta(
        minutes=step_min * (prediction_horizon - 1)
    )
    covered_bins_utc = {
        floor_to_step(timestamp_utc, step_min)
        for timestamp_utc in finite_samples_utc
        if first_required_bin_utc <= timestamp_utc <= now_utc
    }
    required_bins_utc = [
        first_required_bin_utc + timedelta(minutes=step_min * index)
        for index in range(prediction_horizon)
    ]
    required_set_utc = set(required_bins_utc)
    missing_bins_utc = sorted(required_set_utc - covered_bins_utc)
    summary: dict[str, Any] = {
        "mode": LOAD_HISTORY_MODE_HEALTHY,
        "required_bins": prediction_horizon,
        "covered_bins": len(covered_bins_utc & required_set_utc),
        "missing_bin_count": len(missing_bins_utc),
        "missing_bins": [value.isoformat() for value in missing_bins_utc],
        "interpolation_method": None,
        "newest_sample_at": newest_utc.isoformat(),
        "newest_sample_age_minutes": round(newest_age_min, 1),
    }
    if newest_age_min > 5:
        summary["mode"] = LOAD_HISTORY_MODE_UNSAFE
        raise LoadHistoryError(
            f"Load history newest finite sample is stale "
            f"({newest_age_min:.1f} minutes old; maximum 5)",
            summary=summary,
        )
    if not missing_bins_utc:
        return summary

    index_by_bin = {
        value: index for index, value in enumerate(required_bins_utc)
    }
    missing_indices = [index_by_bin[value] for value in missing_bins_utc]
    is_consecutive = all(
        right == left + 1
        for left, right in zip(missing_indices, missing_indices[1:])
    )
    has_brackets = (
        missing_indices[0] > 0
        and missing_indices[-1] < prediction_horizon - 1
    )
    is_bounded = (
        1 <= len(missing_indices) <= MAX_DEGRADED_LOAD_GAP_BINS
    )

    if is_consecutive and has_brackets and is_bounded:
        gap_start = missing_bins_utc[0]
        gap_end_exclusive = missing_bins_utc[-1] + timedelta(minutes=step_min)
        summary.update(
            {
                "mode": LOAD_HISTORY_MODE_DEGRADED,
                "gap_start": gap_start.isoformat(),
                "gap_end_exclusive": gap_end_exclusive.isoformat(),
                "gap_duration_minutes": int(
                    (gap_end_exclusive - gap_start).total_seconds() / 60
                ),
                "interpolation_method": "emhass_native_linear",
            }
        )
        return summary

    summary["mode"] = LOAD_HISTORY_MODE_UNSAFE
    rendered = ", ".join(
        value.astimezone(local_tz).isoformat()
        for value in missing_bins_utc[:4]
    )
    suffix = "..." if len(missing_bins_utc) > 4 else ""
    reason = (
        "gap is not one consecutive sequence"
        if not is_consecutive
        else "gap is not bracketed inside the required window"
        if not has_brackets
        else (
            f"gap exceeds {MAX_DEGRADED_LOAD_GAP_BINS} "
            "30-minute bins"
        )
    )
    raise LoadHistoryError(
        f"Load history unsafe: {reason}; missing "
        f"{len(missing_bins_utc)} required 30-minute bin(s): "
        f"{rendered}{suffix}",
        summary=summary,
    )


def read_load_history_config(share_dir: Path) -> tuple[str, int]:
    config_path = share_dir / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise LoadHistoryError(
            f"Invalid EMHASS config {config_path}: {type(exc).__name__}"
        ) from exc
    if not isinstance(config, dict):
        raise LoadHistoryError(
            f"Invalid EMHASS config {config_path}: root must be an object"
        )

    entity_id = config.get("sensor_power_load_no_var_loads")
    if not isinstance(entity_id, str) or not entity_id.strip():
        raise LoadHistoryError(
            f"Invalid EMHASS config {config_path}: "
            "sensor_power_load_no_var_loads must be a non-empty string"
        )
    step_min = config.get("optimization_time_step")
    if type(step_min) is not int or step_min != 30:
        raise LoadHistoryError(
            f"Invalid EMHASS config {config_path}: "
            "optimization_time_step must be 30"
        )
    return entity_id.strip(), step_min


def validate_native_load_history(
    secrets_path: Path,
    share_dir: Path,
    *,
    prediction_horizon: int,
    tz_name: str,
    now: datetime | None = None,
    load_history_config: tuple[str, int] | None = None,
    load_history_credentials: tuple[str, str] | None = None,
) -> dict[str, Any]:
    if load_history_config is None:
        entity_id, step_min = read_load_history_config(share_dir)
    else:
        entity_id, step_min = load_history_config
    checked_at = now or datetime.now(ZoneInfo(tz_name))
    checked_at_utc = checked_at.astimezone(timezone.utc)
    current_bin_utc = floor_to_step(checked_at_utc, step_min)
    start_utc = current_bin_utc - timedelta(
        minutes=step_min * prediction_horizon
    )
    fetch_kwargs: dict[str, Any] = {
        "start": start_utc,
        "end": checked_at_utc,
    }
    if load_history_credentials is not None:
        fetch_kwargs["credentials"] = load_history_credentials
    records = fetch_load_history_from_ha(
        secrets_path,
        entity_id,
        **fetch_kwargs,
    )
    summary = validate_load_history_records(
        records,
        prediction_horizon=prediction_horizon,
        step_min=step_min,
        now=checked_at,
    )
    logging.info(
        "HA load history (%s): %d/%d half-hour bins valid for %s; newest sample %s",
        summary["mode"],
        summary["covered_bins"],
        summary["required_bins"],
        entity_id,
        summary["newest_sample_at"],
    )
    return summary


def publish_ha_input_datetime(
    secrets_path: Path,
    entity_id: str,
    completed_at: datetime,
    *,
    timeout: float = 15,
    max_attempts: int = MPC_STATUS_MAX_ATTEMPTS,
    retry_delay_s: float = MPC_STATUS_RETRY_DELAY_S,
    credentials: tuple[str, str] | None = None,
) -> None:
    """Set a restart-restored HA input_datetime using one idempotent timestamp."""
    if completed_at.tzinfo is None or completed_at.utcoffset() is None:
        raise ValueError("completed_at must be timezone-aware")
    if credentials is None:
        if not secrets_path.is_file():
            raise RuntimeError(f"Secrets file not found: {secrets_path}")
        text = secrets_path.read_text(encoding="utf-8")
        hass_url = _parse_simple_yaml_value(text, "hass_url")
        token = _parse_simple_yaml_value(text, "long_lived_token")
        if not hass_url or not token:
            raise RuntimeError("hass_url or long_lived_token missing in secrets")
        if not hass_url.endswith("/"):
            hass_url += "/"
    else:
        hass_url, token = credentials

    url = f"{hass_url}api/services/input_datetime/set_datetime"
    body = json.dumps(
        {"entity_id": entity_id, "timestamp": completed_at.timestamp()}
    ).encode("utf-8")
    last_exc: OSError | HTTPException | None = None
    for attempt in range(1, max_attempts + 1):
        request = Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                response.read()
            return
        except (HTTPError, URLError, TimeoutError, OSError, HTTPException) as exc:
            last_exc = exc
            if attempt >= max_attempts:
                break
            logging.warning(
                "HA successful-MPC marker attempt %d/%d failed (%s); retry in %.0fs",
                attempt,
                max_attempts,
                exc,
                retry_delay_s,
            )
            time.sleep(retry_delay_s)

    raise RuntimeError(
        f"Could not update {entity_id} after {max_attempts} attempts: {last_exc}"
    ) from last_exc


def _hourly_from_forecast(forecast: list[Any], tz: ZoneInfo) -> dict[datetime, float]:
    hourly: dict[datetime, float] = {}
    for row in forecast:
        if not isinstance(row, dict):
            continue
        raw_dt = row.get("datetime")
        raw_price = row.get("electricity_price")
        if raw_dt is None or raw_price is None:
            continue
        dt = parse_forecast_datetime(str(raw_dt), tz)
        if dt is None:
            logging.warning("Skipping unparseable forecast datetime: %r", raw_dt)
            continue
        hour_start = dt.replace(minute=0, second=0, microsecond=0)
        hourly[hour_start] = float(raw_price) / 10_000_000
    return hourly


def expand_hourly_to_time_step(
    hourly: dict[datetime, float],
    *,
    step_min: int,
    slot_start: datetime,
) -> dict[str, float]:
    """Duplicate each hour into :00 and :30 slots (for 30-min EMHASS step)."""
    out: dict[str, float] = {}
    offsets = (0, step_min) if step_min == 30 else (0,)
    for hour_start, price in sorted(hourly.items()):
        for offset in offsets:
            slot = hour_start + timedelta(minutes=offset)
            if slot >= slot_start:
                out[slot.isoformat()] = round(price, 6)
    return out


def fetch_prices_from_ha(
    secrets_path: Path,
    share_dir: Path,
    *,
    price_sensor: str,
    tz_name: str,
    credentials: tuple[str, str] | None = None,
) -> dict[str, float]:
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    step_min = read_optimization_time_step_min(share_dir)
    slot_start = floor_to_step(now, step_min)

    state = fetch_ha_state(
        secrets_path,
        price_sensor,
        credentials=credentials,
    )
    entity_state = str(state.get("state", "")).strip().lower()
    if entity_state in ("unavailable", "unknown", ""):
        raise RuntimeError(
            f"Price sensor {price_sensor} unavailable (state={state.get('state')!r})"
        )

    forecast = state.get("attributes", {}).get("forecast")
    if not isinstance(forecast, list) or not forecast:
        raise RuntimeError(f"Price sensor {price_sensor} has empty forecast attribute")

    hourly = _hourly_from_forecast(forecast, tz)
    if not hourly:
        raise RuntimeError("No parseable prices in Zonneplan forecast")

    prices = expand_hourly_to_time_step(
        hourly, step_min=step_min, slot_start=slot_start
    )
    if len(prices) < MIN_PRICE_SLOTS:
        config_h = read_prediction_horizon_from_config(share_dir)
        raise RuntimeError(
            f"Only {len(prices)} price slots after expansion "
            f"(need >= {MIN_PRICE_SLOTS} minimum, config max {config_h})"
        )
    logging.info(
        "HA prices: %d slots from %s (sensor last_updated=%s)",
        len(prices),
        price_sensor,
        state.get("last_updated", "?"),
    )
    return prices


def price_fetch_summary(
    secrets_path: Path,
    share_dir: Path,
    *,
    price_sensor: str,
    tz_name: str,
    export_haircut: float,
    credentials: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """Dry-run helper: fetch and expand without writing CSVs."""
    tz = ZoneInfo(tz_name)
    state = fetch_ha_state(
        secrets_path,
        price_sensor,
        credentials=credentials,
    )
    last_updated = state.get("last_updated")
    age_min: float | None = None
    if last_updated:
        try:
            lu = parse_forecast_datetime(str(last_updated), tz)
            if lu:
                age_min = (datetime.now(tz) - lu).total_seconds() / 60.0
        except (TypeError, ValueError):
            pass
    prices = fetch_prices_from_ha(
        secrets_path,
        share_dir,
        price_sensor=price_sensor,
        tz_name=tz_name,
        credentials=credentials,
    )
    keys = sorted(prices.keys())
    n = len(prices)
    effective_h = resolve_prediction_horizon(share_dir, n)
    return {
        "price_rows": n,
        "prediction_horizon_config": read_prediction_horizon_from_config(share_dir),
        "prediction_horizon_effective": effective_h,
        "first_slot": keys[0] if keys else None,
        "last_slot": keys[-1] if keys else None,
        "price_sensor": price_sensor,
        "last_updated": last_updated,
        "last_updated_age_min": round(age_min, 1) if age_min is not None else None,
        "export_price_haircut": export_haircut,
    }


def refresh_price_csvs_from_ha(
    secrets_path: Path,
    share_dir: Path,
    *,
    price_sensor: str,
    tz_name: str,
    export_haircut: float,
    credentials: tuple[str, str] | None = None,
) -> tuple[dict[str, float], dict[str, float], Path, Path]:
    prices = fetch_prices_from_ha(
        secrets_path,
        share_dir,
        price_sensor=price_sensor,
        tz_name=tz_name,
        credentials=credentials,
    )
    prod_prices = apply_export_haircut(prices, export_haircut)
    load_csv, prod_csv = write_price_csvs(
        prices, prod_prices, share_dir, tz_name=tz_name
    )
    return prices, prod_prices, load_csv, prod_csv


def _normalize_timestamp(ts: str, tz_name: str) -> str:
    tz = ZoneInfo(tz_name)
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.astimezone(tz)
    return dt.isoformat()


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _write_text_preserve_inode(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_run_status(
    share_dir: Path,
    key: str,
    *,
    ok: bool,
    error: str | None = None,
    failure_kind: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    path = share_dir / "last_enever_run.json"
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    block: dict[str, Any] = {
        "ok": ok,
        "at": datetime.now(ZoneInfo(DEFAULT_TZ)).isoformat(),
    }
    if error:
        block["error"] = error[:500]
    if failure_kind:
        block["failure_kind"] = failure_kind
    if metadata:
        block.update(metadata)
    data[key] = block
    _atomic_write_text(path, json.dumps(data, indent=2) + "\n")


def write_mpc_weather_failures(share_dir: Path, err: str) -> None:
    is_weather = mpc_error_is_weather_related(err)
    write_run_status(
        share_dir,
        "mpc",
        ok=False,
        error=err,
        failure_kind="weather" if is_weather else None,
    )
    if is_weather:
        write_run_status(
            share_dir,
            "weather",
            ok=False,
            error=err,
            failure_kind="weather",
        )


def apply_export_haircut(
    prices: dict[str, float],
    haircut: float,
) -> dict[str, float]:
    return {
        ts: round(max(price - haircut, 0.0), 6)
        for ts, price in prices.items()
    }


def write_price_csvs(
    prices: dict[str, float],
    prod_prices: dict[str, float],
    share_dir: Path,
    *,
    tz_name: str,
) -> tuple[Path, Path]:
    share_dir.mkdir(parents=True, exist_ok=True)
    load_lines = [
        f"{_normalize_timestamp(ts, tz_name)},{price}"
        for ts, price in sorted(prices.items())
    ]
    prod_lines = [
        f"{_normalize_timestamp(ts, tz_name)},{price}"
        for ts, price in sorted(prod_prices.items())
    ]
    load_path = share_dir / LOAD_COST_CSV
    prod_path = share_dir / PROD_PRICE_CSV
    _write_text_preserve_inode(load_path, "\n".join(load_lines) + "\n")
    _write_text_preserve_inode(prod_path, "\n".join(prod_lines) + "\n")
    _atomic_write_text(
        share_dir / "prices_fetched_at.txt",
        f"{datetime.now(ZoneInfo(tz_name)).isoformat()}\n",
    )
    return load_path, prod_path


def fetch_soc_init(
    secrets_path: Path,
    sensor: str,
    timeout: float = 15,
    *,
    credentials: tuple[str, str] | None = None,
) -> float | None:
    try:
        state = fetch_ha_state(
            secrets_path,
            sensor,
            timeout=timeout,
            credentials=credentials,
        )
        soc_pct = float(state.get("state", "nan"))
        if 0 <= soc_pct <= 100:
            return round(soc_pct / 100.0, 4)
    except (
        OSError,
        HTTPException,
        UnicodeError,
        ValueError,
        TypeError,
        RuntimeError,
    ) as exc:
        logging.warning("Could not read SOC from HA (%s): %s", sensor, exc)
    return None


def fetch_soc_limit(
    secrets_path: Path,
    sensor: str,
    timeout: float = 15,
    *,
    credentials: tuple[str, str] | None = None,
) -> float | None:
    try:
        state = fetch_ha_state(
            secrets_path,
            sensor,
            timeout=timeout,
            credentials=credentials,
        )
        val_pct = float(state.get("state", "nan"))
        if 0 <= val_pct <= 100:
            return round(val_pct / 100.0, 4)
    except (
        OSError,
        HTTPException,
        UnicodeError,
        ValueError,
        TypeError,
        RuntimeError,
    ) as exc:
        logging.warning("Could not read SOC limit from HA (%s): %s", sensor, exc)
    return None


def call_emhass_action(
    base_url: str,
    action: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 180,
) -> str:
    url = f"{base_url.rstrip('/')}/action/{action}"
    body = json.dumps(payload or {}).encode("utf-8")
    req = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8")


def _is_retriable_emhass_error(exc: BaseException) -> bool:
    if isinstance(exc, HTTPError):
        return exc.code >= 500
    return isinstance(exc, URLError)


def mpc_error_is_weather_related(error: str) -> bool:
    err = error.lower()
    return any(k in err for k in WEATHER_FAILURE_KEYWORDS)


def call_emhass_action_with_retry(
    base_url: str,
    action: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 120,
    max_attempts: int = 3,
    backoff_seconds: tuple[float, ...] = WEATHER_CACHE_RETRY_BACKOFF_S,
) -> str:
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return call_emhass_action(base_url, action, payload, timeout=timeout)
        except (HTTPError, URLError) as exc:
            last_exc = exc
            if not _is_retriable_emhass_error(exc) or attempt >= max_attempts:
                raise
            wait = (
                backoff_seconds[attempt - 1]
                if attempt - 1 < len(backoff_seconds)
                else backoff_seconds[-1]
            )
            logging.warning(
                "EMHASS %s attempt %d/%d failed (%s); retry in %.0fs",
                action,
                attempt,
                max_attempts,
                exc,
                wait,
            )
            time.sleep(wait)
    assert last_exc is not None
    raise last_exc


def trigger_dayahead_optim(
    base_url: str,
    load_cost: dict[str, float] | None,
    prod_price: dict[str, float] | None,
    *,
    soc_init: float | None,
    soc_min: float | None = None,
    soc_max: float | None = None,
    timeout: float = 180,
) -> str:
    payload: dict[str, Any] = {}
    if load_cost is not None:
        payload["load_cost_forecast"] = load_cost
        payload["prod_price_forecast"] = (
            prod_price if prod_price is not None else load_cost
        )
    if soc_init is not None:
        payload["soc_init"] = soc_init
    if soc_min is not None:
        payload["battery_minimum_state_of_charge"] = soc_min
    if soc_max is not None:
        payload["battery_maximum_state_of_charge"] = soc_max
    return call_emhass_action(base_url, "dayahead-optim", payload, timeout=timeout)


def read_soc_final_from_config(share_dir: Path) -> float | None:
    config_path = share_dir / "config.json"
    if not config_path.is_file():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logging.warning("Could not read %s: %s", config_path, exc)
        return None
    for key in ("battery_target_state_of_charge", "soc_final"):
        val = data.get(key)
        if val is not None:
            soc = float(val)
            if 0.0 <= soc <= 1.0:
                return round(soc, 4)
    return None


def resolve_soc_final(
    *,
    omit: bool,
    cli_override: float | None,
    soc_min: float | None,
    share_dir: Path,
) -> float | None:
    """MPC horizon endpoint: UI min (soc_min) unless CLI override; config fallback."""
    if omit:
        return None
    if cli_override is not None:
        return round(float(cli_override), 4)
    if soc_min is not None:
        return soc_min
    return read_soc_final_from_config(share_dir)


def _read_config_json(share_dir: Path) -> dict[str, Any]:
    config_path = share_dir / "config.json"
    if not config_path.is_file():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logging.warning("Could not read %s: %s", config_path, exc)
        return {}


def read_prediction_horizon_from_config(share_dir: Path) -> int:
    """Configured MPC cap (steps). Actual run may be lower — see resolve_prediction_horizon."""
    val = _read_config_json(share_dir).get("prediction_horizon")
    if val is None:
        return DEFAULT_PREDICTION_HORIZON
    try:
        horizon = int(val)
    except (TypeError, ValueError):
        return DEFAULT_PREDICTION_HORIZON
    return horizon if horizon > 0 else DEFAULT_PREDICTION_HORIZON


def resolve_prediction_horizon(share_dir: Path, available_slots: int) -> int:
    """
    Cap EMHASS horizon to Zonneplan slots available from HA.

    EPEX day-ahead delivers 24 h for D+1 (~13:00 CET). Zonneplan's HA forecast is often
    same-calendar-day only until then, so slot count shrinks through the afternoon.
    """
    config_h = read_prediction_horizon_from_config(share_dir)
    if available_slots < MIN_PRICE_SLOTS:
        raise RuntimeError(
            f"Only {available_slots} Zonneplan price slots "
            f"(need >= {MIN_PRICE_SLOTS} minimum, config max {config_h})"
        )
    effective = min(config_h, available_slots)
    if effective < config_h:
        logging.info(
            "Capped prediction_horizon %d -> %d (%d Zonneplan slots)",
            config_h,
            effective,
            available_slots,
        )
    return effective


def build_naive_mpc_payload(
    *,
    prediction_horizon: int,
    soc_init: float | None,
    soc_final: float | None,
    soc_min: float | None = None,
    soc_max: float | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "weather_forecast_cache_only": True,
        "prediction_horizon": prediction_horizon,
    }
    if soc_init is not None:
        payload["soc_init"] = soc_init
    if soc_final is not None:
        payload["soc_final"] = soc_final
    if soc_min is not None:
        payload["battery_minimum_state_of_charge"] = soc_min
    if soc_max is not None:
        payload["battery_maximum_state_of_charge"] = soc_max
    return payload


def ensure_weather_forecast_cache(
    base_url: str,
    share_dir: Path,
    prediction_horizon: int,
    *,
    cache_timeout: float = 120,
    force: bool = False,
) -> None:
    reason = weather_cache_refresh_reason(share_dir, prediction_horizon, force=force)
    if reason:
        logging.info("Weather cache refresh needed (%s)", reason)
        clear_weather_cache_files()
    else:
        logging.info("Weather cache OK for %d-step MPC horizon", prediction_horizon)

    call_emhass_action_with_retry(
        base_url, "weather-forecast-cache", {}, timeout=cache_timeout
    )

    still = weather_cache_stale_reason(share_dir, prediction_horizon, force=False)
    if still:
        logging.warning(
            "Cache still invalid after weather-forecast-cache (%s); one retry",
            still,
        )
        clear_weather_cache_files()
        call_emhass_action_with_retry(
            base_url, "weather-forecast-cache", {}, timeout=cache_timeout
        )
        still = weather_cache_stale_reason(share_dir, prediction_horizon, force=False)
        if still:
            raise RuntimeError(
                f"Open-Meteo cache still invalid after weather-forecast-cache: {still}"
            )


def run_mpc_chain(
    base_url: str,
    *,
    soc_init: float | None,
    soc_final: float | None,
    soc_min: float | None = None,
    soc_max: float | None = None,
    prediction_horizon: int,
    share_dir: Path,
    cache_timeout: float = 120,
    optim_timeout: float = 180,
    publish_timeout: float = 60,
    force_weather: bool = False,
) -> None:
    ensure_weather_forecast_cache(
        base_url,
        share_dir,
        prediction_horizon,
        cache_timeout=cache_timeout,
        force=force_weather,
    )

    mpc_payload = build_naive_mpc_payload(
        prediction_horizon=prediction_horizon,
        soc_init=soc_init,
        soc_final=soc_final,
        soc_min=soc_min,
        soc_max=soc_max,
    )

    logging.info("MPC: naive-mpc-optim (CSV prices, cache-only weather, native naive load)")
    call_emhass_action(base_url, "naive-mpc-optim", mpc_payload, timeout=optim_timeout)
    logging.info("MPC: publish-data to Home Assistant")
    call_emhass_action(base_url, "publish-data", {}, timeout=publish_timeout)


def finalize_successful_mpc_run(
    secrets_path: Path,
    share_dir: Path,
    completed_at: datetime,
    *,
    prediction_horizon: int,
    load_history: dict[str, Any],
    credentials: tuple[str, str] | None = None,
) -> bool:
    publish_kwargs: dict[str, Any] = {}
    if credentials is not None:
        publish_kwargs["credentials"] = credentials
    try:
        publish_ha_input_datetime(
            secrets_path,
            MPC_SUCCESS_ENTITY_ID,
            completed_at,
            **publish_kwargs,
        )
    except RuntimeError as exc:
        error = f"HA successful-MPC marker failed: {exc}"
        logging.error(error)
        write_run_status(
            share_dir,
            "mpc",
            ok=False,
            error=error,
            failure_kind="status_publish",
            metadata={"load_history": load_history},
        )
        return False

    write_run_status(
        share_dir,
        "mpc",
        ok=True,
        metadata={
            "prediction_horizon": prediction_horizon,
            "plan_generated_at": completed_at.isoformat(),
            "load_history": load_history,
        },
    )
    write_run_status(share_dir, "weather", ok=True)
    return True


def _abort_mpc_prices(share_dir: Path, error: str) -> int:
    write_run_status(share_dir, "prices", ok=False, error=error)
    write_run_status(share_dir, "mpc", ok=False, error=f"price fetch failed: {error}")
    logging.error("MPC aborted: %s", error)
    return 1


def _abort_mpc_load_history(
    share_dir: Path,
    error: str,
    *,
    summary: dict[str, Any] | None = None,
    prediction_horizon: int = DEFAULT_PREDICTION_HORIZON,
) -> int:
    if summary is None:
        summary = {
            "mode": LOAD_HISTORY_MODE_UNSAFE,
            "required_bins": prediction_horizon,
            "covered_bins": 0,
            "missing_bin_count": prediction_horizon,
            "missing_bins": [],
            "interpolation_method": None,
            "newest_sample_at": None,
            "newest_sample_age_minutes": None,
        }
    write_run_status(
        share_dir,
        "mpc",
        ok=False,
        error=f"load history validation failed: {error}",
        failure_kind="load_history",
        metadata={"load_history": summary},
    )
    logging.error("MPC aborted: load history validation failed: %s", error)
    return 1


LOAD_INTERPOLATION_FAILURE_KEYWORDS = (
    "get_load_forecast",
    "p_load_forecast",
    "get_mix_forecast",
    "load forecast",
)


def mpc_error_is_load_interpolation_related(error: str) -> bool:
    lowered = error.lower()
    return any(
        keyword in lowered
        for keyword in LOAD_INTERPOLATION_FAILURE_KEYWORDS
    )


def write_mpc_chain_failure(
    share_dir: Path,
    error: str,
    *,
    load_history: dict[str, Any],
) -> None:
    is_weather = mpc_error_is_weather_related(error)
    is_interpolation = (
        load_history.get("mode") == LOAD_HISTORY_MODE_DEGRADED
        and mpc_error_is_load_interpolation_related(error)
    )
    failure_kind = (
        "weather"
        if is_weather
        else "load_interpolation"
        if is_interpolation
        else None
    )
    write_run_status(
        share_dir,
        "mpc",
        ok=False,
        error=error,
        failure_kind=failure_kind,
        metadata={"load_history": load_history},
    )
    if is_weather:
        write_run_status(
            share_dir,
            "weather",
            ok=False,
            error=error,
            failure_kind="weather",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--action",
        choices=("mpc", "publish", "refresh-weather"),
        default="mpc",
        help="mpc (default): HA prices + MPC chain; refresh-weather; publish",
    )
    parser.add_argument(
        "--emhass-base",
        default=None,
        help="EMHASS base URL (default: EMAHASS_BASE_URL)",
    )
    parser.add_argument(
        "--emhass-url",
        default=None,
        help="Deprecated: use --emhass-base",
    )
    parser.add_argument(
        "--price-sensor",
        default=DEFAULT_PRICE_SENSOR,
        help="HA entity for Zonneplan import tariff forecast",
    )
    parser.add_argument(
        "--export-price-haircut",
        type=float,
        default=DEFAULT_EXPORT_PRICE_HAIRCUT,
        help="EUR/kWh deducted from import for export CSV",
    )
    parser.add_argument(
        "--timezone",
        default=os.environ.get("TZ", DEFAULT_TZ),
        help="Timezone for price slots",
    )
    parser.add_argument(
        "--secrets",
        type=Path,
        default=Path(DEFAULT_SECRETS_ENV or DEFAULT_SECRETS),
        help="secrets_emhass.yaml path",
    )
    parser.add_argument(
        "--soc-sensor",
        default=DEFAULT_SOC_SENSOR,
        help="HA battery SOC sensor (percent)",
    )
    parser.add_argument(
        "--soc-min-sensor",
        default=DEFAULT_SOC_MIN_SENSOR,
        help="HA sensor for minimum battery SOC (percent, 0-100)",
    )
    parser.add_argument(
        "--soc-max-sensor",
        default=DEFAULT_SOC_MAX_SENSOR,
        help="HA sensor for maximum battery SOC (percent, 0-100)",
    )
    parser.add_argument(
        "--no-soc",
        action="store_true",
        help="Do not pass soc_init to EMHASS",
    )
    parser.add_argument(
        "--share-dir",
        type=Path,
        default=DEFAULT_EMAHASS_SHARE,
        help="EMHASS share directory",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No EMHASS calls; mpc dry-run still fetches HA prices",
    )
    parser.add_argument(
        "--write-only",
        action="store_true",
        help="Fetch HA prices and write CSVs only",
    )
    parser.add_argument(
        "--soc-refresh",
        action="store_true",
        help="dayahead-optim with cached CSVs and soc_init only",
    )
    parser.add_argument(
        "--soc-final",
        type=float,
        default=None,
        help="Override soc_final for MPC",
    )
    parser.add_argument(
        "--omit-soc-final",
        action="store_true",
        help="MPC: do not pass soc_final",
    )
    parser.add_argument(
        "--force-weather",
        action="store_true",
        help="Clear Open-Meteo cache before weather-forecast-cache",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.emhass_base is None:
        if args.emhass_url and "/action/" in args.emhass_url:
            args.emhass_base = args.emhass_url.split("/action/", 1)[0].rstrip("/")
        else:
            args.emhass_base = _default_emhass_base()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    load_history_config: tuple[str, int] | None = None
    load_history_credentials: tuple[str, str] | None = None
    if args.action == "mpc" and not args.write_only and not args.soc_refresh:
        try:
            load_history_config = read_load_history_config(args.share_dir)
            load_history_credentials = read_load_history_credentials(args.secrets)
        except LoadHistoryError as exc:
            if args.dry_run:
                print(
                    json.dumps(
                        {
                            "action": "mpc",
                            "error": f"load history config preflight failed: {exc}",
                            "load_history": {
                                "mode": LOAD_HISTORY_MODE_UNSAFE,
                                "required_bins": DEFAULT_PREDICTION_HORIZON,
                                "covered_bins": 0,
                                "missing_bin_count": DEFAULT_PREDICTION_HORIZON,
                                "missing_bins": [],
                                "interpolation_method": None,
                                "newest_sample_at": None,
                                "newest_sample_age_minutes": None,
                                "error": str(exc),
                            },
                        },
                        indent=2,
                    )
                )
                return 1
            return _abort_mpc_load_history(
                args.share_dir, str(exc), summary=exc.summary
            )

    if config_err := validate_cache_max_age_config(args.share_dir):
        logging.error("Invalid EMHASS weather cache config: %s", config_err)

    if args.action == "refresh-weather":
        horizon = read_prediction_horizon_from_config(args.share_dir)
        force = args.force_weather
        if not force:
            stale = weather_cache_refresh_reason(args.share_dir, horizon, force=False)
            if stale is None:
                if not docker_available():
                    stale = "docker socket unavailable"
                else:
                    logging.info("refresh-weather: cache OK, skipping EMHASS call")
                    write_run_status(args.share_dir, "weather", ok=True)
                    return 0
            logging.info("refresh-weather: cache stale (%s)", stale)
        try:
            ensure_weather_forecast_cache(
                args.emhass_base,
                args.share_dir,
                horizon,
                force=force,
            )
        except (HTTPError, URLError, RuntimeError) as exc:
            write_run_status(
                args.share_dir,
                "weather",
                ok=False,
                error=str(exc),
                failure_kind="weather",
            )
            return 1
        write_run_status(args.share_dir, "weather", ok=True)
        return 0

    if args.action == "publish":
        if args.dry_run:
            print(json.dumps({"action": "publish-data"}, indent=2))
            return 0
        try:
            print(call_emhass_action(args.emhass_base, "publish-data", {}, timeout=60).strip())
        except (HTTPError, URLError) as exc:
            logging.error("publish-data failed: %s", exc)
            return 1
        return 0

    if args.write_only:
        if args.dry_run:
            try:
                summary = price_fetch_summary(
                    args.secrets,
                    args.share_dir,
                    price_sensor=args.price_sensor,
                    tz_name=args.timezone,
                    export_haircut=args.export_price_haircut,
                )
            except (HTTPError, URLError, RuntimeError) as exc:
                write_run_status(args.share_dir, "prices", ok=False, error=str(exc))
                return 1
            print(json.dumps({"write_only": True, **summary}, indent=2))
            return 0
        try:
            prices, _prod, load_csv, prod_csv = refresh_price_csvs_from_ha(
                args.secrets,
                args.share_dir,
                price_sensor=args.price_sensor,
                tz_name=args.timezone,
                export_haircut=args.export_price_haircut,
            )
        except (HTTPError, URLError, RuntimeError) as exc:
            write_run_status(args.share_dir, "prices", ok=False, error=str(exc))
            return 1
        logging.info("Wrote %d slots to %s and %s", len(prices), load_csv, prod_csv)
        write_run_status(args.share_dir, "prices", ok=True)
        return 0

    if args.soc_refresh:
        load_csv = args.share_dir / LOAD_COST_CSV
        if not load_csv.is_file():
            logging.error("No cached prices at %s — run --write-only or mpc first", load_csv)
            return 1
        soc_init = None if args.no_soc else fetch_soc_init(args.secrets, args.soc_sensor)
        soc_min = fetch_soc_limit(args.secrets, args.soc_min_sensor)
        soc_max = fetch_soc_limit(args.secrets, args.soc_max_sensor)
        if args.dry_run:
            print(json.dumps({"soc_init": soc_init, "soc_min": soc_min, "soc_max": soc_max, "prices": "csv_cache"}, indent=2))
            return 0
        try:
            result = trigger_dayahead_optim(args.emhass_base, None, None, soc_init=soc_init, soc_min=soc_min, soc_max=soc_max)
        except (HTTPError, URLError) as exc:
            write_run_status(args.share_dir, "soc_refresh", ok=False, error=str(exc))
            return 1
        print(result.strip())
        write_run_status(args.share_dir, "soc_refresh", ok=True)
        return 0

    if args.action == "mpc":
        assert load_history_credentials is not None
        soc_init = (
            None
            if args.no_soc
            else fetch_soc_init(
                args.secrets,
                args.soc_sensor,
                credentials=load_history_credentials,
            )
        )
        soc_min = fetch_soc_limit(
            args.secrets,
            args.soc_min_sensor,
            credentials=load_history_credentials,
        )
        soc_max = fetch_soc_limit(
            args.secrets,
            args.soc_max_sensor,
            credentials=load_history_credentials,
        )
        soc_final = resolve_soc_final(
            omit=args.omit_soc_final,
            cli_override=args.soc_final,
            soc_min=soc_min,
            share_dir=args.share_dir,
        )
        try:
            price_summary = price_fetch_summary(
                args.secrets,
                args.share_dir,
                price_sensor=args.price_sensor,
                tz_name=args.timezone,
                export_haircut=args.export_price_haircut,
                credentials=load_history_credentials,
            )
        except (
            OSError,
            HTTPException,
            UnicodeError,
            ValueError,
            TypeError,
            RuntimeError,
        ) as exc:
            if args.dry_run:
                print(
                    json.dumps(
                        {
                            "action": "mpc",
                            "error": f"price fetch failed: {exc}",
                            "load_history": {
                                "mode": LOAD_HISTORY_MODE_UNSAFE,
                                "required_bins": DEFAULT_PREDICTION_HORIZON,
                                "covered_bins": 0,
                                "missing_bin_count": DEFAULT_PREDICTION_HORIZON,
                                "missing_bins": [],
                                "interpolation_method": None,
                                "newest_sample_at": None,
                                "newest_sample_age_minutes": None,
                            },
                        },
                        indent=2,
                    )
                )
                return 1
            return _abort_mpc_prices(args.share_dir, str(exc))

        prediction_horizon = price_summary["prediction_horizon_effective"]

        if args.dry_run:
            assert load_history_config is not None
            try:
                load_history_summary = validate_native_load_history(
                    args.secrets,
                    args.share_dir,
                    prediction_horizon=prediction_horizon,
                    tz_name=args.timezone,
                    load_history_config=load_history_config,
                    load_history_credentials=load_history_credentials,
                )
            except LoadHistoryError as exc:
                load_history_summary = exc.summary or {
                    "mode": LOAD_HISTORY_MODE_UNSAFE,
                    "required_bins": prediction_horizon,
                    "covered_bins": 0,
                    "missing_bin_count": prediction_horizon,
                    "missing_bins": [],
                    "interpolation_method": None,
                    "newest_sample_at": None,
                    "newest_sample_age_minutes": None,
                    "error": str(exc),
                }
            stale = weather_cache_stale_reason(
                args.share_dir,
                prediction_horizon,
                force=args.force_weather,
            )
            print(
                json.dumps(
                    {
                        "action": "mpc",
                        "soc_init": soc_init,
                        "soc_final": soc_final,
                        "soc_min": soc_min,
                        "soc_max": soc_max,
                        "prediction_horizon": prediction_horizon,
                        "weather_cache_stale": stale,
                        "load_history": load_history_summary,
                        **price_summary,
                        "chain": [
                            "ha-prices",
                            "weather-forecast-cache",
                            "naive-mpc-optim",
                            "publish-data",
                        ],
                    },
                    indent=2,
                )
            )
            return 0

        try:
            prices, _prod, _load_csv, _prod_csv = refresh_price_csvs_from_ha(
                args.secrets,
                args.share_dir,
                price_sensor=args.price_sensor,
                tz_name=args.timezone,
                export_haircut=args.export_price_haircut,
                credentials=load_history_credentials,
            )
            prediction_horizon = resolve_prediction_horizon(
                args.share_dir, len(prices)
            )
        except (
            OSError,
            HTTPException,
            UnicodeError,
            ValueError,
            TypeError,
            RuntimeError,
        ) as exc:
            return _abort_mpc_prices(args.share_dir, str(exc))

        write_run_status(args.share_dir, "prices", ok=True)

        assert load_history_config is not None
        try:
            load_history_summary = validate_native_load_history(
                args.secrets,
                args.share_dir,
                prediction_horizon=prediction_horizon,
                tz_name=args.timezone,
                load_history_config=load_history_config,
                load_history_credentials=load_history_credentials,
            )
        except LoadHistoryError as exc:
            return _abort_mpc_load_history(
                args.share_dir, str(exc), summary=exc.summary
            )

        try:
            run_mpc_chain(
                args.emhass_base,
                soc_init=soc_init,
                soc_final=soc_final,
                soc_min=soc_min,
                soc_max=soc_max,
                prediction_horizon=prediction_horizon,
                share_dir=args.share_dir,
                force_weather=args.force_weather,
            )
        except HTTPError as exc:
            err = f"HTTP {exc.code}: {exc.read().decode()[:300]}"
            write_mpc_chain_failure(
                args.share_dir, err, load_history=load_history_summary
            )
            return 1
        except (URLError, Exception) as exc:
            write_mpc_chain_failure(
                args.share_dir, str(exc), load_history=load_history_summary
            )
            return 1

        completed_at = datetime.now(ZoneInfo(args.timezone))
        finalized = finalize_successful_mpc_run(
            args.secrets,
            args.share_dir,
            completed_at,
            prediction_horizon=prediction_horizon,
            load_history=load_history_summary,
            credentials=load_history_credentials,
        )
        return 0 if finalized else 1

    logging.error("Unknown action %r", args.action)
    return 1


if __name__ == "__main__":
    sys.exit(main())
