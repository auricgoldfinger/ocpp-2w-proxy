"""Which secondaries never receive a charger-originated action, per charger: a frozen, precomputed map."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from ..config import Config
from ..ocpp import OCPP_ACTIONS

CHARGER_ORIGIN = "charger"

type _ByAction = Mapping[str, tuple[str, ...]]


class NotSentMap:
    """charger id -> action -> the charger's secondaries whose ``forward_actions`` lack that action."""

    def __init__(self, by_charger: Mapping[str, _ByAction], all_secondaries: Mapping[str, tuple[str, ...]]):
        self._by_charger = by_charger
        # An action outside OCPP_ACTIONS is in no forward_actions, so every secondary of the charger skips it.
        self._all_secondaries = all_secondaries

    @classmethod
    def from_config(cls, config: Config) -> NotSentMap:
        forward_actions = {secondary.name: secondary.forward_actions for secondary in config.secondaries}
        by_charger: dict[str, _ByAction] = {}
        all_secondaries: dict[str, tuple[str, ...]] = {}
        for charger in config.chargers.values():
            secondaries = tuple(name for name in forward_actions if name in charger.secondary_ids)
            by_charger[charger.id] = MappingProxyType(
                {
                    action: tuple(name for name in secondaries if action not in forward_actions[name])
                    for action in OCPP_ACTIONS
                }
            )
            all_secondaries[charger.id] = secondaries
        return cls(MappingProxyType(by_charger), MappingProxyType(all_secondaries))

    def for_exchange(self, charger_id: str, origin: str, action: str) -> tuple[str, ...]:
        """Only charger-originated exchanges have a not_sent list; commands and proxy calls get ()."""
        if origin != CHARGER_ORIGIN:
            return ()
        by_action = self._by_charger.get(charger_id)
        if by_action is None:
            return ()
        return by_action.get(action, self._all_secondaries[charger_id])
