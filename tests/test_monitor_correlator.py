import pytest

from ocpp_2w_proxy.monitor.correlator import Correlator, ReplyFlags

ON_TIME = ReplyFlags(late=False, duplicate=False)


def test_reply_resolves_to_the_registered_exchange():
    correlator = Correlator()
    correlator.register_sent("cp", "primary", "w1", 7, at=0.0)

    assert correlator.resolve_reply("cp", "primary", "w1", at=1.0) == (7, ON_TIME)
    assert len(correlator) == 0


@pytest.mark.parametrize(
    ("charger", "peer", "wire_id"),
    [("other", "primary", "w1"), ("cp", "stats", "w1"), ("cp", "primary", "w2")],
)
def test_reply_with_a_different_key_is_an_orphan(charger, peer, wire_id):
    correlator = Correlator()
    correlator.register_sent("cp", "primary", "w1", 7, at=0.0)

    assert correlator.resolve_reply(charger, peer, wire_id, at=1.0) is None


def test_second_reply_attaches_as_duplicate():
    correlator = Correlator()
    correlator.register_sent("cp", "primary", "w1", 7, at=0.0)
    correlator.resolve_reply("cp", "primary", "w1", at=1.0)

    assert correlator.resolve_reply("cp", "primary", "w1", at=2.0) == (7, ReplyFlags(late=False, duplicate=True))


def test_timed_out_entry_is_kept_and_its_reply_is_late():
    correlator = Correlator()
    correlator.register_sent("cp", "charger", "w1", 7, at=0.0)
    correlator.mark_timed_out("cp", "charger", "w1")

    assert len(correlator) == 1
    assert correlator.resolve_reply("cp", "charger", "w1", at=40.0) == (7, ReplyFlags(late=True, duplicate=False))
    assert correlator.resolve_reply("cp", "charger", "w1", at=41.0) == (7, ReplyFlags(late=True, duplicate=True))


def test_mark_timed_out_of_an_unknown_entry_is_ignored():
    correlator = Correlator()
    correlator.mark_timed_out("cp", "charger", "nope")

    assert len(correlator) == 0


def test_retry_under_a_new_wire_id_gets_its_own_entry():
    correlator = Correlator()
    correlator.register_sent("cp", "primary", "w1", 7, at=0.0)
    correlator.register_sent("cp", "primary", "w2", 7, at=5.0)

    assert correlator.resolve_reply("cp", "primary", "w2", at=6.0) == (7, ON_TIME)
    assert correlator.resolve_reply("cp", "primary", "w1", at=7.0) == (7, ON_TIME)


def test_link_down_clears_only_that_peers_entries_and_tombstones():
    correlator = Correlator()
    correlator.register_sent("cp", "primary", "w1", 7, at=0.0)
    correlator.register_sent("cp", "primary", "w2", 8, at=0.0)
    correlator.register_sent("cp", "stats", "w1", 9, at=0.0)
    correlator.resolve_reply("cp", "primary", "w2", at=1.0)

    correlator.link_down("cp", "primary")

    assert correlator.resolve_reply("cp", "primary", "w1", at=2.0) is None
    assert correlator.resolve_reply("cp", "primary", "w2", at=2.0) is None
    assert correlator.resolve_reply("cp", "stats", "w1", at=2.0) == (9, ON_TIME)


def test_wire_id_reused_after_link_down_resolves_to_the_new_exchange():
    correlator = Correlator()
    correlator.register_sent("cp", "charger", "1", 7, at=0.0)
    correlator.link_down("cp", "charger")
    correlator.register_sent("cp", "charger", "1", 8, at=1.0)

    assert correlator.resolve_reply("cp", "charger", "1", at=2.0) == (8, ON_TIME)


def test_entries_expire_from_the_head_after_an_hour():
    correlator = Correlator()
    correlator.register_sent("cp", "primary", "old", 1, at=0.0)
    correlator.register_sent("cp", "primary", "new", 2, at=3000.0)

    assert correlator.resolve_reply("cp", "primary", "old", at=3601.0) is None
    assert correlator.resolve_reply("cp", "primary", "new", at=3602.0) == (2, ON_TIME)


def test_map_is_capped_and_evicts_the_oldest():
    correlator = Correlator(max_entries=3)
    for n in range(5):
        correlator.register_sent("cp", "primary", f"w{n}", n, at=float(n))

    assert len(correlator) == 3
    assert correlator.resolve_reply("cp", "primary", "w1", at=9.0) is None
    assert correlator.resolve_reply("cp", "primary", "w2", at=9.0) == (2, ON_TIME)


def test_tombstone_ring_is_bounded():
    correlator = Correlator(max_tombstones=2)
    for n in range(3):
        correlator.register_sent("cp", "primary", f"w{n}", n, at=0.0)
        correlator.resolve_reply("cp", "primary", f"w{n}", at=0.0)

    assert correlator.resolve_reply("cp", "primary", "w0", at=1.0) is None
    assert correlator.resolve_reply("cp", "primary", "w2", at=1.0) == (2, ReplyFlags(late=False, duplicate=True))
