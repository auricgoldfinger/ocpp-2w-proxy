"""Mirror a primary outage to the charger: hide a short one, report a long one."""

from __future__ import annotations

from .primary_channel import PrimaryChannel


async def outlast_grace(primary: PrimaryChannel, grace: float) -> None:
    """Return once the primary has been unreachable for longer than `grace` seconds.

    A charger that believes it is online sends Authorize and StartTransaction to a primary
    that cannot answer; offline, it applies its own OCPP offline rules (local authorization,
    queued transaction messages) instead.
    """
    while True:
        await primary.wait_disconnected()
        if not await primary.wait_connected(grace):
            return
