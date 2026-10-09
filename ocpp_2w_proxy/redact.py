"""Make OCPP traffic safe to log: mask RFID tags, never print credentials."""

from __future__ import annotations

from typing import Any

from .ocpp import Call, CallError, CallResult, Message

_ID_TAG_KEYS = frozenset({"idTag", "parentIdTag"})
_VISIBLE_SUFFIX = 4


def mask(value: str) -> str:
    if len(value) <= _VISIBLE_SUFFIX:
        return "***"
    return "***" + value[-_VISIBLE_SUFFIX:]


def redact_payload(value: Any) -> Any:
    """Recursively copy a payload with every idTag / parentIdTag masked."""
    if isinstance(value, dict):
        return {
            key: mask(item) if key in _ID_TAG_KEYS and isinstance(item, str) else redact_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    return value


def describe(message: Message, include_payload: bool) -> str:
    """One-line description. Payloads are only included (redacted) when explicitly requested."""
    match message:
        case Call(id=message_id, action=action, payload=payload):
            text = f"Call {action} id={message_id}"
        case CallResult(id=message_id, payload=payload):
            text = f"CallResult id={message_id}"
        case CallError(id=message_id, code=code, description=description, details=payload):
            text = f"CallError {code} id={message_id} {description!r}"
    if include_payload:
        text += f" {redact_payload(payload)}"
    return text


def describe_credentials(username: str | None, password: str | None) -> str:
    """For handshake diagnostics: tells whether credentials were sent, never their value."""
    if username is None:
        return "no Authorization header"
    password_summary = f"present ({len(password)} chars)" if password else "absent"
    return f"username={username!r} password={password_summary}"
