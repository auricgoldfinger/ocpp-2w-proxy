"""Why a message did or did not reach a peer: the closed vocabulary of proxy decisions.

Every place where the proxy decides the fate of one leg of a message reports one Reason.
Each Reason belongs to exactly one Outcome and carries the human text the monitor shows.
"""

from __future__ import annotations

from enum import Enum, StrEnum


class Outcome(StrEnum):
    DELIVERED = "delivered"
    ANSWERED_BY_PROXY = "answered_by_proxy"
    QUEUED = "queued"
    WITHHELD = "withheld"
    DROPPED = "dropped"
    FAILED = "failed"


class Reason(Enum):
    """A proxy decision. Templates may use ``{peer}`` and ``{detail}``."""

    # session / commands
    POLICY_ANSWERED = (Outcome.ANSWERED_BY_PROXY, "Answered by the proxy for {peer}; the charger never saw it")
    POLICY_REFUSED = (Outcome.ANSWERED_BY_PROXY, "Refused by the proxy for {peer} by command policy")
    UNKNOWN_TRANSACTION = (Outcome.ANSWERED_BY_PROXY, "Refused: {peer} named an unknown transaction {detail}")
    CHARGER_DISCONNECTED = (Outcome.FAILED, "The charger is not connected")
    CHARGER_NO_REPLY = (Outcome.FAILED, "The charger did not reply in time")
    BACKEND_GONE_REPLY_LOST = (Outcome.DROPPED, "{peer} disconnected before the reply could be returned")
    MALFORMED_FRAME = (Outcome.FAILED, "Malformed frame from {peer}: {detail}")
    DEBUG_COMMAND = (Outcome.DELIVERED, "Sent from the debug endpoint")
    # withholding
    WITHHELD_UNAUTHORIZED = (Outcome.WITHHELD, "Withheld from {peer}: the idTag was not authorized")
    START_NOT_CONFIRMED = (Outcome.WITHHELD, "Withheld from {peer}: the primary did not accept the start")
    # primary
    QUEUED = (Outcome.QUEUED, "Queued for {peer} during an outage; acknowledged locally")
    KEPT_LATEST = (Outcome.QUEUED, "Kept as the latest status for {peer}; sent once the queue is empty")
    SUPERSEDED = (Outcome.DROPPED, "Replaced by a newer status before it was sent to {peer}")
    HEARTBEAT_LOCAL = (Outcome.ANSWERED_BY_PROXY, "Heartbeat answered locally while {peer} is unavailable")
    DROPPED_UNAVAILABLE = (Outcome.DROPPED, "Dropped: {peer} is unavailable and this message is not queued")
    START_REFUSED_CATCHING_UP = (Outcome.ANSWERED_BY_PROXY, "Start refused while the queue for {peer} drains")
    BOOT_FROM_REPLAY = (Outcome.ANSWERED_BY_PROXY, "Boot answered from the last reply of {peer}")
    PRIMARY_UNAVAILABLE_CLOSE = (Outcome.FAILED, "{peer} unavailable too long; charger connection closed")
    QUEUED_REJECTED = (Outcome.FAILED, "{peer} rejected a queued message: {detail}")
    # shared
    QUEUE_OVERFLOW = (Outcome.DROPPED, "Dropped: the queue for {peer} is full")
    # secondary
    SKIPPED_OFFLINE = (Outcome.DROPPED, "Not sent: {peer} is offline and this message is live only")
    TRANSIENT_DROPPED = (Outcome.DROPPED, "Live message dropped while {peer} reconnected")
    NO_LONGER_FORWARDED = (Outcome.DROPPED, "Dropped: {peer} is no longer configured for this action")
    NO_TRANSACTION_LINK = (Outcome.DROPPED, "Dropped: no transaction number of {peer} is known for {detail}")
    MAX_ATTEMPTS = (Outcome.DROPPED, "Dropped after too many failed attempts to {peer}")
    BOOT_FROM_CACHE = (Outcome.DELIVERED, "Boot information resent to {peer} from cache")

    def __init__(self, outcome: Outcome, template: str):
        self.outcome = outcome
        self.template = template

    @property
    def is_problem(self) -> bool:
        """A failure, or a loss that was not simply replaced by newer data."""
        return self.outcome is Outcome.FAILED or (self.outcome is Outcome.DROPPED and self is not Reason.SUPERSEDED)

    def describe(self, peer: str, detail: object = None) -> str:
        return self.template.format(peer=peer, detail="" if detail is None else detail)
