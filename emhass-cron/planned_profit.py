"""Planned covered-slot profit lock helpers for EMHASS MPC runs."""

from __future__ import annotations

import fcntl
import json
import logging
import math
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Europe/Amsterdam")
JSON_NAME = "daily_planned_profit.json"
LOCK_NAME = "daily_planned_profit.json.lock"
HELPER_ENTITY_ID = "input_text.emhass_planned_profit_payload"
MAX_HELPER_CHARS = 255
HA_PUBLISH_MAX_ATTEMPTS = 3
HA_PUBLISH_RETRY_DELAY_S = 2.0
DEFAULT_EMHASS_CONTAINER = "emhass"

OPT_RES_READER_PY = r"""
import csv
import json
import math
import sys
from datetime import datetime, timezone

path = sys.argv[1] if len(sys.argv) > 1 else "/data/opt_res_latest.csv"
rows = []
optim_status = None
with open(path, newline="", encoding="utf-8") as handle:
    reader = csv.DictReader(handle)
    fieldnames = reader.fieldnames or []
    if "cost_profit" not in fieldnames:
        raise SystemExit("missing cost_profit column")
    timestamp_key = "timestamp" if "timestamp" in fieldnames else fieldnames[0]
    for row in reader:
        raw_ts = row.get(timestamp_key) or row.get("")
        raw_profit = row.get("cost_profit")
        if raw_ts in (None, "") or raw_profit in (None, ""):
            continue
        try:
            profit = float(raw_profit)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(profit):
            continue
        row_status = row.get("optim_status")
        if optim_status is None and row_status not in (None, ""):
            optim_status = str(row_status)
        rows.append(
            {
                "timestamp": str(raw_ts),
                "cost_profit": profit,
                "optim_status": None if row_status in (None, "") else str(row_status),
            }
        )
print(
    json.dumps(
        {
            "optim_status": optim_status,
            "row_count": len(rows),
            "rows": rows,
        }
    )
)
"""


class StoreCorruptError(RuntimeError):
    """Existing daily_planned_profit.json cannot be parsed; do not overwrite it."""


@dataclass(frozen=True)
class OptResRow:
    timestamp: datetime
    cost_profit: float
    optim_status: str | None


def target_date(now: datetime) -> date:
    local = now.astimezone(LOCAL_TZ)
    return local.date() + timedelta(days=1)


def _parse_datetime(raw: str) -> datetime | None:
    raw = raw.strip()
    try:
        if raw.endswith("Z"):
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(raw)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(raw[:19] if len(raw) > 16 else raw, fmt)
                break
            except ValueError:
                dt = None
        if dt is None:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ)


def forecast_has_target_date(forecast: list[Any], *, now: datetime) -> bool:
    target = target_date(now)
    for item in forecast:
        if not isinstance(item, dict):
            continue
        raw = item.get("datetime")
        if raw is None:
            continue
        parsed = _parse_datetime(str(raw))
        if parsed is not None and parsed.date() == target:
            return True
    return False


def parse_opt_res_payload(payload: dict[str, Any]) -> list[OptResRow]:
    rows_raw = payload.get("rows")
    if not isinstance(rows_raw, list):
        raise ValueError("payload rows must be a list")
    parsed: list[OptResRow] = []
    for row in rows_raw:
        if not isinstance(row, dict):
            continue
        if "cost_profit" not in row:
            raise ValueError("row missing cost_profit")
        timestamp_raw = row.get("timestamp")
        if timestamp_raw in (None, ""):
            continue
        ts = _parse_datetime(str(timestamp_raw))
        if ts is None:
            continue
        try:
            profit = float(row["cost_profit"])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(profit):
            continue
        status = row.get("optim_status")
        if status in (None, ""):
            status = None
        else:
            status = str(status)
        parsed.append(OptResRow(timestamp=ts, cost_profit=profit, optim_status=status))
    return parsed


def _normalize_row_timestamp(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(LOCAL_TZ)


def covered_profit(
    rows: list[OptResRow],
    plan_date: date,
    *,
    step_min: int = 30,
) -> dict[str, Any] | None:
    matching: list[datetime] = []
    total = 0.0
    for row in rows:
        local_ts = _normalize_row_timestamp(row.timestamp)
        if local_ts.date() != plan_date:
            continue
        if not math.isfinite(row.cost_profit):
            continue
        matching.append(local_ts)
        total += row.cost_profit
    if not matching:
        return None
    matching.sort()
    coverage_start = matching[0].isoformat()
    coverage_end_exclusive = (
        matching[-1] + timedelta(minutes=step_min)
    ).isoformat()
    return {
        "plan_date": plan_date.isoformat(),
        "planned_eur": round(total, 2),
        "slot_count": len(matching),
        "horizon_slots": len(rows),
        "coverage_start": coverage_start,
        "coverage_end_exclusive": coverage_end_exclusive,
        "partial": True,
    }


def read_opt_res_via_docker(
    *,
    container: str,
    path: str = "/data/opt_res_latest.csv",
    runner: Callable[..., CompletedProcess[str]] | None = None,
) -> dict[str, Any]:
    if runner is None:
        runner = subprocess.run
    cmd = ["docker", "exec", container, "python3", "-c", OPT_RES_READER_PY, path]
    result = runner(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(
            f"docker exec failed (exit {result.returncode}): {stderr or 'no stderr'}"
        )
    stdout = (result.stdout or "").strip()
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"docker exec returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("docker exec returned non-object JSON")
    return payload


def _empty_store() -> dict[str, Any]:
    return {
        "version": 1,
        "timezone": "Europe/Amsterdam",
        "metric": "emhass_cost_profit_covered_slots",
        "days": {},
    }


def load_store(share_dir: Path) -> dict[str, Any]:
    path = share_dir / JSON_NAME
    if not path.is_file():
        return _empty_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StoreCorruptError(f"malformed {JSON_NAME}: {exc}") from exc
    if not isinstance(data, dict):
        raise StoreCorruptError(f"{JSON_NAME} is not a JSON object")
    days = data.get("days")
    if days is None:
        data["days"] = {}
    elif not isinstance(days, dict):
        raise StoreCorruptError(f"{JSON_NAME} days is not an object")
    data.setdefault("version", 1)
    data.setdefault("timezone", "Europe/Amsterdam")
    data.setdefault("metric", "emhass_cost_profit_covered_slots")
    return data


def _prune_days(days: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    cutoff = now.astimezone(LOCAL_TZ).date() - timedelta(days=14)
    pruned: dict[str, Any] = {}
    for key, value in days.items():
        try:
            day = date.fromisoformat(key)
        except ValueError:
            continue
        if day >= cutoff:
            pruned[key] = value
    return pruned


def persist_lock(
    share_dir: Path,
    record: dict[str, Any],
    *,
    now: datetime,
) -> str:
    plan_date_str = str(record["plan_date"])
    share_dir.mkdir(parents=True, exist_ok=True)
    lock_path = share_dir / LOCK_NAME
    json_path = share_dir / JSON_NAME
    tmp_path = share_dir / f"{JSON_NAME}.tmp"
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        store = load_store(share_dir)
        days = store.get("days")
        if not isinstance(days, dict):
            days = {}
        if plan_date_str in days:
            return "exists"
        days[plan_date_str] = record
        store["days"] = _prune_days(days, now=now)
        tmp_path.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(json_path)
    return "written"


def day_record(share_dir: Path, plan_date: date) -> dict[str, Any] | None:
    days = load_store(share_dir).get("days")
    if not isinstance(days, dict):
        return None
    record = days.get(plan_date.isoformat())
    return record if isinstance(record, dict) else None


def helper_payload(record: dict[str, Any]) -> str:
    payload_obj = {
        "plan_date": record["plan_date"],
        "planned_eur": record["planned_eur"],
        "locked_at": record["locked_at"],
        "slot_count": record["slot_count"],
        "coverage_start": record["coverage_start"],
        "coverage_end_exclusive": record["coverage_end_exclusive"],
        "partial": record["partial"],
    }
    payload = json.dumps(payload_obj, separators=(",", ":"), sort_keys=True)
    if len(payload) > MAX_HELPER_CHARS:
        raise ValueError(f"helper payload exceeds {MAX_HELPER_CHARS} characters")
    return payload


def _parse_simple_yaml_value(text: str, key: str) -> str | None:
    prefix = f"{key}:"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            value = stripped[len(prefix) :].strip()
            return value or None
    return None


def _ha_credentials(
    secrets_path: Path,
    credentials: tuple[str, str] | None,
) -> tuple[str, str]:
    if credentials is not None:
        hass_url, token = credentials
    else:
        if not secrets_path.is_file():
            raise RuntimeError(f"Secrets file not found: {secrets_path}")
        text = secrets_path.read_text(encoding="utf-8")
        hass_url = _parse_simple_yaml_value(text, "hass_url")
        token = _parse_simple_yaml_value(text, "long_lived_token")
        if not hass_url or not token:
            raise RuntimeError("hass_url or long_lived_token missing in secrets")
    if not hass_url.endswith("/"):
        hass_url += "/"
    return hass_url, token


def publish_ha_input_text(
    secrets_path: Path,
    entity_id: str,
    value: str,
    *,
    timeout: float = 15,
    max_attempts: int = HA_PUBLISH_MAX_ATTEMPTS,
    retry_delay_s: float = HA_PUBLISH_RETRY_DELAY_S,
    credentials: tuple[str, str] | None = None,
) -> None:
    hass_url, token = _ha_credentials(secrets_path, credentials)
    url = f"{hass_url}api/services/input_text/set_value"
    body = json.dumps({"entity_id": entity_id, "value": value}).encode("utf-8")
    last_exc: Exception | None = None
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
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_exc = exc
            if attempt >= max_attempts:
                break
            logging.warning(
                "HA input_text publish attempt %d/%d failed (%s); retry in %.0fs",
                attempt,
                max_attempts,
                exc,
                retry_delay_s,
            )
            time.sleep(retry_delay_s)
    raise RuntimeError(
        f"Could not update {entity_id} after {max_attempts} attempts: {last_exc}"
    ) from last_exc


def _write_planned_profit_status(
    share_dir: Path,
    *,
    ok: bool,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    path = share_dir / "last_enever_run.json"
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                data = parsed
        except (OSError, UnicodeError, json.JSONDecodeError):
            data = {}
    block: dict[str, Any] = {
        "ok": ok,
        "at": datetime.now(LOCAL_TZ).isoformat(),
    }
    if error:
        block["error"] = error[:500]
    if metadata:
        block.update(metadata)
    data["planned_profit"] = block
    share_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def run_planned_profit_side_effect(
    *,
    share_dir: Path,
    snapshot: Any,
    completed_at: datetime,
    secrets_path: Path,
    credentials: tuple[str, str] | None = None,
    docker_runner: Callable[..., CompletedProcess[str]] | None = None,
    container: str | None = None,
) -> None:
    try:
        plan_date = target_date(completed_at)
        forecast = snapshot.state.get("attributes", {}).get("forecast")
        if not isinstance(forecast, list):
            forecast = []
        locked_new = False
        status_meta: dict[str, Any] = {
            "locked": False,
            "plan_date": plan_date.isoformat(),
        }
        if (
            forecast_has_target_date(forecast, now=completed_at)
            and day_record(share_dir, plan_date) is None
        ):
            container_name = container or os.environ.get(
                "EMHASS_CONTAINER", DEFAULT_EMHASS_CONTAINER
            )
            payload = read_opt_res_via_docker(
                container=container_name,
                runner=docker_runner,
            )
            optim_status = payload.get("optim_status")
            if optim_status is not None and str(optim_status) != "Optimal":
                status_meta["optim_status"] = str(optim_status)
            else:
                rows = parse_opt_res_payload(payload)
                covered = covered_profit(rows, plan_date)
                if covered is not None:
                    record = {
                        **covered,
                        "locked_at": completed_at.isoformat(),
                        "mpc_completed_at": completed_at.isoformat(),
                        "locked_on_date": completed_at.astimezone(LOCAL_TZ)
                        .date()
                        .isoformat(),
                    }
                    result = persist_lock(share_dir, record, now=completed_at)
                    locked_new = result == "written"
                    status_meta["locked"] = locked_new

        today = completed_at.astimezone(LOCAL_TZ).date()
        today_record = day_record(share_dir, today)
        if today_record is not None:
            publish_ha_input_text(
                secrets_path,
                HELPER_ENTITY_ID,
                helper_payload(today_record),
                credentials=credentials,
            )
        _write_planned_profit_status(share_dir, ok=True, metadata=status_meta)
    except Exception as exc:
        logging.exception("Planned profit side effect failed")
        try:
            _write_planned_profit_status(share_dir, ok=False, error=str(exc)[:500])
        except OSError:
            logging.exception("Could not record planned_profit status")
