import json

import pytest

from encrypted_sessions import EncryptedSessionSerializer


class JsonSerializer:
    def encode(self, value):
        return json.dumps(dict(value)).encode()

    def decode(self, value):
        return json.loads(value)


def test_new_server_side_session_rows_are_encrypted_and_round_trip():
    serializer = EncryptedSessionSerializer(JsonSerializer(), "synthetic-session-secret")
    encoded = serializer.encode({"sv_password": "synthetic-password"})

    assert encoded.startswith(serializer.PREFIX)
    assert b"synthetic-password" not in encoded
    assert serializer.decode(encoded) == {"sv_password": "synthetic-password"}


def test_legacy_server_side_session_rows_remain_readable():
    legacy = json.dumps({"guest_id": "synthetic-guest"}).encode()
    serializer = EncryptedSessionSerializer(JsonSerializer(), "synthetic-session-secret")

    assert serializer.decode(legacy) == {"guest_id": "synthetic-guest"}


def test_modified_encrypted_session_data_is_rejected():
    serializer = EncryptedSessionSerializer(JsonSerializer(), "synthetic-session-secret")
    encoded = bytearray(serializer.encode({"value": "synthetic"}))
    encoded[-1] ^= 1

    with pytest.raises(ValueError, match="authenticated"):
        serializer.decode(bytes(encoded))
