"""Folds the flow events of one exchange into its summary (pure).

The writer feeds it live events; the read path feeds it the events rebuilt from stored frames and
decisions, so both show the same legs. Events must already be correlated to this exchange.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..config import PRIMARY_NAME
from ..ocpp import Call
from ..reasons import Reason
from .events import EventKind, FlowEvent
from .exchange_outcome import is_problem, overall_outcome
from .headline import headline
from .legs import Leg, TimedReply
from .rewrites import transaction_id

CHARGER = "charger"
PROXY_ORIGIN = "proxy"
DEBUG_ORIGIN = "debug"
# How long a leg may stay open before the monitor assumes it lost the leg's end (call_timeout plus a margin).
DEFAULT_STALE_AFTER = 120.0


class ExchangeProjection:
    def __init__(self, exchange_id: int, *, stale_after: float = DEFAULT_STALE_AFTER):
        self.exchange_id = exchange_id
        self.stale_after = stale_after
        self.charger_id: str | None = None
        self.started_at: float | None = None
        self.last_at: float | None = None
        # Set by the writer: frozen at the first insert, and when a monitor gap overlapped the exchange.
        self.not_sent: tuple[str, ...] = ()
        self.incomplete = False
        self.proxy_kind: str | None = None
        # Set from the store before applying events: the origin the exchange's first write recorded wins, so an
        # exchange continued after a restart (queued, then delivered) keeps its origin.
        self.known_origin: str | None = None
        self._origin_peer: str | None = None  # the peer the origin Call was received from
        self._origin_call: Call | None = None
        self._from_debug = False
        self._proxy_reply: TimedReply | None = None
        self._legs: dict[str, Leg] = {}
        self._apply: Mapping[EventKind, Callable[[FlowEvent], None]] = {
            EventKind.RECEIVED: self._on_received,
            EventKind.SENT: self._on_sent,
            EventKind.DECIDED: self._on_decided,
        }

    def apply(self, event: FlowEvent) -> None:
        handle = self._apply.get(event.kind)
        if handle is None:
            return  # link changes belong to no exchange
        if self.charger_id is None:
            self.charger_id = event.charger_id
        self.started_at = event.at if self.started_at is None else min(self.started_at, event.at)
        self.last_at = event.at if self.last_at is None else max(self.last_at, event.at)
        handle(event)

    @property
    def origin(self) -> str | None:
        """Who started the exchange; None for an orphan reply, whose Call the monitor never saw."""
        if self.known_origin is not None:
            return self.known_origin
        if self._origin_peer is not None:
            return self._origin_peer
        if self._from_debug:
            return DEBUG_ORIGIN
        return PROXY_ORIGIN if self._origin_call is not None else None

    @property
    def action(self) -> str | None:
        return None if self._origin_call is None else self._origin_call.action

    @property
    def payload(self) -> Mapping[str, Any] | None:
        """The origin payload, against which every leg's rewrites are measured."""
        return None if self._origin_call is None else self._origin_call.payload

    @property
    def settled(self) -> bool:
        """Every leg has ended (a queued leg counts as ended); an exchange without legs is not settled."""
        return bool(self._legs) and all(leg.outcome is not None for leg in self._legs.values())

    def summary(self, now: float | None = None) -> dict:
        """The exchange summary of ``docs/monitor_api.md``. ``now`` lets long-open legs show as unknown."""
        summary = self.overview()
        summary["legs"] = [
            leg.view(self.payload, self._proxy_reply, self._unknown(leg, now)) for leg in self._legs.values()
        ]
        if self.origin == PROXY_ORIGIN:
            summary["proxy_kind"] = self.proxy_kind
        return summary

    def overview(self) -> dict:
        """The summary without its legs: all the exchange's stored row needs, and far cheaper to build."""
        legs = self._legs.values()
        payload = self.payload or {}
        return {
            "id": str(self.exchange_id),
            "charger_id": self.charger_id,
            "origin": self.origin,
            "action": self.action,
            "started_at": self.started_at,
            "last_at": self.last_at,
            "outcome": _value(overall_outcome(legs)),
            "problem": is_problem(legs),
            "incomplete": self.incomplete,
            "transaction_id": self._transaction_id(),
            "connector_id": _int(payload.get("connectorId")),
            "id_tag": _str(payload.get("idTag")),
            "headline": headline(self.action, payload),
            "not_sent": [peer for peer in self.not_sent if peer not in self._legs],
        }

    # --- folding --------------------------------------------------------------------------

    def _on_received(self, event: FlowEvent) -> None:
        message = event.message
        if isinstance(message, Call):
            if self._origin_peer is None:
                self._origin_peer, self._origin_call = event.peer, message
        elif message is not None:
            self._leg(event.peer).on_peer_reply(message, event.at)

    def _on_sent(self, event: FlowEvent) -> None:
        message = event.message
        if isinstance(message, Call):
            if self._origin_call is None:
                self._origin_call = message  # the proxy (or the debug endpoint) started this exchange
            self._leg(event.peer).on_sent(message, event.at)
        elif message is not None and event.peer == self.origin and self._proxy_reply is None:
            self._proxy_reply = TimedReply(message, event.at)

    def _on_decided(self, event: FlowEvent) -> None:
        reason = event.reason
        if reason is None:
            return
        if reason is Reason.DEBUG_COMMAND:
            self._from_debug = True
        self._leg(self._leg_peer(event.peer)).on_decision(reason, event.peer, event.detail)

    def _leg_peer(self, decided_peer: str) -> str:
        """A decision about the origin itself (a backend's command answered by the proxy) ends its charger leg."""
        origin = self.origin
        return CHARGER if decided_peer == origin and origin != CHARGER else decided_peer

    def _leg(self, peer: str) -> Leg:
        leg = self._legs.get(peer)
        if leg is None:
            leg = self._legs[peer] = Leg(peer)
        return leg

    # --- derived fields -------------------------------------------------------------------

    def _unknown(self, leg: Leg, now: float | None) -> bool:
        if self.incomplete:
            return True
        return now is not None and leg.sent_at is not None and now - leg.sent_at > self.stale_after

    def _transaction_id(self) -> int | None:
        """The primary's transaction id: as the charger sees it, or as the primary assigned it to a start."""
        charger_leg = self._legs.get(CHARGER)
        charger_payload = self.payload if self.origin == CHARGER else charger_leg and charger_leg.payload
        tx = _int(transaction_id(charger_payload)) if charger_payload else None
        primary = self._legs.get(PRIMARY_NAME)
        if tx is None and self.action == "StartTransaction" and primary and primary.reply_payload:
            tx = _int(primary.reply_payload.get("transactionId"))
        return tx


def _value(outcome: Any) -> str | None:
    return None if outcome is None else outcome.value


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str(value: object) -> str | None:
    return value if isinstance(value, str) else None
