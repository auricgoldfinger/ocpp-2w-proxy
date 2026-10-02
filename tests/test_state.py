from ocpp_2w_proxy.state import StateStore


def test_state_store_round_trip_and_corruption(tmp_path):
    store = StateStore(tmp_path / "c.json")
    store.state.transactions["1"] = 2
    store.save()
    assert StateStore(tmp_path / "c.json").state.transactions == {"1": 2}

    (tmp_path / "c.json").write_text("{garbage")
    assert StateStore(tmp_path / "c.json").state.transactions == {}
    assert (tmp_path / "c.corrupt").exists()
