"""The query string of ``GET /api/exchanges`` parsed into an ExchangeQuery; anything malformed is a 400.

An empty value means the filter is not set, so a form that submits blank fields asks for everything.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from typing import Any

from ..minihttp import HttpError
from ..reasons import Outcome
from .queries import ExchangeQuery

OUTCOMES = frozenset(outcome.value for outcome in Outcome)
PROBLEM_FLAGS = {"1": True, "0": False}
# What SQLite stores in an INTEGER column; a larger number would fail inside the query.
INTEGER = re.compile(r"-?[0-9]{1,19}")
SQLITE_INTEGER_RANGE = range(-(2**63), 2**63)


def parse_exchange_query(parameters: Mapping[str, str]) -> ExchangeQuery:
    unknown = parameters.keys() - _PARSERS.keys()
    if unknown:
        raise _bad(f"unknown query parameter {min(unknown)!r}")
    fields = {name: _PARSERS[name](name, value) for name, value in parameters.items() if value}
    return ExchangeQuery(**fields)


def _text(name: str, value: str) -> str:
    return value


def _outcome(name: str, value: str) -> str:
    if value not in OUTCOMES:
        raise _bad(f"{name} must be one of {', '.join(sorted(OUTCOMES))}")
    return value


def _problem(name: str, value: str) -> bool:
    if value not in PROBLEM_FLAGS:
        raise _bad(f"{name} must be 1 or 0")
    return PROBLEM_FLAGS[value]


def _integer(name: str, value: str) -> int:
    if not INTEGER.fullmatch(value) or int(value) not in SQLITE_INTEGER_RANGE:
        raise _bad(f"{name} must be an integer")
    return int(value)


def _limit(name: str, value: str) -> int:
    limit = _integer(name, value)
    if limit < 1:
        raise _bad(f"{name} must be at least 1")
    return limit  # the query clamps it to its maximum


def _epoch_seconds(name: str, value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        raise _bad(f"{name} must be epoch seconds") from None
    if not math.isfinite(seconds):
        raise _bad(f"{name} must be epoch seconds")
    return seconds


def _actions(name: str, value: str) -> tuple[str, ...]:
    return tuple(action for action in (part.strip() for part in value.split(",")) if action)


def _bad(message: str) -> HttpError:
    return HttpError(400, "bad_request", message)


_PARSERS: Mapping[str, Callable[[str, str], Any]] = {
    "charger": _text,
    "action": _text,
    "outcome": _outcome,
    "peer": _text,
    "problem": _problem,
    "tx": _integer,
    "q": _text,
    "exclude": _actions,
    "since": _epoch_seconds,
    "before": _text,  # a malformed cursor surfaces as InvalidCursor when the query runs
    "limit": _limit,
}
