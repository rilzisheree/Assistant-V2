"""Persistent, opt-in reminder escalation state.

Normal reminders are still scheduled by :mod:`actions.reminder`.  This module
only tracks reminders whose tool call explicitly included an escalation object.
The state is deliberately small and JSON-backed so an assistant restart does
not silently lose an escalation that was already requested.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


_BASE_DIR = Path(__file__).resolve().parent.parent
_STORE = _BASE_DIR / "memory" / "reminder_escalations.json"
_LOCK = threading.RLock()
_DEFAULT_MESSENGER_ESCALATION_URL = (
    "https://www.messenger.com/e2ee/t/7721188384660930"
)

_ACTIVE_STATES = {
    "scheduled",
    "awaiting_user",
    "whatsapp_sending",
    "whatsapp_verifying",
    "awaiting_contact",
    "call_pending",
    "call_verifying",
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
        "messenger_escalation_url": str(
            config.get("messenger_escalation_url")
            or os.environ.get("MESSENGER_ESCALATION_URL")
            or _DEFAULT_MESSENGER_ESCALATION_URL
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


def get_messenger_escalation_url(config: dict[str, Any] | None = None) -> str:
    """Return the configured direct Messenger conversation URL."""
    return str(normalize_config(config).get("messenger_escalation_url", "")).strip()


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


def recover_inflight() -> None:
    """Move interrupted UI work to verification-only recovery states.

    Sending another message or starting another call after a process restart
    could duplicate an irreversible action. Recovery events therefore verify
    what is already visible and never repeat the action automatically.
    """
    with _LOCK:
        records = _read()
        changed = False
        for record in records:
            state = record.get("state")
            if state == "whatsapp_sending":
                record["state"] = "whatsapp_verifying"
                record["recovery_event_emitted"] = False
                record["recovery_reason"] = (
                    "Assistant restarted while WhatsApp work was in progress."
                )
                changed = True
            elif state == "call_pending":
                record["state"] = "call_verifying"
                record["recovery_event_emitted"] = False
                record["recovery_reason"] = (
                    "Assistant restarted while Messenger call work was in progress."
                )
                changed = True
        if changed:
            stamp = _now().isoformat()
            for record in records:
                if record.get("state") in _ACTIVE_STATES:
                    record["updated_at"] = stamp
            _write(records)


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
                    record["work_started_at"] = now.isoformat()
                    events.append(_event("whatsapp_due", record))
                    changed = True
                elif state == "whatsapp_verifying" and not record.get(
                    "recovery_event_emitted"
                ):
                    record["recovery_event_emitted"] = True
                    events.append(_event("whatsapp_recovery", record))
                    changed = True
                elif state == "awaiting_contact" and (
                    now >= _parse(record["contact_deadline"])
                ):
                    record["state"] = "call_pending"
                    record["work_started_at"] = now.isoformat()
                    events.append(_event("call_due", record))
                    changed = True
                elif (
                    state == "awaiting_contact"
                    and (
                        not record.get("last_response_check_at")
                        or now
                        >= _parse(record["last_response_check_at"])
                        + timedelta(seconds=10)
                    )
                ):
                    record["last_response_check_at"] = now.isoformat()
                    events.append(_event("contact_check", record))
                    changed = True
                elif state == "call_verifying" and not record.get(
                    "recovery_event_emitted"
                ):
                    record["recovery_event_emitted"] = True
                    events.append(_event("call_recovery", record))
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


def cancel(text: str) -> str | None:
    """Cancel the active escalation without treating cancellation as acknowledgement."""
    normalized = " ".join((text or "").lower().split())
    if not normalized:
        return None
    markers = (
        "cancel that reminder",
        "cancel the reminder",
        "cancel reminder",
        "cancel the baba escalation",
        "cancel baba escalation",
        "stop the escalation",
        "stop escalation",
    )
    if not any(marker in normalized for marker in markers):
        return None

    with _LOCK:
        records = _read()
        for record in reversed(records):
            if record.get("state") not in _ACTIVE_STATES:
                continue
            stamp = _now().isoformat()
            record["state"] = "cancelled"
            record["cancelled_at"] = stamp
            record["cancel_reason"] = text.strip()
            record["updated_at"] = stamp
            _write(records)
            return str(record.get("id", ""))
    return None


def cancel_by_id(reminder_id: str, reason: str = "Cancelled from reminders settings.") -> bool:
    """Cancel one escalation without relying on a voice command match."""
    if not reminder_id:
        return False
    with _LOCK:
        records = _read()
        for record in records:
            if record.get("id") != reminder_id:
                continue
            if record.get("state") not in _ACTIVE_STATES:
                return False
            stamp = _now().isoformat()
            record["state"] = "cancelled"
            record["cancelled_at"] = stamp
            record["cancel_reason"] = str(reason)
            record["updated_at"] = stamp
            _write(records)
            return True
    return False


def cleanup_legacy_test_records() -> int:
    """Remove persisted records left by the retired automatic test mode."""
    with _LOCK:
        records = _read()
        kept = []
        removed = 0
        for record in records:
            haystack = " ".join(
                str(record.get(key, "")) for key in ("id", "message", "state")
            ).lower()
            if "test" in haystack:
                removed += 1
            else:
                kept.append(record)
        if removed:
            _write(kept)
        return removed


def mark_whatsapp_result(
    reminder_id: str,
    result: str,
    sent: bool,
    outgoing_message: str = "",
    now: datetime | None = None,
) -> bool:
    """Store verified WhatsApp delivery or finish with an explicit failure."""
    now = now or _now()
    with _LOCK:
        records = _read()
        for record in records:
            if record.get("id") != reminder_id:
                continue
            if record.get("state") not in {"whatsapp_sending", "whatsapp_verifying"}:
                return False
            config = normalize_config(record.get("config"))
            record["whatsapp_result"] = str(result)
            record["whatsapp_verified"] = bool(sent)
            if outgoing_message:
                record["whatsapp_message"] = str(outgoing_message)
            if sent:
                record["state"] = "awaiting_contact"
                record["whatsapp_sent_at"] = now.isoformat()
                record["contact_deadline"] = (
                    now
                    + timedelta(minutes=config["contact_response_timeout_minutes"])
                ).isoformat()
            else:
                record["state"] = "failed"
                record["error"] = (
                    "WhatsApp delivery could not be verified: " + str(result)
                )
            record["updated_at"] = now.isoformat()
            _write(records)
            return True
    return False


def mark_contact_response(reminder_id: str, result: str) -> bool:
    """Finish an escalation after a verified new WhatsApp response."""
    with _LOCK:
        records = _read()
        for record in records:
            if record.get("id") != reminder_id:
                continue
            if record.get("state") not in {"awaiting_contact", "call_pending"}:
                return False
            stamp = _now().isoformat()
            record["state"] = "contact_responded"
            record["contact_response"] = str(result)
            record["contact_responded_at"] = stamp
            record["updated_at"] = stamp
            _write(records)
            return True
    return False


def mark_call_result(
    reminder_id: str,
    result: str,
    connected: bool | None,
    audio_bridge_supported: bool = False,
) -> bool:
    """Finish the escalation only after a verified call connection.

    This project has no Messenger-to-Gemini audio bridge. A connected call is
    therefore recorded as partially failed instead of starting or claiming a
    Gemini voice session.
    """
    with _LOCK:
        records = _read()
        for record in records:
            if record.get("id") != reminder_id:
                continue
            if connected is True and audio_bridge_supported:
                record["state"] = "call_connected"
            elif connected is True:
                record["state"] = "partial_failed"
                result = (
                    str(result)
                    + " Messenger connected, but bidirectional "
                    "Messenger/Gemini audio is unsupported; Gemini was not started."
                )
            elif connected is None:
                record["state"] = "partial_failed"
                result = (
                    str(result)
                    + " Messenger connection was not verifiable; no Gemini "
                    "voice session was started."
                )
            else:
                record["state"] = "failed"
            record["call_result"] = str(result)
            record["call_connected"] = connected
            record["gemini_audio_bridge_supported"] = bool(audio_bridge_supported)
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