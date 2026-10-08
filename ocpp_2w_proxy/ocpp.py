"""OCPP-J 1.6 message model: parsing, validation and serialisation of RPC frames."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum
from typing import Any

MAX_MESSAGE_ID_LENGTH = 36


def now_iso() -> str:
    """Current UTC time in the OCPP 1.6 DateTime format (e.g. 2026-10-08T12:34:56Z)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class MessageType(IntEnum):
    CALL = 2
    CALL_RESULT = 3
    CALL_ERROR = 4


class ProtocolError(ValueError):
    """Raised when a frame is not a well-formed OCPP-J message.

    message_id is the sender's id when it could still be read off the frame, so the
    caller can correlate its error reply; None when not even that was readable.
    """

    def __init__(self, reason: str, message_id: str | None = None, message_type: int | None = None):
        super().__init__(reason)
        self.message_id = message_id
        self.message_type = message_type

    @property
    def was_reply(self) -> bool:
        """The broken frame claimed to be a CallResult / CallError."""
        return self.message_type in (MessageType.CALL_RESULT, MessageType.CALL_ERROR)


@dataclass(frozen=True)
class Call:
    id: str
    action: str
    payload: dict[str, Any]

    def with_id(self, new_id: str) -> Call:
        return Call(new_id, self.action, self.payload)

    def with_payload(self, payload: dict[str, Any]) -> Call:
        return Call(self.id, self.action, payload)


@dataclass(frozen=True)
class CallResult:
    id: str
    payload: dict[str, Any]

    def with_id(self, new_id: str) -> CallResult:
        return CallResult(new_id, self.payload)


@dataclass(frozen=True)
class CallError:
    id: str
    code: str
    description: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def with_id(self, new_id: str) -> CallError:
        return CallError(new_id, self.code, self.description, self.details)


Message = Call | CallResult | CallError
Reply = CallResult | CallError


def new_message_id() -> str:
    return str(uuid.uuid4())


def parse(text: str | bytes) -> Message:
    """Parse one OCPP-J frame. Raises ProtocolError on anything malformed."""
    try:
        frame = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProtocolError(f"not valid JSON: {exc}") from exc

    if not isinstance(frame, list) or len(frame) < 3:
        raise ProtocolError("frame is not an array of at least 3 elements")

    message_type, message_id = frame[0], frame[1]
    if not isinstance(message_id, str) or not 0 < len(message_id) <= MAX_MESSAGE_ID_LENGTH:
        raise ProtocolError("invalid message id")

    try:
        match message_type:
            case MessageType.CALL:
                return _parse_call(frame, message_id)
            case MessageType.CALL_RESULT:
                return _parse_call_result(frame, message_id)
            case MessageType.CALL_ERROR:
                return _parse_call_error(frame, message_id)
            case _:
                raise ProtocolError(f"unknown message type {message_type!r}")
    except ProtocolError as exc:
        exc.message_id = message_id  # salvage the id: the sender can correlate our reply
        exc.message_type = message_type
        raise


def protocol_error_reply(error: ProtocolError) -> CallError | None:
    """The reply for a malformed frame, so its sender stops waiting for an answer.

    None for a malformed reply: a CallResult / CallError is never answered. Its sender is
    not waiting for anything; whoever waits for it needs substitute_reply() instead.
    """
    if error.was_reply:
        return None
    return CallError(error.message_id or new_message_id(), "ProtocolError", str(error))


def substitute_reply(error: ProtocolError) -> CallError | None:
    """What to treat as received when a reply to our Call arrived malformed, so the Call
    ends now with an error instead of waiting out its timeout."""
    if not error.was_reply or error.message_id is None:
        return None
    return CallError(error.message_id, "GenericError", f"malformed reply: {error}")


def _parse_call(frame: list, message_id: str) -> Call:
    if len(frame) != 4:
        raise ProtocolError("Call must have 4 elements")
    action, payload = frame[2], frame[3]
    if not isinstance(action, str) or not action:
        raise ProtocolError("Call action must be a non-empty string")
    if not isinstance(payload, dict):
        raise ProtocolError("Call payload must be an object")
    return Call(message_id, action, payload)


def _parse_call_result(frame: list, message_id: str) -> CallResult:
    if len(frame) != 3 or not isinstance(frame[2], dict):
        raise ProtocolError("CallResult must be [3, id, {payload}]")
    return CallResult(message_id, frame[2])


def _parse_call_error(frame: list, message_id: str) -> CallError:
    if len(frame) != 5:
        raise ProtocolError("CallError must have 5 elements")
    code, description, details = frame[2], frame[3], frame[4]
    if not isinstance(code, str) or not isinstance(description, str) or not isinstance(details, dict):
        raise ProtocolError("CallError fields have wrong types")
    return CallError(message_id, code, description, details)


def serialize(message: Message) -> str:
    match message:
        case Call(id=message_id, action=action, payload=payload):
            frame: list[Any] = [MessageType.CALL, message_id, action, payload]
        case CallResult(id=message_id, payload=payload):
            frame = [MessageType.CALL_RESULT, message_id, payload]
        case CallError(id=message_id, code=code, description=description, details=details):
            frame = [MessageType.CALL_ERROR, message_id, code, description, details]
    return json.dumps(frame, separators=(",", ":"))


def to_dict(call: Call) -> dict[str, Any]:
    """Representation used for persisting queued calls."""
    return {"id": call.id, "action": call.action, "payload": call.payload}


def from_dict(data: dict[str, Any]) -> Call:
    return Call(data["id"], data["action"], data["payload"])
