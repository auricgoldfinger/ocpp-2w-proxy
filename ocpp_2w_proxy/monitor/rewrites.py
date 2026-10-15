"""The rewrite chips of a leg: which tracked fields its payload changed relative to the origin payload."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

type Payload = Mapping[str, Any]

_ABSENT = object()


def _id_tag(payload: Payload) -> object:
    return payload.get("idTag", _ABSENT)


def _transaction_id(payload: Payload) -> object:
    """The top-level transactionId, or the one a SetChargingProfile's TxProfile names."""
    if "transactionId" in payload:
        return payload["transactionId"]
    profile = payload.get("csChargingProfiles")
    if isinstance(profile, Mapping):
        return profile.get("transactionId", _ABSENT)
    return _ABSENT


def transaction_id(payload: Payload) -> object | None:
    value = _transaction_id(payload)
    return None if value is _ABSENT else value


def _charging_profile(payload: Payload) -> object:
    """A RemoteStartTransaction's embedded profile, identified by its id when it has one."""
    profile = payload.get("chargingProfile", _ABSENT)
    if isinstance(profile, Mapping):
        return profile.get("chargingProfileId", "present")
    return profile


TRACKED_FIELDS: Mapping[str, Callable[[Payload], object]] = {
    "id": _id_tag,
    "tx": _transaction_id,
    "profile": _charging_profile,
}


def rewritten(origin: Payload, leg: Payload) -> list[dict[str, Any]]:
    """``[{"field", "from", "to"}]``; ``to`` is None when the field was removed. Empty for an equal copy."""
    if leg is origin or leg == origin:
        return []
    chips = []
    for field, read in TRACKED_FIELDS.items():
        before, after = read(origin), read(leg)
        if before != after:
            chips.append({"field": field, "from": _value(before), "to": _value(after)})
    return chips


def _value(value: object) -> object:
    return None if value is _ABSENT else value
