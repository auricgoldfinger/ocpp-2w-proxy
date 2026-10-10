"""Typed accessors shared by the configuration sections."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .config_error import ConfigError


def reject_unknown_keys(section: Mapping[str, Any], known: frozenset[str], where: str) -> None:
    unknown = set(section) - known
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}")


def number(
    section: Mapping[str, Any],
    key: str,
    default: Any,
    where: str,
    cast: type,
    minimum: float | None = None,
    maximum: float | None = None,
) -> Any:
    value = section.get(key, default)
    try:
        if isinstance(value, bool):
            raise TypeError
        number = cast(value)
    except TypeError, ValueError:
        raise ConfigError(f"{where}: {key} must be a number, not {value!r}") from None
    if (minimum is not None and number < minimum) or (maximum is not None and number > maximum):
        raise ConfigError(f"{where}: {key} must be {_bounds_text(minimum, maximum)}, not {value!r}")
    return number


def _bounds_text(minimum: float | None, maximum: float | None) -> str:
    if minimum is not None and maximum is not None:
        return f"between {minimum} and {maximum}"
    if minimum is not None:
        return f"at least {minimum}"
    return f"at most {maximum}"


def flag(section: Mapping[str, Any], key: str, where: str) -> bool:
    value = section.get(key, False)
    if not isinstance(value, bool):
        raise ConfigError(f"{where}: {key} must be true or false, not {value!r}")
    return value
