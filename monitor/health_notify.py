#!/usr/bin/env python3
"""
Energy host health checks (.135). On failure, push notify.notify to Home Assistant (mobile app).

Runs via supercronic in energy-monitor container (docker.sock + /share read-only).
"""

from __future__ import annotations

import hashlib
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

LOAD_HISTORY_STATE_KEY = "_load_history_incident"
LOAD_HISTORY_ROOT_ENTITY = "input_boolean.emhass_load_history_root_active"
OWNERSHIP_STATE_KEY = "_load_history_root_ownership"
PLANNED_PROFIT_STATE_KEY = "planned_profit_missing"
PLANNED_PROFIT_JSON = "daily_planned_profit.json"


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
MPC_ESCALATION_SEC = 60 * 60
TZ = ZoneInfo(os.environ.get("TZ", "Europe/Amsterdam"))

B5_SUSTAINED_IDS = frozenset({"B5_cache", "B5_zero_fill"})
HARD_ALERT_IDS = frozenset({"A4", "A6", "B1"})

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


def call_ha_service(
    base_url: str,
    token: str,
    service: str,
    data: dict,
) -> None:
    domain, _, name = service.partition(".")
    url = f"{base_url}api/services/{domain}/{name}"
    body = json.dumps(data).encode("utf-8")
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


def notify_ha(
    base_url: str,
    token: str,
    service: str,
    title: str,
    message: str,
) -> None:
    call_ha_service(
        base_url,
        token,
        service,
        {"title": title, "message": message},
    )


def log_ha_event(
    *,
    incident_id: str,
    phase: str,
    message: str,
    base_url: str,
    token: str,
    action: str = "",
) -> bool:
    try:
        call_ha_service(
            base_url,
            token,
            "script.emhass_log_event",
            {
                "incident_id": incident_id,
                "phase": phase,
                "message": message,
                "action": action or "",
            },
        )
        return True
    except Exception as exc:
        logging.error(
            "HA event log failed %s phase=%s: %s",
            incident_id,
            phase,
            exc,
        )
        return False


def sync_load_history_root_ownership(
    active: bool,
    *,
    base_url: str,
    token: str,
) -> bool:
    """Publish B6 root ownership so HA can suppress derivative C2a/E1 phones."""
    service = (
        "input_boolean.turn_on" if active else "input_boolean.turn_off"
    )
    try:
        call_ha_service(
            base_url,
            token,
            service,
            {"entity_id": LOAD_HISTORY_ROOT_ENTITY},
        )
        return True
    except Exception as exc:
        logging.error(
            "HA load-history root ownership sync failed active=%s: %s",
            active,
            exc,
        )
        return False


def set_load_history_root_ownership(
    alert_state: dict[str, AlertState],
    *,
    desired_active: bool,
    base_url: str,
    token: str,
) -> bool:
    """Set desired B6 ownership and sync to HA; keep pending on failure."""
    entry = alert_state.setdefault(OWNERSHIP_STATE_KEY, AlertState())
    entry.ownership_desired_active = desired_active
    ok = sync_load_history_root_ownership(
        desired_active,
        base_url=base_url,
        token=token,
    )
    entry.ownership_pending = not ok
    return ok


def flush_load_history_root_ownership(
    alert_state: dict[str, AlertState],
    *,
    base_url: str,
    token: str,
) -> bool:
    """Retry a pending ownership sync until HA accepts it."""
    entry = alert_state.get(OWNERSHIP_STATE_KEY)
    if entry is None or not entry.ownership_pending:
        return True
    if entry.ownership_desired_active is None:
        entry.ownership_pending = False
        return True
    ok = sync_load_history_root_ownership(
        entry.ownership_desired_active,
        base_url=base_url,
        token=token,
    )
    entry.ownership_pending = not ok
    return ok


def load_history_root_is_active(alert_state: dict[str, AlertState]) -> bool:
    incident = alert_state.get(LOAD_HISTORY_STATE_KEY)
    if incident is not None and incident.incident_mode in ("degraded", "unsafe"):
        return True
    return any(
        key.startswith("B6_") and entry.phase is not None
        for key, entry in alert_state.items()
    )


_SIGNATURE_BODY_KEYS = frozenset({"message", "title", "action"})


def incident_signature(alert_id: str, context: dict) -> str:
    """Stable dedup key: alert id + non-body context (never age-bearing message text)."""
    stable_context = {
        key: value
        for key, value in context.items()
        if key not in _SIGNATURE_BODY_KEYS
    }
    if not stable_context:
        stable_context = {"kind": alert_id}
    payload = {
        "alert_id": alert_id,
        "context": stable_context,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def is_recoverable_alert(alert_id: str) -> bool:
    if alert_id in HARD_ALERT_IDS or alert_id == LOAD_HISTORY_STATE_KEY:
        return False
    if alert_id in {"B2", "B3", "B4"}:
        return True
    return alert_id.startswith(("B2_", "B5_", "B6_"))


def _dedup_path(share: Path) -> Path:
    return share / "monitor_alert_state.json"


@dataclass
class AlertState:
    last_notify: float = 0.0
    consecutive_failures: int = 0
    incident_mode: str | None = None
    incident_signature: str | None = None
    phase: str | None = None
    first_detected: float | None = None
    notification_signature: str | None = None
    escalation_deadline: float | None = None
    ownership_desired_active: bool | None = None
    ownership_pending: bool = False


@dataclass(frozen=True)
class LoadHistoryTransition:
    mode: str
    signature: str | None
    alert_id: str
    title: str
    message: str


def _parse_alert_entry(val: object) -> AlertState:
    if isinstance(val, (int, float)):
        return AlertState(last_notify=float(val))
    if isinstance(val, dict):
        first_detected = val.get("first_detected")
        escalation_deadline = val.get("escalation_deadline")
        desired = val.get("ownership_desired_active")
        return AlertState(
            last_notify=float(val.get("last_notify", 0) or 0),
            consecutive_failures=int(val.get("consecutive_failures", 0) or 0),
            incident_mode=val.get("incident_mode"),
            incident_signature=val.get("incident_signature"),
            phase=val.get("phase"),
            first_detected=(
                float(first_detected) if first_detected is not None else None
            ),
            notification_signature=val.get("notification_signature"),
            escalation_deadline=(
                float(escalation_deadline)
                if escalation_deadline is not None
                else None
            ),
            ownership_desired_active=(
                None if desired is None else bool(desired)
            ),
            ownership_pending=bool(val.get("ownership_pending", False)),
        )
    return AlertState()


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _escalation_fields(entry: AlertState) -> dict[str, object]:
    fields: dict[str, object] = {}
    if entry.phase is not None:
        fields["phase"] = entry.phase
    if entry.first_detected is not None:
        fields["first_detected"] = entry.first_detected
    if entry.escalation_deadline is not None:
        fields["escalation_deadline"] = entry.escalation_deadline
    if entry.notification_signature is not None:
        fields["notification_signature"] = entry.notification_signature
    return fields


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
        escalation = _escalation_fields(entry)
        if key == OWNERSHIP_STATE_KEY:
            raw[key] = {
                "ownership_desired_active": entry.ownership_desired_active,
                "ownership_pending": entry.ownership_pending,
            }
        elif key == LOAD_HISTORY_STATE_KEY:
            payload: dict[str, object] = {
                "last_notify": entry.last_notify,
                "incident_mode": entry.incident_mode,
                "incident_signature": entry.incident_signature,
            }
            payload.update(escalation)
            raw[key] = payload
        elif key in B5_SUSTAINED_IDS:
            payload = {
                "last_notify": entry.last_notify,
                "consecutive_failures": entry.consecutive_failures,
            }
            payload.update(escalation)
            raw[key] = payload
        elif escalation:
            payload = {"last_notify": entry.last_notify}
            payload.update(escalation)
            raw[key] = payload
        elif entry.last_notify:
            raw[key] = entry.last_notify
    _atomic_write_text(_dedup_path(share), json.dumps(raw, indent=2))


def clear_recoverable_fields(entry: AlertState) -> None:
    entry.phase = None
    entry.first_detected = None
    entry.escalation_deadline = None
    entry.notification_signature = None


def record_recoverable_incident(
    state: dict[str, AlertState],
    *,
    alert_id: str,
    context: dict,
    now: float,
    base_url: str,
    token: str,
) -> bool:
    signature = incident_signature(alert_id, context)
    entry = state.setdefault(alert_id, AlertState())
    if (
        entry.phase in ("detected", "recovering", "actionable")
        and entry.notification_signature == signature
    ):
        return True
    message = str(context.get("message", ""))
    entry.phase = "detected"
    entry.first_detected = now
    entry.escalation_deadline = now + MPC_ESCALATION_SEC
    entry.notification_signature = signature
    return log_ha_event(
        incident_id=alert_id,
        phase="detected",
        message=message,
        base_url=base_url,
        token=token,
    )


def should_escalate_recoverable(
    entry: AlertState,
    *,
    alert_id: str,
    now: float,
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
) -> bool:
    del alert_id, failure_threshold  # reserved for callers; deadline-only gate
    if entry.phase == "actionable":
        return False
    if entry.phase not in ("detected", "recovering"):
        return False
    # Recoverable phone gate is the approved 60-minute deadline only.
    # Do not treat monitor poll consecutive_failures as "scheduled failures"
    # (monitor cadence is 10 min; two polls ≈ 20 min).
    deadline = entry.escalation_deadline
    if deadline is None:
        if entry.first_detected is None:
            return False
        deadline = entry.first_detected + MPC_ESCALATION_SEC
    return now >= deadline


def process_recoverable_escalation(
    state: dict[str, AlertState],
    *,
    alert_id: str,
    title: str,
    message: str,
    now: float,
    base_url: str,
    token: str,
    service: str,
    context: dict | None = None,
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
) -> bool:
    entry = state.get(alert_id)
    if entry is None:
        return False
    sig_context = dict(context or {})
    if "message" not in sig_context:
        sig_context["message"] = message
    signature = incident_signature(alert_id, sig_context)
    if (
        entry.phase == "actionable"
        and entry.notification_signature == signature
    ):
        return False
    if not should_escalate_recoverable(
        entry,
        alert_id=alert_id,
        now=now,
        failure_threshold=failure_threshold,
    ):
        return False
    try:
        notify_ha(
            base_url,
            token,
            service,
            f"Energy: {title}",
            message,
        )
    except Exception as exc:
        logging.error("HA notify failed %s: %s", alert_id, exc)
        return False
    entry.phase = "actionable"
    entry.last_notify = now
    entry.notification_signature = signature
    log_ha_event(
        incident_id=alert_id,
        phase="actionable",
        message=message,
        base_url=base_url,
        token=token,
        action="Investigate energy-host monitor alert and restore MPC.",
    )
    return True


def process_recoverable_recovery(
    state: dict[str, AlertState],
    *,
    alert_id: str,
    message: str,
    now: float,
    base_url: str,
    token: str,
) -> bool:
    del now  # recovery is immediate when clear
    entry = state.get(alert_id)
    if entry is None or entry.phase is None:
        return False
    logged = log_ha_event(
        incident_id=alert_id,
        phase="resolved",
        message=message,
        base_url=base_url,
        token=token,
    )
    if not logged:
        return False
    clear_recoverable_fields(entry)
    return True


def normalize_load_history_error(error: str) -> str:
    normalized = re.sub(
        r"\(\d+(\.\d+)? minutes old; maximum \d+\)",
        "(stale)",
        error,
    )
    return normalized[:200]


def load_history_signature(mpc: dict) -> str:
    load_history = mpc.get("load_history")
    missing_bins = (
        load_history.get("missing_bins", [])
        if isinstance(load_history, dict)
        else []
    )
    payload = {
        "failure_kind": mpc.get("failure_kind"),
        "missing_bins": [str(value) for value in missing_bins],
    }
    if not payload["missing_bins"]:
        raw_error = str(mpc.get("error", ""))
        payload["error"] = normalize_load_history_error(raw_error)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def detect_load_history_transition(
    mpc: dict,
    state: AlertState,
) -> LoadHistoryTransition | None:
    load_history = mpc.get("load_history")
    if not isinstance(load_history, dict):
        return None
    reported_mode = load_history.get("mode")
    mode: str | None = None
    if mpc.get("ok") is True and reported_mode == "degraded":
        mode = "degraded"
    elif (
        mpc.get("ok") is False
        and mpc.get("failure_kind") == "load_history"
    ):
        mode = "unsafe"
    elif mpc.get("ok") is True and reported_mode == "healthy":
        mode = "healthy"
    if mode is None:
        return None

    if mode == "healthy":
        if state.incident_mode not in ("degraded", "unsafe"):
            return None
        return LoadHistoryTransition(
            mode="healthy",
            signature=None,
            alert_id="B6_load_history_recovered",
            title="EMHASS load history recovered",
            message=(
                "Native MPC load history is healthy again; "
                "degraded/unsafe gap incident cleared."
            ),
        )

    signature = load_history_signature(mpc)
    if (
        state.incident_mode == mode
        and state.incident_signature == signature
    ):
        return None
    missing = int(load_history.get("missing_bin_count", 0) or 0)
    duration = int(load_history.get("gap_duration_minutes", 0) or 0)
    if mode == "degraded":
        return LoadHistoryTransition(
            mode=mode,
            signature=signature,
            alert_id="B6_load_history_degraded",
            title="EMHASS load history degraded",
            message=(
                f"MPC remains operational with native interpolation for "
                f"{missing} missing bin(s), {duration} minutes."
            ),
        )
    error_l = str(mpc.get("error", "")).lower()
    if missing > 0:
        unsafe_message = (
            f"MPC is fail-closed for {missing} missing bin(s); "
            "Sigenergy remains in native self-consumption."
        )
    elif "stale" in error_l:
        unsafe_message = (
            "MPC is fail-closed because the load-history sample is stale; "
            "Sigenergy remains in native self-consumption."
        )
    else:
        unsafe_message = (
            "MPC is fail-closed for unsafe load history; "
            "Sigenergy remains in native self-consumption."
        )
    return LoadHistoryTransition(
        mode=mode,
        signature=signature,
        alert_id="B6_load_history_unsafe",
        title="EMHASS load history unsafe",
        message=unsafe_message,
    )


def apply_load_history_transition(
    state: AlertState,
    transition: LoadHistoryTransition,
    *,
    notified_at: float,
) -> None:
    state.incident_mode = transition.mode
    state.incident_signature = transition.signature
    state.last_notify = notified_at


def suppress_load_history_derivatives(
    failures: list[tuple[str, str, str]],
    mpc: dict,
    *,
    on_suppress: object | None = None,
) -> list[tuple[str, str, str]]:
    load_history = mpc.get("load_history")
    if not (
        mpc.get("ok") is False
        and mpc.get("failure_kind") == "load_history"
        and isinstance(load_history, dict)
        and load_history.get("mode") in ("degraded", "unsafe")
    ):
        return failures
    suppressed_ids = {"B2_mpc", "B3", "B5_cache"}
    kept: list[tuple[str, str, str]] = []
    for failure in failures:
        if failure[0] in suppressed_ids:
            if callable(on_suppress):
                on_suppress(failure)
            continue
        kept.append(failure)
    return kept


def read_mpc_block(share: Path) -> dict | None:
    path = share / "last_enever_run.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    mpc = data.get("mpc")
    return mpc if isinstance(mpc, dict) else None


def _clear_other_b6_incidents(
    alert_state: dict[str, AlertState],
    *,
    keep_alert_id: str | None,
) -> None:
    for key, entry in list(alert_state.items()):
        if not key.startswith("B6_"):
            continue
        if keep_alert_id is not None and key == keep_alert_id:
            continue
        if entry.phase is not None:
            clear_recoverable_fields(entry)


def _b6_record_context(transition: LoadHistoryTransition) -> dict:
    return {
        "kind": transition.mode,
        "load_signature": transition.signature,
        "message": transition.message,
    }


def _backfill_legacy_b6_incident(
    alert_state: dict[str, AlertState],
    *,
    incident: AlertState,
    transition: LoadHistoryTransition,
    now: float,
    base_url: str,
    token: str,
) -> bool:
    """Create recoverable B6 state from root-only legacy load-history tracking."""
    entry = alert_state.get(transition.alert_id)
    if entry is not None and entry.phase is not None:
        return False
    anchor = incident.last_notify if incident.last_notify else now
    context = _b6_record_context(transition)
    signature = incident_signature(transition.alert_id, context)
    entry = alert_state.setdefault(transition.alert_id, AlertState())
    entry.phase = "detected"
    entry.first_detected = anchor
    entry.escalation_deadline = anchor + MPC_ESCALATION_SEC
    entry.notification_signature = signature
    log_ha_event(
        incident_id=transition.alert_id,
        phase="detected",
        message=transition.message,
        base_url=base_url,
        token=token,
    )
    return True


def process_load_history_transition(
    alert_state: dict[str, AlertState],
    mpc: dict | None,
    *,
    base_url: str,
    token: str,
    service: str,
    now: float,
    suppressed_failures: list[tuple[str, str, str]] | None = None,
) -> bool:
    if mpc is None:
        return False
    flush_load_history_root_ownership(
        alert_state,
        base_url=base_url,
        token=token,
    )
    incident = alert_state.setdefault(
        LOAD_HISTORY_STATE_KEY,
        AlertState(),
    )
    transition = detect_load_history_transition(mpc, incident)
    acted = False

    if transition is not None and transition.mode == "healthy":
        open_b6 = [
            key
            for key, entry in alert_state.items()
            if key.startswith("B6_") and entry.phase is not None
        ]
        recovered_ok = True
        if open_b6:
            for key in open_b6:
                if not process_recoverable_recovery(
                    alert_state,
                    alert_id=key,
                    message=transition.message,
                    now=now,
                    base_url=base_url,
                    token=token,
                ):
                    recovered_ok = False
        elif incident.incident_mode in ("degraded", "unsafe"):
            recovered_ok = log_ha_event(
                incident_id=transition.alert_id,
                phase="resolved",
                message=transition.message,
                base_url=base_url,
                token=token,
            )
        if not recovered_ok:
            return False
        apply_load_history_transition(
            incident,
            transition,
            notified_at=now,
        )
        set_load_history_root_ownership(
            alert_state,
            desired_active=False,
            base_url=base_url,
            token=token,
        )
        return True

    if transition is not None:
        apply_load_history_transition(
            incident,
            transition,
            notified_at=now,
        )
        _clear_other_b6_incidents(
            alert_state,
            keep_alert_id=transition.alert_id,
        )
        record_recoverable_incident(
            alert_state,
            alert_id=transition.alert_id,
            context=_b6_record_context(transition),
            now=now,
            base_url=base_url,
            token=token,
        )
        set_load_history_root_ownership(
            alert_state,
            desired_active=True,
            base_url=base_url,
            token=token,
        )
        if suppressed_failures:
            for alert_id, _title, message in suppressed_failures:
                log_ha_event(
                    incident_id=alert_id,
                    phase="detected",
                    message=(
                        "Suppressed under load-history root cause: "
                        f"{message}"
                    ),
                    base_url=base_url,
                    token=token,
                )
        acted = True

    load_history = mpc.get("load_history")
    if not (
        isinstance(load_history, dict)
        and load_history.get("mode") in ("degraded", "unsafe")
    ):
        # Healthy/no-load path: still retry a pending turn_off.
        flush_load_history_root_ownership(
            alert_state,
            base_url=base_url,
            token=token,
        )
        return acted

    probe = detect_load_history_transition(mpc, AlertState())
    if probe is None or probe.mode not in ("degraded", "unsafe"):
        flush_load_history_root_ownership(
            alert_state,
            base_url=base_url,
            token=token,
        )
        return acted

    b6_entry = alert_state.get(probe.alert_id)
    if (
        incident.incident_mode == probe.mode
        and (b6_entry is None or b6_entry.phase is None)
    ):
        if _backfill_legacy_b6_incident(
            alert_state,
            incident=incident,
            transition=probe,
            now=now,
            base_url=base_url,
            token=token,
        ):
            set_load_history_root_ownership(
                alert_state,
                desired_active=True,
                base_url=base_url,
                token=token,
            )
            acted = True

    if load_history_root_is_active(alert_state):
        set_load_history_root_ownership(
            alert_state,
            desired_active=True,
            base_url=base_url,
            token=token,
        )
    else:
        flush_load_history_root_ownership(
            alert_state,
            base_url=base_url,
            token=token,
        )

    if process_recoverable_escalation(
        alert_state,
        alert_id=probe.alert_id,
        title=probe.title,
        message=probe.message,
        now=now,
        base_url=base_url,
        token=token,
        service=service,
        context=_b6_record_context(probe),
    ):
        acted = True
    return acted


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


def planned_profit_alert_signature(target: date) -> str:
    return f"planned_profit_missing:{target.isoformat()}"


def local_deadline_reached(now: datetime) -> bool:
    local = now.astimezone(TZ)
    return (local.hour, local.minute) >= (22, 0)


def lock_state_for_target(share: Path, target: date) -> str:
    path = share / PLANNED_PROFIT_JSON
    if not path.is_file():
        return "missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "invalid"
    days = data.get("days")
    if not isinstance(days, dict):
        return "invalid"
    record = days.get(target.isoformat())
    if not isinstance(record, dict):
        return "missing"
    if record.get("plan_date") != target.isoformat():
        return "invalid"
    return "locked"


def process_planned_profit_deadline(
    *,
    share: Path,
    state: dict[str, AlertState],
    now: datetime,
    base_url: str,
    token: str,
    service: str,
) -> int:
    local_now = now.astimezone(TZ)
    target = local_now.date() + timedelta(days=1)
    signature = planned_profit_alert_signature(target)
    entry = state.setdefault(PLANNED_PROFIT_STATE_KEY, AlertState())
    lock_state = lock_state_for_target(share, target)

    if not local_deadline_reached(local_now):
        if lock_state == "locked" and entry.notification_signature == signature:
            entry.notification_signature = None
            entry.last_notify = 0.0
        return 0

    if lock_state == "locked":
        if entry.notification_signature == signature:
            entry.notification_signature = None
            entry.last_notify = 0.0
        return 0

    if entry.notification_signature == signature and entry.last_notify:
        return 0

    if lock_state == "missing":
        message = (
            f"No EMHASS planned-profit lock exists for tomorrow "
            f"({target.isoformat()}). A day-ahead price forecast or a successful "
            f"MPC may be missing."
        )
    else:
        message = (
            f"EMHASS planned-profit lock record is invalid; tomorrow "
            f"({target.isoformat()}) is treated as unlocked."
        )

    try:
        notify_ha(base_url, token, service, "Energy: Planned profit", message)
        entry.notification_signature = signature
        entry.last_notify = local_now.timestamp()
        return 1
    except (urllib.error.HTTPError, OSError, urllib.error.URLError) as exc:
        logging.error("Planned profit notify failed: %s", exc)
        return 0


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

    mpc = read_mpc_block(share)
    suppressed_failures: list[tuple[str, str, str]] = []
    if mpc is not None:
        before = list(failures)
        failures = suppress_load_history_derivatives(failures, mpc)
        suppressed_ids = {item[0] for item in failures}
        suppressed_failures = [
            item for item in before if item[0] not in suppressed_ids
        ]

    failing_ids = {alert_id for alert_id, _, _ in failures}
    alert_state = load_alert_state(share)
    record_b5_outcomes(alert_state, failing_ids)

    now = time.time()
    process_load_history_transition(
        alert_state,
        mpc,
        base_url=base_url,
        token=token,
        service=notify_svc,
        now=now,
        suppressed_failures=suppressed_failures,
    )

    sent = 0
    for alert_id, title, message in failures:
        entry = alert_state.setdefault(alert_id, AlertState())
        if is_recoverable_alert(alert_id):
            # Always log/record on first detection and start the 60 min clock.
            record_recoverable_incident(
                alert_state,
                alert_id=alert_id,
                context={"message": message},
                now=now,
                base_url=base_url,
                token=token,
            )
            if process_recoverable_escalation(
                alert_state,
                alert_id=alert_id,
                title=title,
                message=message,
                now=now,
                base_url=base_url,
                token=token,
                service=notify_svc,
                failure_threshold=failure_threshold,
            ):
                sent += 1
            continue

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

    for alert_id, entry in list(alert_state.items()):
        if not is_recoverable_alert(alert_id):
            continue
        if entry.phase is None:
            continue
        if alert_id.startswith("B6_"):
            continue
        if alert_id in failing_ids:
            continue
        process_recoverable_recovery(
            alert_state,
            alert_id=alert_id,
            message=f"{alert_id} recovered",
            now=now,
            base_url=base_url,
            token=token,
        )

    sent += process_planned_profit_deadline(
        share=share,
        state=alert_state,
        now=datetime.now(TZ),
        base_url=base_url,
        token=token,
        service=notify_svc,
    )

    persist_alert_state(share, alert_state)

    if not failures:
        logging.info("All energy-host checks OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
