import asyncio

from conftest import CHARGER_ID
from fakes import FakeCharger
from test_proxy_integration import START, eventually, responder_with_transaction

from ocpp_2w_proxy.ocpp import Call, CallError, CallResult
from ocpp_2w_proxy.reasons import Reason
from ocpp_2w_proxy.session_resume import ResumeTracker
from ocpp_2w_proxy.state import StateStore

CARD = "04A2B3C4"
ACCEPTED_START = CallResult("s", {"transactionId": 100, "idTagInfo": {"status": "Accepted"}})
REMOTE_STOP = Call("r", "RemoteStopTransaction", {"transactionId": 100})
ACCEPTED = CallResult("r", {"status": "Accepted"})
RESUME = Call("h", "RemoteStartTransaction", {"connectorId": 1, "idTag": "HA-TAG"})


def make(tmp_path):
    return ResumeTracker(StateStore(tmp_path / "s.json"))


def start(connector=1, id_tag=CARD):
    return Call("s", "StartTransaction", {"connectorId": connector, "idTag": id_tag, "meterStart": 0})


def status(value, connector=1):
    return Call("n", "StatusNotification", {"connectorId": connector, "status": value, "errorCode": "NoError"})


def stopped_by_ha(tmp_path):
    tracker = make(tmp_path)
    tracker.start_answered(start(), ACCEPTED_START)
    tracker.command_answered("ha", REMOTE_STOP, ACCEPTED)
    return tracker


def test_resumes_with_the_card_of_the_session_it_stopped(tmp_path):
    resumed = stopped_by_ha(tmp_path).resume(RESUME, "ha")
    assert (resumed.id, resumed.action, resumed.payload) == (
        "h",
        "RemoteStartTransaction",
        {"connectorId": 1, "idTag": CARD},
    )


def test_a_charging_profile_is_not_resumed_along(tmp_path):
    with_profile = RESUME.with_payload({**RESUME.payload, "chargingProfile": {"chargingProfileId": 1}})
    assert stopped_by_ha(tmp_path).resume(with_profile, "ha").payload == {"connectorId": 1, "idTag": CARD}


def test_without_connector_the_only_stopped_one_resumes(tmp_path):
    resumed = stopped_by_ha(tmp_path).resume(RESUME.with_payload({"idTag": "HA-TAG"}), "ha")
    assert resumed.payload == {"connectorId": 1, "idTag": CARD}


def test_only_the_backend_that_stopped_may_resume(tmp_path):
    tracker = stopped_by_ha(tmp_path)
    assert tracker.resume(RESUME, "stats") is None
    assert tracker.resume(RESUME.with_payload({"connectorId": 2, "idTag": "HA-TAG"}), "ha") is None


def test_no_resume_without_a_remote_stop(tmp_path):
    tracker = make(tmp_path)
    tracker.start_answered(start(), ACCEPTED_START)
    assert tracker.resume(RESUME, "ha") is None


def test_a_refused_remote_stop_does_not_count(tmp_path):
    tracker = make(tmp_path)
    tracker.start_answered(start(), ACCEPTED_START)
    tracker.command_answered("ha", REMOTE_STOP, CallResult("r", {"status": "Rejected"}))
    tracker.command_answered("ha", REMOTE_STOP, CallError("r", "GenericError", "no"))
    assert tracker.resume(RESUME, "ha") is None


def test_a_stop_of_another_transaction_does_not_count(tmp_path):
    tracker = make(tmp_path)
    tracker.start_answered(start(), ACCEPTED_START)
    tracker.command_answered("ha", REMOTE_STOP.with_payload({"transactionId": 99}), ACCEPTED)
    assert tracker.resume(RESUME, "ha") is None


def test_unplugging_forgets_the_session(tmp_path):
    tracker = stopped_by_ha(tmp_path)
    tracker.status_changed(status("Finishing"))
    assert tracker.resume(RESUME, "ha") is not None
    tracker.status_changed(status("Available"))
    assert tracker.resume(RESUME, "ha") is None


def test_a_new_start_replaces_the_stopped_session(tmp_path):
    tracker = stopped_by_ha(tmp_path)
    tracker.start_answered(start(), CallResult("s", {"transactionId": 101, "idTagInfo": {"status": "Accepted"}}))
    assert tracker.resume(RESUME, "ha") is None


def test_a_start_the_primary_refused_forgets_the_session(tmp_path):
    tracker = stopped_by_ha(tmp_path)
    tracker.start_answered(
        start(id_tag="OTHER"), CallResult("s", {"transactionId": 7, "idTagInfo": {"status": "Invalid"}})
    )
    assert tracker.resume(RESUME, "ha") is None


def test_a_stopped_session_survives_a_restart(tmp_path):
    stopped_by_ha(tmp_path)
    assert make(tmp_path).resume(RESUME, "ha").payload["idTag"] == CARD


def test_other_commands_are_no_resume(tmp_path):
    assert stopped_by_ha(tmp_path).resume(REMOTE_STOP, "ha") is None


# --- through the proxy ------------------------------------------------------------------------


async def stopped_session(primary, secondary, start_proxy, **policy):
    primary.responder = responder_with_transaction(100)
    secondary.responder = responder_with_transaction(9)
    url = await start_proxy(primary.url, secondary.url, secondary={"policy": policy})
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await secondary.connected.wait()
    await charger.call("StartTransaction", START)
    await secondary.wait_for_call("StartTransaction")
    await asyncio.sleep(0.05)  # let the secondary's StartTransaction.conf be processed
    assert (await secondary.call("RemoteStopTransaction", {"transactionId": 9}))[2] == {"status": "Accepted"}
    await charger.call("StopTransaction", {"transactionId": 100, "meterStop": 10, "timestamp": "t", "reason": "Remote"})
    await charger.call("StatusNotification", {"connectorId": 1, "status": "Finishing", "errorCode": "NoError"})
    return charger


async def test_a_secondary_resumes_its_own_stop_with_the_card(primary, secondary, start_proxy, flow):
    charger = await stopped_session(primary, secondary, start_proxy, resume_own_stops=True)

    reply = await secondary.call("RemoteStartTransaction", {"connectorId": 1, "idTag": "HA-TAG"}, message_id="ha-1")

    assert reply == [3, "ha-1", {"status": "Accepted"}]
    assert charger.received_calls[-1][2:] == ["RemoteStartTransaction", {"connectorId": 1, "idTag": START["idTag"]}]
    [decision] = flow.with_reason(Reason.RESUMED)
    assert (decision.peer, decision.call.payload["idTag"]) == ("tap", "HA-TAG")
    await charger.close()


async def test_without_resume_own_stops_a_secondary_start_stays_rejected(primary, secondary, start_proxy):
    charger = await stopped_session(primary, secondary, start_proxy)

    reply = await secondary.call("RemoteStartTransaction", {"connectorId": 1, "idTag": "HA-TAG"})

    assert reply[2] == {"status": "Rejected"}
    assert [call[2] for call in charger.received_calls] == ["RemoteStopTransaction"]
    await charger.close()


async def test_no_resume_after_the_cable_was_unplugged(primary, secondary, start_proxy):
    charger = await stopped_session(primary, secondary, start_proxy, resume_own_stops=True)
    await charger.call("StatusNotification", {"connectorId": 1, "status": "Available", "errorCode": "NoError"})

    reply = await secondary.call("RemoteStartTransaction", {"connectorId": 1, "idTag": "HA-TAG"})

    assert reply[2] == {"status": "Rejected"}
    await eventually(lambda: [call[2] for call in charger.received_calls] == ["RemoteStopTransaction"])
    await charger.close()
