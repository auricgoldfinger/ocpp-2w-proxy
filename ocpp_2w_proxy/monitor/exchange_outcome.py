"""The exchange-level verdicts derived from its legs: the overall outcome and the problem flag (pure)."""

from __future__ import annotations

from collections.abc import Iterable

from ..reasons import Outcome
from .legs import Leg

# Worst first. An open leg (None) outranks the benign endings, so an exchange shows as in progress until
# its legs settle, unless a leg has already ended badly.
_SEVERITY: tuple[Outcome | None, ...] = (
    Outcome.FAILED,
    Outcome.DROPPED,
    Outcome.WITHHELD,
    Outcome.QUEUED,
    None,
    Outcome.ANSWERED_BY_PROXY,
    Outcome.DELIVERED,
)
_RANK = {outcome: rank for rank, outcome in enumerate(_SEVERITY)}


def overall_outcome(legs: Iterable[Leg]) -> Outcome | None:
    """The worst leg outcome; None for an exchange without legs or with an open leg and nothing worse."""
    return min((leg.outcome for leg in legs), key=_RANK.__getitem__, default=None)


def is_problem(legs: Iterable[Leg]) -> bool:
    """A leg ended in a problem reason, or its peer replied with a CallError. Monitor gaps never count."""
    return any((leg.reason is not None and leg.reason.is_problem) or leg.peer_error for leg in legs)
