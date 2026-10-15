"""Reading key figures out of an OCPP 1.6 MeterValues payload, tolerating the sloppy data chargers send."""

from __future__ import annotations

from typing import Any

ENERGY = "Energy.Active.Import.Register"
POWER = "Power.Active.Import"

# OCPP 1.6 sampledValue defaults when the charger leaves the field out.
_DEFAULT_MEASURAND = ENERGY
_UNIT_FACTORS = {"Wh": 1.0, "kWh": 1000.0, "W": 1.0, "kW": 1000.0}
_DEFAULT_UNIT = {ENERGY: "Wh", POWER: "W"}


def number(value: Any) -> float | None:
    """A number, also from a numeric string with stray whitespace (``' 1.2'``); None for anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def latest_reading(payload: Any, measurand: str) -> float | None:
    """The newest total (phase-less, or the first phase when only phases exist) of ``measurand`` in Wh or W."""
    meter_values = payload.get("meterValue") if isinstance(payload, dict) else None
    if not isinstance(meter_values, list):
        return None
    for meter_value in reversed(meter_values):
        reading = _reading(meter_value, measurand)
        if reading is not None:
            return reading
    return None


def _reading(meter_value: Any, measurand: str) -> float | None:
    sampled = meter_value.get("sampledValue") if isinstance(meter_value, dict) else None
    if not isinstance(sampled, list):
        return None
    matching = [
        value
        for value in sampled
        if isinstance(value, dict) and value.get("measurand", _DEFAULT_MEASURAND) == measurand
    ]
    totals = [value for value in matching if "phase" not in value] or matching
    for value in totals:
        reading = _in_base_unit(value, measurand)
        if reading is not None:
            return reading
    return None


def _in_base_unit(sampled_value: dict[str, Any], measurand: str) -> float | None:
    amount = number(sampled_value.get("value"))
    factor = _UNIT_FACTORS.get(sampled_value.get("unit", _DEFAULT_UNIT.get(measurand, "")))
    if amount is None or factor is None:
        return None
    return amount * factor
