"""Every leg of one logical message carries the exchange id of the Call it came from, and that id is cheap."""

import statistics
import timeit

import pytest
from conftest import CHARGER_ID, make_raw_config
from fakes import RecordingFlow
from test_secondary_channel import make_channel, make_config

from ocpp_2w_proxy.command_router import CommandRouter
from ocpp_2w_proxy.config import parse
from ocpp_2w_proxy.ocpp import Call, CallResult
from ocpp_2w_proxy.policy import CommandPolicy, Rule
from ocpp_2w_proxy.primary_channel import PrimaryChannel
from ocpp_2w_proxy.state import StateStore
from ocpp_2w_proxy.traffic_log import TrafficLog
from ocpp_2w_proxy.transactions import TransactionMap


class _Target:
    name = "tap"

    async def reply(self, message):  # pragma: no cover - not used here
        pass


class _RecordingLink:
    def __init__(self):
        self.calls: list[Call] = []

    async def call(self, call, timeout):
        self.calls.append(call)
        return CallResult(call.id, {})


def test_every_new_call_gets_a_fresh_exchange_id():
    first, second = Call("1", "Heartbeat", {}), Call("1", "Heartbeat", {})
    assert first.exchange_id != second.exchange_id


def test_exchange_id_does_not_affect_equality_or_repr():
    first, second = Call("1", "Heartbeat", {}), Call("1", "Heartbeat", {})
    assert first == second
    assert "exchange_id" not in repr(first)


def test_with_id_and_with_payload_keep_the_exchange_id():
    call = Call("1", "MeterValues", {"connectorId": 1})
    assert call.with_id("2").exchange_id == call.exchange_id
    assert call.with_payload({}).exchange_id == call.exchange_id


def test_exchange_id_survives_the_command_router():
    call = Call("1", "GetConfiguration", {})
    assert CommandRouter(RecordingFlow()).outbound(_Target(), call).exchange_id == call.exchange_id


def test_exchange_id_survives_the_policy_rewrite():
    policy = CommandPolicy({"RemoteStartTransaction": Rule.FORWARD}, Rule.ERROR, strip_charging_profile=True)
    call = Call("1", "RemoteStartTransaction", {"idTag": "ABC", "chargingProfile": {"x": 1}})
    stripped = policy.decide(call)
    assert stripped.payload == {"idTag": "ABC"}
    assert stripped.exchange_id == call.exchange_id


def test_exchange_id_survives_the_transaction_rewrites(tmp_path):
    txmap = TransactionMap(StateStore(tmp_path / "CH1.json"))
    txmap.primary_started("a", 100)
    txmap.secondary_started("tap", "a", 9)
    to_secondary = Call("1", "StopTransaction", {"transactionId": 100})
    to_charger = Call("1", "RemoteStopTransaction", {"transactionId": 9})

    assert txmap.rewrite_for_secondary(to_secondary, "tap").exchange_id == to_secondary.exchange_id
    assert txmap.rewrite_for_charger(to_charger, "tap").exchange_id == to_charger.exchange_id


async def test_exchange_id_survives_secondary_submit(tmp_path):
    channel = make_channel(make_config({"name": "tap", "url": "ws://s"}), "tap", StateStore(tmp_path / "CH1.json"))
    call = Call("1", "StartTransaction", {"connectorId": 1})
    channel.submit(call, start_ref="ref")
    queued = channel._queue[-1].call
    assert queued.id != call.id
    assert queued.exchange_id == call.exchange_id


async def test_exchange_id_survives_primary_delivery(tmp_path):
    config = parse(make_raw_config("ws://127.0.0.1:1", None, tmp_path, primary={"auth": "none"}), {})
    store = StateStore.for_charger(config.proxy.state_dir, CHARGER_ID)
    channel = PrimaryChannel(config.primary, config.chargers[CHARGER_ID], store, TrafficLog(CHARGER_ID, False))
    link = _RecordingLink()
    call = Call("1", "StopTransaction", {"transactionId": 1})
    try:
        await channel._deliver(link, call)
    finally:
        await channel.close()
    assert link.calls[0].id != call.id
    assert link.calls[0].exchange_id == call.exchange_id


# --- cost: the counter is on the relay path even with the monitor off -------------------------

# The plan's budget for constructing a Call, counter included; measured about 0.2 µs (Python 3.14, x86-64).
CALL_BUDGET_SECONDS = 0.3e-6
# The default run only guards against a gross regression: shared machines are noisy.
CALL_GUARD_SECONDS = 1e-6


def median_call_construction_seconds(loops: int = 20_000, repeat: int = 7) -> float:
    timer = timeit.Timer("Call('w1', 'Heartbeat', payload)", globals={"Call": Call, "payload": {}})
    return statistics.median(timer.repeat(repeat=repeat, number=loops)) / loops


def test_constructing_a_call_stays_cheap():
    assert median_call_construction_seconds() <= CALL_GUARD_SECONDS


@pytest.mark.perf
def test_constructing_a_call_meets_the_plan_budget():
    median = median_call_construction_seconds(loops=100_000)
    print(f"Call construction median {median * 1e9:.0f} ns")
    assert median <= CALL_BUDGET_SECONDS
