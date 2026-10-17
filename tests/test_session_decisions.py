"""What a charger session reports about the messages it relays, answers itself or loses.

Most decisions are asserted where test_proxy_integration exercises them; these have no other home."""

from __future__ import annotations

from conftest import attached_charger
from fakes import eventually

from ocpp_2w_proxy.reasons import Reason


async def test_the_charger_link_is_reported_up_and_down(primary, start_proxy, flow):
    charger = await attached_charger(await start_proxy(primary.url))
    assert flow.links_of("charger") == [True]

    await charger.close()
    await eventually(lambda: flow.links_of("charger") == [True, False])


async def test_an_unauthorized_id_tag_is_withheld_from_the_primary(primary, secondary, start_proxy, flow):
    charger = await attached_charger(await start_proxy(primary.url, secondary.url))

    await charger.call("Authorize", {"idTag": "0000"})

    [decision] = flow.with_reason(Reason.WITHHELD_UNAUTHORIZED)
    assert (decision.peer, decision.call.action) == ("primary", "Authorize")
    await charger.close()
