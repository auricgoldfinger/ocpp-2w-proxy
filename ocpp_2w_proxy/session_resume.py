"""Resume a session a secondary backend stopped, as a new session for the same card.

Some chargers cannot pause a transaction (a zero-current limit is undone within seconds),
so a control backend pauses by stopping the transaction instead. To resume it sends a
RemoteStartTransaction, which a secondary may never forward (Authorization Commands stay
with the primary). With `resume_own_stops` the proxy forwards it anyway, but only as a
resume: on the connector whose session this backend stopped, with the cable still in,
and with the idTag of that session, which the primary accepted. The charger authorizes
that idTag at the primary again (AuthorizeRemoteTxRequests), and the primary bills the
resume as a new session of its own.

Per connector the proxy keeps the last session the primary accepted and who stopped it
remotely. A new start replaces it; a cable unplug (Available) or a connector that can no
longer charge (Unavailable, Faulted) forgets it.
"""

from __future__ import annotations

import logging

from .ocpp import Call, CallResult, Reply, is_accepted
from .state import StateStore

logger = logging.getLogger(__name__)

# Connector states in which the stopped session's car is gone or cannot charge.
_SESSION_GONE_STATUSES = frozenset({"Available", "Unavailable", "Faulted"})


class ResumeTracker:
    def __init__(self, store: StateStore):
        self._store = store
        self._sessions = store.state.resumable_sessions

    # --- what the charger and the backends report -------------------------------------------

    def start_answered(self, start: Call, reply: Reply) -> None:
        """The primary answered a StartTransaction: an accepted one is the connector's session now."""
        connector = str(start.payload.get("connectorId"))
        payload = reply.payload if isinstance(reply, CallResult) else {}
        transaction_id = payload.get("transactionId")
        if isinstance(transaction_id, int) and payload.get("idTagInfo", {}).get("status") == "Accepted":
            self._sessions[connector] = {"transaction_id": transaction_id, "id_tag": start.payload.get("idTag")}
        elif self._sessions.pop(connector, None) is None:
            return
        self._store.save()

    def status_changed(self, status: Call) -> None:
        connector = str(status.payload.get("connectorId"))
        if status.payload.get("status") in _SESSION_GONE_STATUSES and self._sessions.pop(connector, None):
            self._store.save()

    def command_answered(self, backend_name: str, sent: Call, reply: Reply) -> None:
        """The charger accepted a RemoteStopTransaction: remember which backend stopped that session."""
        if sent.action != "RemoteStopTransaction" or not is_accepted(reply):
            return
        for session in self._sessions.values():
            if session["transaction_id"] == sent.payload.get("transactionId"):
                session["stopped_by"] = backend_name
                self._store.save()
                logger.info("transaction %s stopped by %s: it may resume it", session["transaction_id"], backend_name)

    # --- resuming ---------------------------------------------------------------------------

    def resume(self, call: Call, backend_name: str) -> Call | None:
        """The RemoteStartTransaction to forward for this backend's resume, or None if it is not one.

        Only connectorId and the stopped session's idTag go out: the backend's own idTag is
        not a card the primary knows, and a charging profile would override the schedule.
        """
        if call.action != "RemoteStartTransaction":
            return None
        connector = self._resumable_connector(call.payload.get("connectorId"), backend_name)
        if connector is None:
            return None
        session = self._sessions[connector]
        logger.info("%s resumes the session of transaction %s", backend_name, session["transaction_id"])
        return call.with_payload({"connectorId": int(connector), "idTag": session["id_tag"]})

    def _resumable_connector(self, requested: object, backend_name: str) -> str | None:
        """The connector to resume: the requested one, or without one the only connector this backend stopped."""
        stopped = [c for c, s in self._sessions.items() if s.get("stopped_by") == backend_name and s.get("id_tag")]
        if requested is None:
            return stopped[0] if len(stopped) == 1 else None
        return str(requested) if str(requested) in stopped else None
