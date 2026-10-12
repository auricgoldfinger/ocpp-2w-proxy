import pytest

from ocpp_2w_proxy.ocpp import Call
from ocpp_2w_proxy.state import StateStore
from ocpp_2w_proxy.transactions import TransactionMap
from ocpp_2w_proxy.withheld_calls import is_unauthorized_id_tag, withhold


@pytest.fixture
def txmap(tmp_path):
    return TransactionMap(StateStore(tmp_path / "s.json"))


@pytest.mark.parametrize("tag", ["", "0", "0" * 20])
def test_empty_or_zero_tags_are_unauthorized(tag):
    assert is_unauthorized_id_tag(tag)


@pytest.mark.parametrize("tag", ["00001", "04A2B3C4", None, 0])
def test_other_tags_are_not(tag):
    assert not is_unauthorized_id_tag(tag)


def test_authorize_is_answered_invalid(txmap):
    withheld = withhold(Call("1", "Authorize", {"idTag": "0000"}), txmap)
    assert withheld.reply.payload == {"idTagInfo": {"status": "Invalid"}}
    assert withheld.start_ref is None


def test_start_gets_a_proxy_made_transaction(txmap):
    withheld = withhold(Call("1", "StartTransaction", {"connectorId": 1, "idTag": "0000"}), txmap)
    assert withheld.reply.payload == {"transactionId": 2147483647, "idTagInfo": {"status": "Invalid"}}
    assert withheld.start_ref is not None
    assert txmap.is_withheld(2147483647)


def test_the_withheld_session_follows_its_start(txmap):
    tx = withhold(Call("1", "StartTransaction", {"idTag": ""}), txmap).reply.payload["transactionId"]
    assert withhold(Call("2", "MeterValues", {"transactionId": tx}), txmap).reply.payload == {}
    assert withhold(Call("3", "StopTransaction", {"transactionId": tx}), txmap).reply.payload == {}
    assert not txmap.is_withheld(tx)


@pytest.mark.parametrize(
    "call",
    [
        Call("1", "Authorize", {"idTag": "04A2B3C4"}),
        Call("1", "StartTransaction", {"idTag": "04A2B3C4"}),
        Call("1", "MeterValues", {"connectorId": 1, "meterValue": []}),
        Call("1", "MeterValues", {"transactionId": 100}),
        Call("1", "StopTransaction", {"transactionId": 100, "idTag": "0000"}),
        Call("1", "Heartbeat", {}),
    ],
)
def test_everything_else_goes_to_the_primary(txmap, call):
    assert withhold(call, txmap) is None
