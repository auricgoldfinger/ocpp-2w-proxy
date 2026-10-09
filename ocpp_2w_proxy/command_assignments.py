"""Cross-backend rules for who may command the charger (UC-001 step 3, BR-007, BR-008).

Applied to the parsed configuration: refuses setups that would send conflicting commands
to one charger, and hands ownership of configuration keys from the primary to the
secondary that claims them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from .config_error import ConfigError
from .policy import AUTHORIZATION_ACTIONS, AUTHORIZATION_CONFIG_KEYS, EXCLUSIVE_ACTIONS, Rule

if TYPE_CHECKING:
    from .config import BackendConfig, PrimaryConfig, SecondaryConfig


def validate_command_assignments(primary: BackendConfig, secondaries: Sequence[SecondaryConfig]) -> None:
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


def withhold_secondary_keys(primary: PrimaryConfig, secondaries: Sequence[SecondaryConfig]) -> PrimaryConfig:
    """Keys a secondary owns are no longer the primary's to change: it answers them itself."""
    owned = frozenset().union(*(backend.policy.change_configuration_allow_keys for backend in secondaries))
    if not owned:
        return primary
    return replace(primary, policy=replace(primary.policy, withheld_configuration_keys=owned))
