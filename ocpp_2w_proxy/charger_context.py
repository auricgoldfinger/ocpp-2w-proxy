"""Per-charger wiring: the durable state and the channels (primary + secondaries) sharing it.

The channels are owned here, at charger level, not per session: the primary and the
secondaries all outlive individual charger sessions and keep draining their queues.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

from .primary_channel import PrimaryChannel
from .secondary_channel import SecondaryChannel
from .traffic_log import TrafficLog
from .transactions import TransactionMap


@dataclass(frozen=True)
class ChargerContext:
    """Everything the proxy owns per charger: its transaction map, one traffic log and
    one channel per backend."""

    primary: PrimaryChannel
    secondaries: Mapping[str, SecondaryChannel]
    transactions: TransactionMap
    traffic: TrafficLog

    def start_background(self) -> None:
        self.primary.start_background()
        for channel in self.secondaries.values():
            channel.start_background()

    async def close(self) -> None:
        await asyncio.gather(self.primary.close(), *(channel.close() for channel in self.secondaries.values()))
