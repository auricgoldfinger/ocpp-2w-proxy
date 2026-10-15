import json

import pytest

from ocpp_2w_proxy.monitor import compact_json

BODIES = [{}, {"idTag": "04A1·B2", "n": [1, 2.5, None, True]}, [{"a": {"b": "c"}}], "text", 7]


@pytest.mark.parametrize("body", BODIES)
def test_the_shared_encoder_writes_what_json_dumps_writes(body):
    expected = json.dumps(body, separators=(",", ":"))

    assert compact_json.dumps(body) == expected
    assert compact_json.dumps_bytes(body) == expected.encode()
