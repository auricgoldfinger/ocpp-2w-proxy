"""Per-charger wiring: the durable state and the primary channel sharing it."""

from __future__ import annotations

from dataclasses import dataclass

from .primary_channel import PrimaryChannel
from .state import StateStore
from .traffic_log import TrafficLog
from .transactions import TransactionMap


@dataclass(frozen=True)
class ChargerContext:
    """Everything the proxy owns per charger: one state file, one traffic log, one channel."""

    primary: PrimaryChannel
    store: StateStore
    transactions: TransactionMap
    traffic: TrafficLog
