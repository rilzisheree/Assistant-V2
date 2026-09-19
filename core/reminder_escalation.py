"""Persistent, opt-in reminder escalation state.

Normal reminders are still scheduled by :mod:`actions.reminder`.  This module
only tracks reminders whose tool call explicitly included an escalation object.
The state is deliberately small and JSON-backed so an assistant restart does
not silently lose an escalation that was already requested.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


_BASE_DIR = Path(__file__).resolve().parent.parent
_STORE = _BASE_DIR / "memory" / "reminder_escalations.json"
_LOCK = threading.RLock()

_ACTIVE_STATES = {
    "scheduled",
    "awaiting_user",
    "whatsapp_sending",
    "awaiting_contact",
    "call_pending",
}


def _now() -> datetime:
    return datetime.now()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _read() -> list[dict[str, Any]]:
    try:
        raw = json.loads(_STORE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _write(records: list[dict[str, Any]]) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w", encoding="utf-8", dir=_STORE.parent, delete=False
    ) as tmp:
        json.dump(records, tmp, indent=2, ensure_ascii=False)
        tmp.write("\n")
        temp_name = tmp.name
    Path(temp_name).replace(_STORE)


def _minutes(value: Any, default: int, maximum: int = 24 * 60) -> int:
    try:
        return max(1, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def normalize_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Validate and normalize the explicit escalation configuration."""
    config = config if isinstance(config, dict) else {}
    enabled = bool(config.get("enabled", False))
    return {
        "enabled": enabled,
        "whatsapp_contact_name": str(
            config.get("whatsapp_contact_name", "")
        ).strip(),
        "messenger_contact_name": str(
            config.get("messenger_contact_name", "")
        ).strip(),
        "initial_message": str(config.get("initial_message", "")).strip(),
        "user_response_timeout_minutes": _minutes(
            config.get("user_response_timeout_minutes"), 15
        ),
        "contact_response_timeout_minutes": _minutes(
            config.get("contact_response_timeout_minutes"), 15
        ),
        "messenger_call_enabled": bool(config.get("messenger_call_enabled", True)),
        "conversation_language": str(
            config.get("conversation_language", "Egyptian Arabic")
        ).strip()
        or "Egyptian Arabic",
        "max_call_duration_minutes": _minutes(
            config.get("max_call_duration_minutes"), 5, maximum=120
        ),
    }


def register(
    reminder_id: str,
    target: datetime,
    message: str,
    config: dict[str, Any],
) -> str:
    """Persist one explicit escalation request and return its stable ID."""
    normalized = normalize_config(config)
    if not normalized["enabled"]:
        return ""
    if not normalized["whatsapp_contact_name"]:
        raise ValueError("WhatsApp contact name is required for escalation.")
    if not normalized["messenger_contact_name"]:
        raise ValueError("Messenger contact name is required for escalation.")

    record = {
        "id": reminder_id,
        "target": target.isoformat(),
        "message": str(message).strip(),
        "config": normalized,
        "state": "scheduled",
        "created_at": _now().isoformat(),
        "updated_at": _now().isoformat(),
    }
    with _LOCK:
        records = [item for item in _read() if item.get("id") != reminder_id]
        records.append(record)
        _write(records)
    return reminder_id


def _active(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in records if item.get("state") in _ACTIVE_STATES]


def _event(kind: str, record: dict[str, Any]) -> dict[str, Any]:
    return {"type": kind, "record": dict(record)}


def poll(now: datetime | None = None) -> list[dict[str, Any]]:
    """Advance due records and return work items for the background loop."""
    now = now or _now()
    events: list[dict[str, Any]] = []
    changed = False
    with _LOCK:
        records = _read()
        for record in _active(records):
            try:
                state = record["state"]
                config = normalize_config(record.get("config"))
                if state == "scheduled" and now >= _parse(record["target"]):
                    record["state"] = "awaiting_user"
                    record["user_deadline"] = (
                        now
                        + timedelta(
                            minutes=config["user_response_timeout_minutes"]
                        )
                    ).isoformat()
                    events.append(_event("user_due", record))
                    changed = True
                elif (
                    state == "awaiting_user"
                    and now >= _parse(record["user_deadline"])
                ):
                    record["state"] = "whatsapp_sending"
                    events.append(_event("whatsapp_due", record))
                    changed = True
                elif (
                    state == "awaiting_contact"
                    and now >= _parse(record["contact_deadline"])
                ):
                    record["state"] = "call_pending"
                    events.append(_event("call_due", record))
                    changed = True
                elif state == "whatsapp_sending":
                    # Recover a work item left behind by a process crash, but
                    # avoid retrying immediately on a healthy process.
                    started = record.get("work_started_at")
                    if not started or now >= _parse(started) + timedelta(minutes=5):
                        events.append(_event("whatsapp_due", record))
                        record["work_started_at"] = now.isoformat()
                        changed = True
                elif state == "call_pending":
                    started = record.get("work_started_at")
                    if not started or now >= _parse(started) + timedelta(minutes=5):
                        events.append(_event("call_due", record))
                        record["work_started_at"] = now.isoformat()
                        changed = True
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                record["state"] = "failed"
                record["error"] = f"Invalid escalation state: {exc}"
                changed = True
        if changed:
            stamp = _now().isoformat()
            for record in records:
                if record.get("state") in _ACTIVE_STATES:
                    record["updated_at"] = stamp
            _write(records)
    return events


def acknowledge(text: str) -> str | None:
    """Acknowledge the active user-facing reminder when wording is explicit."""
    normalized = " ".join((text or "").lower().split())
    if not normalized:
        return None
    markers = (
        "acknowledge",
        "acknowledged",
        "reminder done",
        "done",
        "got it",
        "understood",
        "cancel the reminder",
        "ignore the reminder",
        "تمام",
        "حاضر",
        "ماشي",
        "خلصت",
        "فاهم",
    )
    if not any(marker in normalized for marker in markers):
        return None

    with _LOCK:
        records = _read()
        for record in records:
            if record.get("state") == "awaiting_user":
                record["state"] = "acknowledged"
                record["acknowledged_at"] = _now().isoformat()
                record["updated_at"] = record["acknowledged_at"]
                _write(records)
                return str(record.get("id", ""))
    return None


def mark_whatsapp_result(
    reminder_id: str, result: str, now: datetime | None = None
) -> bool:
    """Store the WhatsApp result and begin the contact-response window."""
    now = now or _now()
    with _LOCK:
        records = _read()
        for record in records:
            if record.get("id") != reminder_id:
                continue
            config = normalize_config(record.get("config"))
            record["whatsapp_result"] = str(result)
            record["state"] = "awaiting_contact"
            record["contact_deadline"] = (
                now
                + timedelta(minutes=config["contact_response_timeout_minutes"])
            ).isoformat()
            record["updated_at"] = now.isoformat()
            _write(records)
            return True
    return False


def mark_call_result(reminder_id: str, result: str) -> bool:
    """Finish the escalation after the best-effort Messenger call attempt."""
    with _LOCK:
        records = _read()
        for record in records:
            if record.get("id") != reminder_id:
                continue
            record["state"] = "call_attempted"
            record["call_result"] = str(result)
            record["updated_at"] = _now().isoformat()
            _write(records)
            return True
    return False


def get(reminder_id: str) -> dict[str, Any] | None:
    with _LOCK:
        for record in _read():
            if record.get("id") == reminder_id:
                return dict(record)
    return None