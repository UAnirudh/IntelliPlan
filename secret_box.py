"""Encryption at rest for the third-party tokens IntelliPlan holds.

A student who connects Canvas, Google, Notion, Blackboard or Moodle hands
IntelliPlan a credential that reads their coursework and, for Google, their
calendar. Those sat in the database as plaintext, which means a database
backup, a snapshot, a misconfigured read replica, or a SQL-injection bug
anywhere in the app hands over live access to every connected account. The
password column has always been hashed; these were not protected at all.

Design constraints that shaped this:

*Existing rows must keep working.* Turning encryption on cannot log everyone
out of their integrations. Ciphertext carries a version prefix, so a value
without one is recognised as legacy plaintext and returned as-is. Rows
migrate as they are written, and a backfill command exists for the rest.

*A missing key must not break the app.* Development and CI have no key.
Without one this is a no-op that stores plaintext exactly as before, and says
so once at startup rather than failing to boot or silently pretending.

*Losing the key must be survivable.* It is not a hash — a lost key means
unreadable tokens, and every affected student has to reconnect their
accounts. That is recoverable, which is why this is worth doing; it is not
free, which is why the key belongs in a secret store and not in git.

Generating a key:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Then set ``DATA_ENCRYPTION_KEY`` to the result. To rotate, put the new key
first and keep the old one after it, comma separated: values are read with
any key and always written with the first.
"""

from __future__ import annotations

import os

from sqlalchemy import Text, TypeDecorator

#: Marks a value this module produced. Anything without it predates
#: encryption and is returned untouched, which is what makes turning this on
#: safe on a live database.
PREFIX = "enc:v1:"

_fernet = None
_looked_up = False
_warned = False


def _keys() -> list[str]:
    raw = (os.getenv("DATA_ENCRYPTION_KEY") or "").strip()
    return [k.strip() for k in raw.split(",") if k.strip()]


def _cipher():
    """The MultiFernet for the configured keys, or None when unconfigured."""
    global _fernet, _looked_up, _warned
    if _looked_up:
        return _fernet
    _looked_up = True

    keys = _keys()
    if not keys:
        if not _warned:
            _warned = True
            print("[secret-box] DATA_ENCRYPTION_KEY is not set — third-party "
                  "tokens are stored in plaintext. Set it in production.")
        return None

    try:
        from cryptography.fernet import Fernet, MultiFernet
        # First key encrypts; all of them can decrypt, which is what makes
        # rotation a config change rather than a migration.
        _fernet = MultiFernet([Fernet(k.encode()) for k in keys])
    except Exception as exc:
        # A malformed key must not silently downgrade to plaintext without
        # anybody noticing.
        print(f"[secret-box] DATA_ENCRYPTION_KEY is unusable ({exc}); "
              "storing plaintext.")
        _fernet = None
    return _fernet


def reset_cache() -> None:
    """Forget the cached cipher. For tests that change the key."""
    global _fernet, _looked_up, _warned
    _fernet, _looked_up, _warned = None, False, False


def is_enabled() -> bool:
    return _cipher() is not None


def is_encrypted(value: str | None) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(value):
    """Encrypt a string. Returns it unchanged when no key is configured."""
    if value is None or value == "":
        return value
    if is_encrypted(value):
        return value
    cipher = _cipher()
    if cipher is None:
        return value
    try:
        return PREFIX + cipher.encrypt(str(value).encode()).decode()
    except Exception as exc:
        print(f"[secret-box] encrypt failed: {exc}")
        return value


def decrypt(value):
    """Decrypt a value this module produced; pass anything else through.

    A value that fails to decrypt is returned as-is rather than raised on.
    The realistic cause is a rotated-away key, and turning that into a 500 on
    every page the token touches helps nobody — the integration fails, the
    student reconnects, and the log says why.
    """
    if not is_encrypted(value):
        return value
    cipher = _cipher()
    if cipher is None:
        print("[secret-box] found encrypted data but no key is configured.")
        return None
    try:
        return cipher.decrypt(value[len(PREFIX):].encode()).decode()
    except Exception as exc:
        print(f"[secret-box] decrypt failed (rotated or wrong key?): {exc}")
        return None


class EncryptedText(TypeDecorator):
    """A Text column encrypted on the way in and decrypted on the way out.

    Transparent to every caller, so existing code that reads
    ``row.access_token`` needs no change and cannot forget to decrypt.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt(value)

    def process_result_value(self, value, dialect):
        return decrypt(value)
