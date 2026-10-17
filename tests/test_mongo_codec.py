import pytest

from intelliplan.persistence.codec import pack_document, unpack_document


def test_large_unicode_and_json_strings_round_trip_exactly():
    original = {"_id": "a", "json": '{"number":0.12345678901234567890}' * 2000,
                "unicode": "\u2603\u65e5\U0001f680" * 10000, "small": "same", "none": None}
    packed = pack_document(original)
    assert "_ip_encoding" in packed
    assert unpack_document(packed) == original
    assert original["unicode"] == "\u2603\u65e5\U0001f680" * 10000


def test_small_fields_are_unchanged_and_unknown_encoding_is_rejected():
    assert pack_document({"_id": "a", "data": "small"}) == {"_id": "a", "data": "small"}
    with pytest.raises(ValueError, match="reserved"):
        pack_document({"_ip_encoding": {}})
    with pytest.raises(ValueError, match="Unsupported"):
        unpack_document({"_ip_encoding": {"data": "other"}, "data": "value"})
