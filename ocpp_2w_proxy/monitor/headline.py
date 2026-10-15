"""The one-line headline the exchange list shows per action: a dispatch table of pure payload readers.

Each reader returns None when the data it needs is missing or malformed; none of them raises.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .meter_values import ENERGY, POWER, latest_reading, number

SEPARATOR = " · "

type Payload = dict[str, Any]


def headline(action: str | None, payload: Any) -> str | None:
    reader = HEADLINES.get(action) if action else None
    if reader is None or not isinstance(payload, dict):
        return None
    return reader(payload)


def _join(*parts: str | None) -> str | None:
    present = [part for part in parts if part]
    return SEPARATOR.join(present) if present else None


def _text(payload: Payload, key: str) -> str | None:
    value = payload.get(key)
    return value.strip() or None if isinstance(value, str) else None


def _kwh(watt_hours: float | None) -> str | None:
    return None if watt_hours is None else f"{watt_hours / 1000:.1f} kWh"


def _meter_values(payload: Payload) -> str | None:
    power = latest_reading(payload, POWER)
    return _join(None if power is None else f"{power / 1000:.1f} kW", _kwh(latest_reading(payload, ENERGY)))


def _status_notification(payload: Payload) -> str | None:
    error_code = _text(payload, "errorCode")
    return _join(_text(payload, "status"), None if error_code == "NoError" else error_code)


def _start_transaction(payload: Payload) -> str | None:
    return _kwh(number(payload.get("meterStart")))


def _stop_transaction(payload: Payload) -> str | None:
    return _join(_kwh(number(payload.get("meterStop"))), _text(payload, "reason"))


def _authorize(payload: Payload) -> str | None:
    return _text(payload, "idTag")


def _boot_notification(payload: Payload) -> str | None:
    names = (_text(payload, "chargePointVendor"), _text(payload, "chargePointModel"))
    return _join(" ".join(name for name in names if name), _text(payload, "firmwareVersion"))


def _heartbeat(payload: Payload) -> str | None:
    """A Heartbeat Call is empty; its CallResult carries the backend's clock."""
    return _text(payload, "currentTime")


HEADLINES: Mapping[str, Callable[[Payload], str | None]] = {
    "MeterValues": _meter_values,
    "StatusNotification": _status_notification,
    "StartTransaction": _start_transaction,
    "StopTransaction": _stop_transaction,
    "Authorize": _authorize,
    "BootNotification": _boot_notification,
    "Heartbeat": _heartbeat,
}
