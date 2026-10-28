"""Authenticated encryption for Flask-Session's server-side serialized data."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


class EncryptedSessionSerializer:
    """Encrypt new session rows while remaining able to read existing rows.

    Flask-Session stores an opaque session ID in the browser, but its database
    serializer otherwise leaves values such as guest LMS credentials readable
    to anyone with a database backup. Legacy rows remain readable and become
    encrypted the next time the session is written.
    """

    PREFIX = b"intelliplan-session:v1:"

    def __init__(self, serializer, secret_key):
        if not secret_key:
            raise ValueError("A Flask secret key is required to encrypt server-side sessions.")
        key_material = secret_key.encode() if isinstance(secret_key, str) else bytes(secret_key)
        derived = hashlib.sha256(b"intelliplan-session-store-v1\0" + key_material).digest()
        self._cipher = Fernet(base64.urlsafe_b64encode(derived))
        self._serializer = serializer

    def encode(self, session):
        plaintext = self._serializer.encode(session)
        return self.PREFIX + self._cipher.encrypt(plaintext)

    def decode(self, serialized_data):
        if isinstance(serialized_data, memoryview):
            serialized_data = serialized_data.tobytes()
        if isinstance(serialized_data, str):
            serialized_data = serialized_data.encode()
        if serialized_data.startswith(self.PREFIX):
            try:
                plaintext = self._cipher.decrypt(serialized_data[len(self.PREFIX):])
            except InvalidToken as exc:
                raise ValueError("Session data could not be authenticated.") from exc
            return self._serializer.decode(plaintext)
        return self._serializer.decode(serialized_data)
