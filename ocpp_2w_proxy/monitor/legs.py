"""One leg of an exchange: the hop to one peer, folded from its frames and decisions (pure)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from ..ocpp import Call, CallError, CallResult, Reply
from ..reasons import Outcome, Reason
from .rewrites import rewritten

PEER = "peer"
PROXY = "proxy"
UNKNOWN_TEXT = "Unknown (monitor gap)"

# A reply after one of these decisions arrived after the proxy stopped waiting: shown, but it changes nothing.
LATE_AFTER = frozenset({Reason.CHARGER_NO_REPLY})


class TimedReply(NamedTuple):
    reply: Reply
    at: float


def reply_kind(reply: Reply) -> str:
    return "error" if isinstance(reply, CallError) else "result"


@dataclass
class Leg:
    peer: str
    wire_id: str | None = None
    payload: Mapping[str, Any] | None = None
    sent_at: float | None = None
    outcome: Outcome | None = None
    reason: Reason | None = None
    reason_peer: str | None = None
    detail: object = None
    reply: Reply | None = None
    replied_at: float | None = None
    late: bool = False
    duplicate: bool = False
    _replied_wire_ids: set[str] = field(default_factory=set)

    @property
    def peer_error(self) -> bool:
        return isinstance(self.reply, CallError)

    @property
    def reply_payload(self) -> Mapping[str, Any] | None:
        return self.reply.payload if isinstance(self.reply, CallResult) else None

    def on_sent(self, call: Call, at: float) -> None:
        """A retry under a new wire id replaces the previous attempt."""
        self.wire_id, self.payload, self.sent_at = call.id, call.payload, at

    def on_peer_reply(self, reply: Reply, at: float) -> None:
        if reply.id in self._replied_wire_ids:
            self.duplicate = True
            return
        self._replied_wire_ids.add(reply.id)
        if self.reason in LATE_AFTER:
            self.late = True
            return
        self.reply, self.replied_at = reply, at
        self.outcome = Outcome.FAILED if isinstance(reply, CallError) else Outcome.DELIVERED

    def on_decision(self, reason: Reason, peer: str, detail: object) -> None:
        self.outcome, self.reason, self.reason_peer, self.detail = reason.outcome, reason, peer, detail

    def view(self, origin_payload: Mapping[str, Any] | None, proxy_reply: TimedReply | None, unknown: bool) -> dict:
        """The leg as ``docs/monitor_api.md`` shows it.

        ``proxy_reply`` is what the proxy itself sent back to the origin; it is this leg's reply only when the
        proxy answered in the peer's place. ``unknown`` marks a leg whose end the monitor may have lost.
        """
        reply, replied_by, replied_at = self._answer(proxy_reply)
        return {
            "peer": self.peer,
            "wire_id": self.wire_id,
            "outcome": None if self.outcome is None else self.outcome.value,
            "reason": None if self.reason is None else self.reason.name,
            "reason_text": self._reason_text(unknown),
            "reply": None if reply is None else reply_kind(reply),
            "error_code": reply.code if isinstance(reply, CallError) else None,
            "replied_by": replied_by,
            "sent_at": self.sent_at,
            "replied_at": replied_at,
            "rewritten": rewritten(origin_payload, self.payload) if origin_payload and self.payload else [],
            "late": self.late,
            "duplicate": self.duplicate,
        }

    def _answer(self, proxy_reply: TimedReply | None) -> tuple[Reply | None, str | None, float | None]:
        if self.reply is not None:
            return self.reply, PEER, self.replied_at
        if self.outcome is Outcome.ANSWERED_BY_PROXY and proxy_reply is not None:
            return proxy_reply.reply, PROXY, proxy_reply.at
        return None, None, None

    def _reason_text(self, unknown: bool) -> str | None:
        if self.reason is not None:
            return self.reason.describe(self.reason_peer or self.peer, self.detail)
        if self.outcome is None and unknown:
            return UNKNOWN_TEXT
        return None
