"""The live part of ``/api/links``, taken on the loop straight from the channels and the monitor's own counters.

Link events can be dropped under load, so the link state shown live is read from the channels themselves, never
reconstructed from events. The proxy is reached through ``ProxyState`` so the monitor never imports the server.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from ..charger_context import ChargerContext
from ..flow import FanOut, FlowRecorder
from .recorder_hub import RecorderHub
from .writer import MonitorWriter


class ProxyState(Protocol):
    def charger_ids(self) -> tuple[str, ...]: ...

    def charger_connected_since(self, charger_id: str) -> float | None:
        """When the charger's current session started; None while it is not connected."""
        ...

    def context_for(self, charger_id: str) -> ChargerContext: ...


class Link(Protocol):
    """What a channel tells about its backend connection."""

    @property
    def connected(self) -> bool: ...

    @property
    def connected_since(self) -> float | None: ...

    @property
    def queued_count(self) -> int: ...


class LiveSnapshot:
    def __init__(
        self,
        proxy: ProxyState,
        primary_name: str,
        recorders: RecorderHub,
        writer: MonitorWriter,
        clock: Callable[[], float] = time.time,
    ):
        self._proxy = proxy
        self._primary_name = primary_name
        self._recorders = recorders
        self._writer = writer
        self._clock = clock

    def __call__(self) -> dict[str, Any]:
        now = self._clock()
        dropped = self._recorders.dropped()
        last_drop_at = self._recorders.last_drop_at()
        contexts = {charger_id: self._proxy.context_for(charger_id) for charger_id in self._proxy.charger_ids()}
        chargers = []
        for charger_id, context in contexts.items():
            since = self._proxy.charger_connected_since(charger_id)
            chargers.append(
                {
                    "id": charger_id,
                    "connected": since is not None,
                    "since": since,
                    "peers": self._peers(charger_id, context, now),
                    "monitor": {"dropped": dropped.get(charger_id, 0), "last_drop_at": last_drop_at.get(charger_id)},
                }
            )
        return {
            "chargers": chargers,
            "monitor": {
                "fanout_failures": sum(_failures(context.traffic) for context in contexts.values()),
                "dropped_batches": self._writer.dropped_batches,
            },
        }

    def connected_chargers(self) -> set[str]:
        ids = self._proxy.charger_ids()
        return {charger_id for charger_id in ids if self._proxy.charger_connected_since(charger_id) is not None}

    def _peers(self, charger_id: str, context: ChargerContext, now: float) -> list[dict[str, Any]]:
        links: Mapping[str, tuple[str, Link]] = {
            self._primary_name: ("primary", context.primary),
            **{name: ("secondary", channel) for name, channel in context.secondaries.items()},
        }
        return [
            {
                "name": name,
                "role": role,
                "up": link.connected,
                "since": link.connected_since,
                "queued": link.queued_count,
                "messages_last_hour": self._writer.rates.last_hour(charger_id, name, now),
            }
            for name, (role, link) in links.items()
        ]


def _failures(traffic: FlowRecorder) -> int:
    return traffic.failures if isinstance(traffic, FanOut) else 0
