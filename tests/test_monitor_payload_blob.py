import json
import zlib

from ocpp_2w_proxy.monitor import payload_blob

SMALL = json.dumps({"currentTime": "2026-10-15T10:00:00Z"})
LARGE = json.dumps({"meterValue": [{"sampledValue": [{"value": str(n)} for n in range(40)]}]})


def test_a_small_payload_is_stored_as_its_json_text():
    assert payload_blob.encode(SMALL) == SMALL.encode()


def test_a_large_payload_is_compressed():
    blob = payload_blob.encode(LARGE)

    assert len(LARGE) >= payload_blob.COMPRESS_FROM
    assert zlib.decompress(blob) == LARGE.encode()


def test_both_forms_decode_to_the_payload():
    for text in (SMALL, LARGE):
        assert payload_blob.decode(payload_blob.encode(text)) == json.loads(text)


def test_a_small_payload_compressed_by_an_earlier_version_still_decodes():
    assert payload_blob.decode(zlib.compress(SMALL.encode())) == json.loads(SMALL)
