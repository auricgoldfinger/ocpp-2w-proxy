"""Per-charger wiring: the durable state and the channels (primary + secondaries) sharing it.

The channels are owned here, at charger level, not per session: the primary and the
secondaries all outlive individual charger sessions and keep draining their queues.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .primary_channel import PrimaryChannel
from .secondary_channel import SecondaryChannel
from .state import StateStore
from .traffic_log import TrafficLog
from .transactions import TransactionMap


@dataclass(frozen=True)
class ChargerContext:
    """Everything the proxy owns per charger: one state file, one traffic log, one
    channel per backend."""

    primary: PrimaryChannel
    secondaries: Mapping[str, SecondaryChannel]
    store: StateStore
    transactions: TransactionMap
    traffic: TrafficLog
