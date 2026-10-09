"""Load and validate the TOML configuration. Secrets are read from environment variables."""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from .ocpp import OCPP_ACTIONS
from .policy import (
    AUTHORIZATION_ACTIONS,
    AUTHORIZATION_CONFIG_KEYS,
    EXCLUSIVE_ACTIONS,
    PRIMARY_DEFAULT_RULE,
    PRIMARY_DEFAULT_RULES,
    SECONDARY_DEFAULT_RULE,
    SECONDARY_DEFAULT_RULES,
    CommandPolicy,
    Rule,
)

CHARGER_ID_PATTERN = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")

PRIMARY_NAME = "primary"
# NFR-004: every durable queue holds at least this many messages, so a small value cannot
# quietly turn an ordinary backend outage into lost billing data.
MIN_QUEUE = 10_000
DEFAULT_OUTAGE_GRACE = 30.0

DEFAULT_SECONDARY_FORWARD_ACTIONS = [
    "BootNotification",
    "Heartbeat",
    "StatusNotification",
    "Authorize",
    "StartTransaction",
    "StopTransaction",
    "MeterValues",
]

# Known keys per configuration section: a typo must fail loudly, not silently never match.
TOP_LEVEL_KEYS = frozenset({"proxy", "logging", "chargers", "primary", "secondary"})
PROXY_KEYS = frozenset({"listen", "port", "state_dir", "tls_cert", "tls_key", "ping_interval", "ping_timeout"})
LOGGING_KEYS = frozenset({"level", "log_payloads"})
CHARGER_KEYS = frozenset({"id", "password_env", "primary_id", "secondary_ids"})
BACKEND_KEYS = frozenset({"url", "auth", "password_env", "call_timeout", "max_queue", "policy"})
PRIMARY_KEYS = BACKEND_KEYS | {"outage_grace"}
SECONDARY_KEYS = BACKEND_KEYS | {"name", "forward_actions"}
POLICY_KEYS = frozenset({"actions", "default", "change_configuration_allow_keys", "strip_charging_profile"})


class ConfigError(ValueError):
    pass


class AuthMode(StrEnum):
    NONE = "none"  # no Authorization header upstream
    FORWARD = "forward"  # pass the charger's own Authorization header through unchanged
    BASIC = "basic"  # Basic auth: backend charger id as username, configured password


@dataclass(frozen=True)
class ProxyConfig:
    listen: str
    port: int
    state_dir: Path
    tls_cert: Path | None
    tls_key: Path | None
    ping_interval: float
    ping_timeout: float
    log_level: str
    log_payloads: bool


@dataclass(frozen=True)
class ChargerConfig:
    id: str
    password: str | None
    primary_id: str
    # Backend name -> the id that backend knows this charger by (defaults to the charger id).
    secondary_ids: Mapping[str, str]


@dataclass(frozen=True)
class BackendConfig:
    name: str
    url: str
    auth: AuthMode
    password: str | None
    policy: CommandPolicy
    call_timeout: float
    max_queue: int = MIN_QUEUE


@dataclass(frozen=True)
class PrimaryConfig(BackendConfig):
    # Seconds a primary outage stays hidden from the charger before its connection is closed.
    outage_grace: float = DEFAULT_OUTAGE_GRACE


@dataclass(frozen=True)
class SecondaryConfig(BackendConfig):
    forward_actions: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Config:
    proxy: ProxyConfig
    chargers: Mapping[str, ChargerConfig]
    primary: PrimaryConfig
    secondaries: tuple[SecondaryConfig, ...]


def load(path: Path, environ: Mapping[str, str] = os.environ) -> Config:
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    return parse(raw, environ)


def _reject_unknown_keys(section: Mapping[str, Any], known: frozenset[str], where: str) -> None:
    unknown = set(section) - known
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}")


def _number(
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


def _flag(section: Mapping[str, Any], key: str, where: str) -> bool:
    value = section.get(key, False)
    if not isinstance(value, bool):
        raise ConfigError(f"{where}: {key} must be true or false, not {value!r}")
    return value


def parse(raw: Mapping[str, Any], environ: Mapping[str, str] = os.environ) -> Config:
    _reject_unknown_keys(raw, TOP_LEVEL_KEYS, "configuration")
    secrets = _Secrets(environ)
    chargers = _parse_chargers(raw.get("chargers", []), secrets)
    if "primary" not in raw:
        raise ConfigError("[primary] backend is required")
    primary = _parse_primary(raw["primary"], secrets)
    secondaries = _parse_secondaries(raw.get("secondary"), secrets)
    _validate_charger_backend_ids(chargers, secondaries)
    _validate_command_assignments(primary, secondaries)
    primary = _withhold_secondary_keys(primary, secondaries)
    return Config(_parse_proxy(raw.get("proxy", {}), raw.get("logging", {})), chargers, primary, secondaries)


def _parse_secondaries(entries: Any, secrets: _Secrets) -> tuple[SecondaryConfig, ...]:
    if entries is None:
        return ()
    if isinstance(entries, Mapping):
        raise ConfigError("[secondary] entries are [[secondary]] array-of-tables, each with a unique name")
    secondaries: list[SecondaryConfig] = []
    names: set[str] = set()
    for section in entries:
        if not isinstance(section, Mapping):
            raise ConfigError("[[secondary]] entries must be tables")
        name = _charger_id(section.get("name"), "[[secondary]] name")
        if name == PRIMARY_NAME:
            raise ConfigError(f"secondary backend name {name!r} is reserved for the primary backend")
        if name in names:
            raise ConfigError(f"duplicate secondary backend name {name!r}")
        names.add(name)
        secondaries.append(_parse_secondary(name, section, secrets))
    return tuple(secondaries)


def _validate_charger_backend_ids(
    chargers: Mapping[str, ChargerConfig], secondaries: Sequence[SecondaryConfig]
) -> None:
    names = {backend.name for backend in secondaries}
    for charger in chargers.values():
        unknown = set(charger.secondary_ids) - names
        if unknown:
            raise ConfigError(
                f"charger {charger.id!r}: secondary_ids names backends that are not configured: {sorted(unknown)}"
            )


def _validate_command_assignments(primary: BackendConfig, secondaries: Sequence[SecondaryConfig]) -> None:
    """UC-001 step 3: refuse configurations that would send conflicting commands to a charger."""
    _validate_authorization_commands(secondaries)
    _validate_exclusive_commands(primary, secondaries)
    _validate_change_configuration_keys(primary, secondaries)


def _validate_authorization_commands(secondaries: Sequence[SecondaryConfig]) -> None:
    """BR-008/FR-019: Authorization Commands may only be forwarded by the primary backend."""
    for backend in secondaries:
        for action in sorted(AUTHORIZATION_ACTIONS):
            if backend.policy.rule_for(action) is Rule.FORWARD:
                raise ConfigError(
                    f"authorization command {action} may only be forwarded by the primary backend, "
                    f"but secondary backend {backend.name!r} forwards it"
                )
        if backend.policy.rule_for("ChangeConfiguration") is Rule.FORWARD:
            raise ConfigError(
                f"secondary backend {backend.name!r} may not forward all configuration changes; "
                "changes to the charger's authorization settings are reserved for the primary backend"
            )
        forbidden = backend.policy.change_configuration_allow_keys & AUTHORIZATION_CONFIG_KEYS
        if forbidden:
            raise ConfigError(
                f"secondary backend {backend.name!r} may not change authorization settings "
                f"{sorted(forbidden)}; those stay with the primary backend"
            )


def _validate_exclusive_commands(primary: BackendConfig, secondaries: Sequence[SecondaryConfig]) -> None:
    """BR-007/FR-008: each Exclusive Command is forwarded by at most one backend."""
    backends = [primary, *secondaries]
    for action in sorted(EXCLUSIVE_ACTIONS):
        forwarders = [backend for backend in backends if backend.policy.rule_for(action) is Rule.FORWARD]
        if len(forwarders) > 1:
            names = ", ".join(backend.name for backend in forwarders)
            raise ConfigError(f"exclusive command {action} is forwarded by more than one backend: {names}")


def _validate_change_configuration_keys(primary: BackendConfig, secondaries: Sequence[SecondaryConfig]) -> None:
    """BR-007: each configuration key is permitted for at most one backend. A backend that
    forwards all configuration changes (the primary) keeps every key nobody else owns."""
    owners: dict[str, str] = {}
    for backend in [primary, *secondaries]:
        for key in sorted(backend.policy.change_configuration_allow_keys):
            owner = owners.get(key)
            if owner is not None:
                raise ConfigError(f"configuration key {key!r} is permitted for both {owner!r} and {backend.name!r}")
            owners[key] = backend.name


def _withhold_secondary_keys(primary: PrimaryConfig, secondaries: Sequence[SecondaryConfig]) -> PrimaryConfig:
    """Keys a secondary owns are no longer the primary's to change: it answers them itself."""
    owned = frozenset().union(*(backend.policy.change_configuration_allow_keys for backend in secondaries))
    if not owned:
        return primary
    return replace(primary, policy=replace(primary.policy, withheld_configuration_keys=owned))


class _Secrets:
    def __init__(self, environ: Mapping[str, str]):
        self._environ = environ

    def get(self, section: Mapping[str, Any], where: str) -> str | None:
        name = section.get("password_env")
        if not name:
            return None
        value = self._environ.get(name)
        if not value:
            raise ConfigError(f"{where}: environment variable {name!r} is not set or empty")
        return value


LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def _parse_proxy(section: Mapping[str, Any], logging_section: Mapping[str, Any]) -> ProxyConfig:
    _reject_unknown_keys(section, PROXY_KEYS, "[proxy]")
    _reject_unknown_keys(logging_section, LOGGING_KEYS, "[logging]")
    tls_cert, tls_key = section.get("tls_cert") or None, section.get("tls_key") or None
    if bool(tls_cert) != bool(tls_key):
        raise ConfigError("[proxy] tls_cert and tls_key must be set together")
    log_level = str(logging_section.get("level", "INFO")).upper()
    if log_level not in LOG_LEVELS:
        raise ConfigError(f"[logging] level must be one of {', '.join(LOG_LEVELS)}, not {log_level!r}")
    return ProxyConfig(
        listen=str(section.get("listen", "0.0.0.0")),
        port=_number(section, "port", 8321, "[proxy]", int, 0, 65535),
        state_dir=Path(section.get("state_dir", "./state")),
        tls_cert=Path(tls_cert) if tls_cert else None,
        tls_key=Path(tls_key) if tls_key else None,
        ping_interval=_number(section, "ping_interval", 30, "[proxy]", float, 1),
        ping_timeout=_number(section, "ping_timeout", 60, "[proxy]", float, 1),
        log_level=log_level,
        log_payloads=_flag(logging_section, "log_payloads", "[logging]"),
    )


def _reject_inline_password(section: Mapping[str, Any], where: str) -> None:
    if "password" in section:
        raise ConfigError(f"{where}: passwords are never stored in the configuration file; use password_env")


def _parse_chargers(entries: Any, secrets: _Secrets) -> dict[str, ChargerConfig]:
    if isinstance(entries, Mapping):
        raise ConfigError("[chargers] entries are [[chargers]] array-of-tables, not a table")
    if not entries:
        raise ConfigError("at least one [[chargers]] entry is required (allowlist)")
    chargers: dict[str, ChargerConfig] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ConfigError("[[chargers]] entries must be tables")
        charger_id = _charger_id(entry.get("id"), "[[chargers]] id")
        if charger_id in chargers:
            raise ConfigError(f"duplicate charger id {charger_id!r}")
        _reject_inline_password(entry, f"charger {charger_id!r}")
        _reject_unknown_keys(entry, CHARGER_KEYS, f"charger {charger_id!r}")
        chargers[charger_id] = ChargerConfig(
            id=charger_id,
            password=secrets.get(entry, f"charger {charger_id}"),
            primary_id=_charger_id(entry.get("primary_id", charger_id), "primary_id"),
            secondary_ids=_parse_secondary_ids(entry, charger_id),
        )
    return chargers


def _parse_secondary_ids(entry: Mapping[str, Any], charger_id: str) -> dict[str, str]:
    raw_ids = entry.get("secondary_ids", {})
    if not isinstance(raw_ids, Mapping):
        raise ConfigError(f"charger {charger_id!r}: secondary_ids must be a table of backend name -> charger id")
    return {
        name: _charger_id(backend_id, f"charger {charger_id!r} secondary_ids[{name!r}]")
        for name, backend_id in raw_ids.items()
    }


def _charger_id(value: Any, where: str) -> str:
    if not isinstance(value, str) or not CHARGER_ID_PATTERN.match(value):
        raise ConfigError(f"{where}: {value!r} must match {CHARGER_ID_PATTERN.pattern}")
    return value


def _parse_backend(
    name: str,
    section: Mapping[str, Any],
    secrets: _Secrets,
    default_rules: Mapping[str, Rule],
    default_rule: Rule,
    known_keys: frozenset[str],
) -> BackendConfig:
    _reject_inline_password(section, f"[{name}]")
    _reject_unknown_keys(section, known_keys, f"[{name}]")
    url = section.get("url")
    if not isinstance(url, str) or not url.startswith(("ws://", "wss://")):
        raise ConfigError(f"[{name}] url must start with ws:// or wss://")
    try:
        auth = AuthMode(section.get("auth", AuthMode.NONE))
    except ValueError as exc:
        raise ConfigError(f"[{name}] auth must be one of {[m.value for m in AuthMode]}") from exc
    password = secrets.get(section, f"[{name}]")
    if auth is AuthMode.BASIC and not password:
        raise ConfigError(f"[{name}] auth = 'basic' requires password_env")
    return BackendConfig(
        name=name,
        url=url.rstrip("/"),
        auth=auth,
        password=password,
        policy=_parse_policy(name, section.get("policy", {}), default_rules, default_rule),
        call_timeout=_number(section, "call_timeout", 30, f"[{name}]", float, 1),
        max_queue=_number(section, "max_queue", MIN_QUEUE, f"[{name}]", int, MIN_QUEUE),
    )


def _parse_policy(
    name: str,
    section: Mapping[str, Any],
    default_rules: Mapping[str, Rule],
    default_rule: Rule,
) -> CommandPolicy:
    if not isinstance(section, Mapping):
        raise ConfigError(f"[{name}] policy must be a table")
    _reject_unknown_keys(section, POLICY_KEYS, f"[{name}.policy]")
    allow_keys = _lowercase_keys(section.get("change_configuration_allow_keys", []), name)
    actions = section.get("actions", {})
    if not isinstance(actions, Mapping):
        raise ConfigError(f"[{name}.policy] actions must be a table of action -> rule")
    unknown_actions = set(actions) - OCPP_ACTIONS
    if unknown_actions:
        raise ConfigError(
            f"[{name}.policy] unknown action(s) {sorted(unknown_actions)}; each must be an OCPP 1.6 action"
        )
    try:
        overrides = {action: Rule(rule) for action, rule in actions.items()}
        rules = {**default_rules, **overrides}
        return CommandPolicy(
            rules=rules,
            default_rule=Rule(section.get("default", default_rule)),
            change_configuration_allow_keys=allow_keys,
            strip_charging_profile=_flag(section, "strip_charging_profile", f"[{name}.policy]"),
        )
    except ValueError as exc:
        raise ConfigError(f"[{name}.policy] {exc}") from exc


def _lowercase_keys(keys: Any, backend_name: str) -> frozenset[str]:
    """OCPP configuration keys are case-insensitive CiStrings: normalize to lowercase
    so that case variations cannot slip past the authorization-key checks."""
    if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
        raise ConfigError(f"[{backend_name}.policy] change_configuration_allow_keys must be a list of key names")
    return frozenset(key.lower() for key in keys)


def _parse_primary(section: Mapping[str, Any], secrets: _Secrets) -> PrimaryConfig:
    base = _parse_backend(PRIMARY_NAME, section, secrets, PRIMARY_DEFAULT_RULES, PRIMARY_DEFAULT_RULE, PRIMARY_KEYS)
    return PrimaryConfig(
        **vars(base),
        outage_grace=_number(section, "outage_grace", DEFAULT_OUTAGE_GRACE, f"[{PRIMARY_NAME}]", float, 0),
    )


def _parse_secondary(name: str, section: Mapping[str, Any], secrets: _Secrets) -> SecondaryConfig:
    base = _parse_backend(name, section, secrets, SECONDARY_DEFAULT_RULES, SECONDARY_DEFAULT_RULE, SECONDARY_KEYS)
    if base.auth is AuthMode.FORWARD:
        raise ConfigError(f"[{name}] auth = 'forward' would leak the charger's credentials; use 'basic' or 'none'")
    return SecondaryConfig(
        **vars(base),
        forward_actions=_forward_actions(section, name),
    )


def _forward_actions(section: Mapping[str, Any], name: str) -> frozenset[str]:
    actions = section.get("forward_actions", DEFAULT_SECONDARY_FORWARD_ACTIONS)
    if not isinstance(actions, list) or not all(isinstance(action, str) for action in actions):
        raise ConfigError(f"[{name}] forward_actions must be a list of action names")
    unknown = set(actions) - OCPP_ACTIONS
    if unknown:
        raise ConfigError(
            f"[{name}] forward_actions has unknown action(s) {sorted(unknown)}; each must be an OCPP 1.6 action"
        )
    return frozenset(actions)
