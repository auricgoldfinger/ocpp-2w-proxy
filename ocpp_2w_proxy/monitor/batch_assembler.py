"""Folds the raw events of one writer batch into exchange projections and the rows to store (pure).

The writer drives it in slices, in two passes over the batch:

1. ``correlate`` every event to its exchange. Calls carry their exchange id; a reply is matched to the Call it
   answers, and becomes an orphan exchange when that Call is unknown. Ids that are neither in memory nor new are
   collected, so the writer can rebuild those exchanges from the store (``adopt``) before folding.
2. ``fold`` every event, in this order: **redact** (with the exchange's action, so only after correlation; an
   orphan's reply and every CallError's details are redacted as unknown, so redaction fails closed), derive
   **not_sent** once on the first sight of the exchange, **project**, and add the frame or decision row. A leg
   payload equal to the origin's (``is``, then ``==``) is neither redacted again nor stored.

Then ``summarize`` (or only ``record``) each changed exchange and ``take_batch``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import NamedTuple

from ..ocpp import Call, CallError, CallResult, Message, next_exchange_id
from ..reasons import Reason
from ..redact import redact_message_payload
from .correlator import Correlator
from .events import EventKind, FlowEvent
from .gaps import ExchangeSpan, Gap, incomplete_exchanges
from .not_sent import NotSentMap
from .open_exchanges import OpenExchanges, TrackedExchange
from .projection import DEFAULT_STALE_AFTER, ExchangeProjection
from .rows import DecisionRow, ExchangeRow, FrameRow, LinkRow, WriteBatch, decision_row, frame_row


class Routed(NamedTuple):
    event: FlowEvent
    exchange_id: int | None  # None for a link change, which belongs to no exchange
    orphan: bool = False


class BatchAssembler:
    def __init__(
        self,
        not_sent: NotSentMap,
        *,
        exchanges: OpenExchanges | None = None,
        reply_window: float = DEFAULT_STALE_AFTER,
        new_exchange_id: Callable[[], int] = next_exchange_id,
    ):
        self._not_sent = not_sent
        self._exchanges = OpenExchanges() if exchanges is None else exchanges
        # How long after a monitor gap an orphan reply may still answer a Call the gap swallowed.
        self._reply_window = reply_window
        self._new_exchange_id = new_exchange_id
        self._outbound = Correlator()  # Calls the proxy sent; the peer's reply resolves them
        self._inbound = Correlator()  # Calls the proxy received; its own reply resolves them
        self._recent_gaps: list[Gap] = []
        self._correlators: dict[EventKind, Callable[[FlowEvent], Routed]] = {
            EventKind.RECEIVED: self._correlate_received,
            EventKind.SENT: self._correlate_sent,
            EventKind.DECIDED: self._correlate_decided,
            EventKind.LINK: self._correlate_link,
        }
        self._start_batch()

    # --- pass 1: correlate ------------------------------------------------------------------

    def correlate(self, raw: tuple) -> Routed:
        """``raw`` is a recorder tuple in ``FlowEvent`` field order."""
        event = FlowEvent._make(raw)
        routed = self._correlators[event.kind](event)
        self._note_whether_known(routed)
        return routed

    def take_unknown(self) -> set[int]:
        """Exchanges this batch continues that are not in memory: a restart or an eviction came in between."""
        unknown, self._unknown = self._unknown, set()
        self._fresh = set()
        return unknown

    def adopt(self, projections: Iterable[ExchangeProjection], now: float) -> None:
        """Projections rebuilt from the store continue in memory; their first event was folded long ago."""
        for projection in projections:
            self._exchanges.touch(TrackedExchange(projection, fresh=False), now)

    # --- pass 2: fold -----------------------------------------------------------------------

    def fold(self, routed: Routed) -> None:
        event, exchange_id = routed.event, routed.exchange_id
        if exchange_id is None:
            up, note = event.detail
            self._links.append(LinkRow(event.charger_id, event.peer, event.at, up, note))
            return
        tracked = self._track(routed)
        projection = tracked.projection
        origin = projection.payload
        event = self._redacted(tracked, event)
        if tracked.fresh:
            tracked.fresh = False
            self._derive_not_sent(projection, event)
        projection.apply(event)
        if event.kind is EventKind.DECIDED:
            self._decisions.append(decision_row(exchange_id, event))
        else:
            self._frames.append(frame_row(exchange_id, event, origin))
        self._exchanges.touch(tracked, event.at)
        self._changed[exchange_id] = tracked

    # --- monitor gaps -----------------------------------------------------------------------

    def record_gaps(self, gaps: Sequence[Gap], now: float) -> None:
        """Store the gaps; orphans folded later still check them, within the reply window."""
        self._gaps.extend(gaps)
        self._recent_gaps = [gap for gap in (*self._recent_gaps, *gaps) if gap.to_at + self._reply_window >= now]

    def tracked(self) -> list[TrackedExchange]:
        return list(self._exchanges)

    def mark_incomplete(self, gaps: Sequence[Gap], exchanges: Sequence[TrackedExchange]) -> None:
        """Flag the ``exchanges`` (a slice of ``tracked()``) the ``gaps`` may have taken events from."""
        by_id = {tracked.exchange_id: tracked for tracked in exchanges}
        spans = [tracked.span() for tracked in exchanges]
        for gap in gaps:
            for exchange_id in incomplete_exchanges(gap, spans, self._reply_window):
                tracked = by_id[exchange_id]
                tracked.projection.incomplete = True
                self._changed[exchange_id] = tracked

    # --- the batch --------------------------------------------------------------------------

    def changed(self) -> list[TrackedExchange]:
        return list(self._changed.values())

    def summarize(self, tracked: TrackedExchange) -> dict:
        """The exchange's summary, for the live stream; its row joins the batch."""
        summary = tracked.projection.summary()
        self._exchange_rows.append(ExchangeRow.from_summary(summary))
        return summary

    def record(self, tracked: TrackedExchange) -> None:
        """The exchange's row joins the batch; for when nobody follows the live stream, so no summary is needed."""
        self._exchange_rows.append(ExchangeRow.from_summary(tracked.projection.overview()))

    def take_batch(self) -> WriteBatch:
        batch = WriteBatch(self._exchange_rows, self._frames, self._decisions, self._links, self._gaps)
        self._start_batch()
        return batch

    def evict(self, now: float) -> None:
        self._exchanges.evict(now)

    def _start_batch(self) -> None:
        self._changed: dict[int, TrackedExchange] = {}
        self._exchange_rows: list[ExchangeRow] = []
        self._frames: list[FrameRow] = []
        self._decisions: list[DecisionRow] = []
        self._links: list[LinkRow] = []
        self._gaps: list[Gap] = []
        self._fresh: set[int] = set()
        self._unknown: set[int] = set()

    # --- correlation by event kind ------------------------------------------------------------

    def _correlate_received(self, event: FlowEvent) -> Routed:
        message = event.message
        if isinstance(message, Call):
            self._inbound.register_sent(event.charger_id, event.peer, message.id, message.exchange_id, event.at)
            return Routed(event, message.exchange_id)
        return self._correlate_reply(event, self._outbound)

    def _correlate_sent(self, event: FlowEvent) -> Routed:
        message = event.message
        if isinstance(message, Call):
            self._outbound.register_sent(event.charger_id, event.peer, message.id, message.exchange_id, event.at)
            return Routed(event, message.exchange_id)
        return self._correlate_reply(event, self._inbound)

    def _correlate_reply(self, event: FlowEvent, correlator: Correlator) -> Routed:
        reply: Message = event.message  # type: ignore[assignment]
        resolved = correlator.resolve_reply(event.charger_id, event.peer, reply.id, event.at)
        if resolved is None:
            return Routed(event, self._new_exchange_id(), orphan=True)
        return Routed(event, resolved[0])

    def _correlate_decided(self, event: FlowEvent) -> Routed:
        call: Call = event.message  # type: ignore[assignment]
        if event.reason is Reason.CHARGER_NO_REPLY:
            self._outbound.mark_timed_out(event.charger_id, event.peer, call.id)
        return Routed(event, call.exchange_id)

    def _correlate_link(self, event: FlowEvent) -> Routed:
        up, _ = event.detail
        if not up:
            self._outbound.link_down(event.charger_id, event.peer)
            self._inbound.link_down(event.charger_id, event.peer)
        return Routed(event, None)

    def _note_whether_known(self, routed: Routed) -> None:
        """A received Call starts a new exchange; any other event of an id not in memory continues a stored one."""
        exchange_id = routed.exchange_id
        if exchange_id is None or routed.orphan or exchange_id in self._fresh or exchange_id in self._exchanges:
            return
        event = routed.event
        if event.kind is EventKind.RECEIVED and isinstance(event.message, Call):
            self._fresh.add(exchange_id)
        else:
            self._unknown.add(exchange_id)

    # --- folding steps ------------------------------------------------------------------------

    def _track(self, routed: Routed) -> TrackedExchange:
        assert routed.exchange_id is not None
        tracked = self._exchanges.get(routed.exchange_id)
        if tracked is None:
            tracked = TrackedExchange(ExchangeProjection(routed.exchange_id))
            if routed.orphan:
                tracked.projection.incomplete = self._in_recent_gap(routed)
        return tracked

    def _in_recent_gap(self, routed: Routed) -> bool:
        event = routed.event
        span = ExchangeSpan(routed.exchange_id or 0, event.charger_id, event.at, event.at, open=False, orphan=True)
        return any(incomplete_exchanges(gap, [span], self._reply_window) for gap in self._recent_gaps)

    def _redacted(self, tracked: TrackedExchange, event: FlowEvent) -> FlowEvent:
        """The frame as it may be stored; a decision is kept as is, since neither its row nor its leg reads the Call."""
        message = event.message
        if event.kind is EventKind.DECIDED:
            return event
        match message:
            case Call():
                redacted: Message = self._redacted_call(tracked, message)
            case CallResult(id=wire_id, payload=payload):
                safe = redact_message_payload(tracked.projection.action, payload)
                redacted = message if safe is payload else CallResult(wire_id, safe)
            case CallError(id=wire_id, code=code, description=description, details=details):
                redacted = CallError(wire_id, code, description, redact_message_payload(None, details))
            case _:
                return event
        return event if redacted is message else event._replace(message=redacted)

    def _redacted_call(self, tracked: TrackedExchange, call: Call) -> Call:
        raw = call.payload
        origin = tracked.projection.payload
        raw_origin = tracked.raw_origin
        if origin is None:  # this Call is the origin
            tracked.raw_origin = raw
            safe = redact_message_payload(call.action, raw)
        elif raw_origin is not None and (raw is raw_origin or raw == raw_origin):
            safe = origin  # unchanged leg: reuse the redacted origin, which also marks it as not stored
        else:
            safe = redact_message_payload(call.action, raw)
        return call if safe is raw else Call(call.id, call.action, safe, exchange_id=call.exchange_id)  # type: ignore[arg-type]

    def _derive_not_sent(self, projection: ExchangeProjection, event: FlowEvent) -> None:
        """Only an exchange first seen as a Call received from the charger has a not_sent list."""
        message = event.message
        if event.kind is EventKind.RECEIVED and isinstance(message, Call):
            projection.not_sent = self._not_sent.for_exchange(event.charger_id, event.peer, message.action)
