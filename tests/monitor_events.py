"""Builders for monitor tests: flow events, stored exchanges and the stored scenarios several tests share."""

from __future__ import annotations

from ocpp_2w_proxy.monitor import db
from ocpp_2w_proxy.monitor.events import EventKind, FlowEvent
from ocpp_2w_proxy.monitor.projection import ExchangeProjection
from ocpp_2w_proxy.monitor.rows import ExchangeRow, WriteBatch, decision_row, frame_row
from ocpp_2w_proxy.ocpp import Call, CallError, CallResult
from ocpp_2w_proxy.reasons import Reason

CP = "cp"
METER = {
    "connectorId": 1,
    "transactionId": 10216,
    "meterValue": [{"sampledValue": [{"value": "1241921", "unit": "Wh"}]}],
}


def received(at, peer, message, charger=CP):
    return FlowEvent(EventKind.RECEIVED, at, charger, peer, message, None, None)


def sent(at, peer, message, charger=CP):
    return FlowEvent(EventKind.SENT, at, charger, peer, message, None, None)


def decided(at, peer, reason, detail=None, charger=CP):
    return FlowEvent(EventKind.DECIDED, at, charger, peer, None, reason, detail)


def link(at, peer, up, charger=CP):
    return FlowEvent(EventKind.LINK, at, charger, peer, None, None, up)


def exchange_row(exchange_id=1, **changes):
    row = ExchangeRow(
        exchange_id=exchange_id,
        charger_id=CP,
        origin="charger",
        action="StopTransaction",
        started_at=10.0,
        last_at=10.1,
        outcome="queued",
        problem=False,
        incomplete=False,
        transaction_id=10216,
        connector_id=1,
        id_tag=None,
        not_sent=("stats",),
        headline="1.0 kWh",
    )
    return row._replace(**changes)


def store(conn, exchange_id, events, not_sent=()):
    """What the writer does: project, encode the frames against the origin payload, write one batch."""
    projection = ExchangeProjection(exchange_id)
    projection.not_sent = not_sent
    frames, decisions, origin = [], [], None
    for event in events:
        projection.apply(event)
        if event.kind is EventKind.DECIDED:
            decisions.append(decision_row(exchange_id, event))
            continue
        frames.append(frame_row(exchange_id, event, origin))
        if origin is None and isinstance(event.message, Call):
            origin = event.message.payload
    exchange = ExchangeRow.from_summary(projection.summary())
    db.write_batch(conn, WriteBatch(exchanges=[exchange], frames=frames, decisions=decisions))


def meter_values(at, exchange_id, charger=CP):
    call = Call(f"mv{exchange_id}", "MeterValues", METER)
    return [
        received(at, "charger", call, charger),
        sent(at + 0.01, "primary", call, charger),
        sent(at + 0.02, "ha", Call(f"ha{exchange_id}", "MeterValues", {**METER, "transactionId": 77}), charger),
        received(at + 0.03, "primary", CallResult(call.id, {}), charger),
    ]


def start(at):
    call = Call("st", "StartTransaction", {"connectorId": 1, "idTag": "***4F2A", "meterStart": 1234500})
    return [
        received(at, "charger", call),
        sent(at + 0.01, "primary", call),
        received(at + 0.02, "primary", CallResult("st", {"transactionId": 10216, "idTagInfo": {}})),
    ]


def stop(at):
    call = Call("sp", "StopTransaction", {"transactionId": 10216, "meterStop": 1241921, "reason": "Local"})
    return [received(at, "charger", call), decided(at + 0.01, "primary", Reason.QUEUE_OVERFLOW)]


def blocked(at):
    call = Call("cmd-1", "SetChargingProfile", {"connectorId": 1, "csChargingProfiles": {}})
    return [
        received(at, "ha", call),
        decided(at, "ha", Reason.POLICY_ANSWERED),
        sent(at, "ha", CallResult("cmd-1", {"status": "Accepted"})),
    ]


def refused(at):
    call = Call("cmd-2", "ChangeConfiguration", {"key": "x", "value": "y"})
    return [
        received(at, "ha", call),
        decided(at, "ha", Reason.POLICY_REFUSED),
        sent(at, "ha", CallError("cmd-2", "NotSupported", "no")),
    ]


def heartbeat(at, exchange_id):
    call = Call(f"hb{exchange_id}", "Heartbeat", {})
    return [received(at, "charger", call), sent(at, "primary", call)]
