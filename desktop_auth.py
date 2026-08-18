"""Handing a browser sign-in back to the desktop app.

Google refuses to run OAuth inside an embedded browser view, and enforces
that hardest against supervised Family Link accounts — exactly the students
this app is for. So the desktop client cannot show Google's sign-in page
itself. It has to open the real system browser and get the resulting
session back somehow.

The route back is the ``intelliplan://`` protocol the app registers at
install time. The browser finishes the OAuth round-trip on the website as
normal, then redirects to ``intelliplan://auth?code=…`` and Windows hands
that to the running app.

That last hop is the dangerous part, and it is what this module exists to
make safe. **Any** local program can register ``intelliplan://`` too, so a
code travelling over it must be assumed to be observable by a hostile
application on the same machine. The protections, which is to say the
reasons every function here is shaped the way it is:

* **PKCE binding.** The app invents a random ``verifier`` before it opens
  the browser and sends only its SHA-256 (the ``challenge``). Redeeming a
  code requires the original verifier, which never leaves the app. A thief
  who intercepts the deep link still cannot spend the code.
* **Single use.** A code is burned on redemption, so a replay loses.
* **Short life.** Two minutes is longer than a sign-in takes and shorter
  than an attacker wants.
* **Stored hashed.** The database keeps the SHA-256 of a code, never the
  code. Read access to the table does not grant the ability to redeem.
* **Constant-time comparison.** Comparing secrets with ``==`` leaks their
  contents through timing.

Everything here is pure: no database, no Flask, no clock of its own. That
keeps the security-critical decisions testable without standing up an app,
which for this file matters more than the convenience of a shortcut.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta

#: How long a minted code stays redeemable. A sign-in that takes longer
#: than this is one the student abandoned; making them click again is a
#: much smaller cost than leaving a credential lying around.
CODE_TTL_SECONDS = 120

#: 32 bytes of entropy, urlsafe-base64'd. Codes ride in a URL and are never
#: typed by a human, so there is no reason to be shy about length.
_CODE_BYTES = 32

#: A verifier below this is not worth calling one. RFC 7636 sets the floor
#: at 43 characters for the base64url of 32 bytes.
_MIN_VERIFIER_LEN = 43
_MAX_VERIFIER_LEN = 128

#: base64url of a SHA-256 digest: 43 characters, no padding.
_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


def _b64url(raw: bytes) -> str:
    """base64url with the padding stripped, as PKCE specifies."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def new_code() -> str:
    """A fresh one-time code to send through the deep link."""
    return secrets.token_urlsafe(_CODE_BYTES)


def new_verifier() -> str:
    """A fresh PKCE verifier. The desktop app makes its own; this exists
    for tests and for any future server-side client."""
    return secrets.token_urlsafe(_CODE_BYTES)


def hash_code(code: str) -> str:
    """What gets stored. Never store the code itself."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def challenge_for(verifier: str) -> str:
    """The PKCE S256 challenge for a verifier.

    The app computes this before opening the browser and sends it along;
    the server holds it until redemption.
    """
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def is_valid_challenge(challenge: str | None) -> bool:
    """Reject anything that is not a well-formed S256 challenge.

    This runs on a query parameter, so it is the boundary between a URL a
    stranger can craft and a value we store. Length and alphabet only —
    a challenge is opaque to us beyond its shape.
    """
    return bool(challenge) and bool(_CHALLENGE_RE.match(challenge))


def is_valid_verifier(verifier: str | None) -> bool:
    """Reject a verifier that could not have produced a real challenge.

    Without a floor here, a client could send a one-character verifier and
    the PKCE binding would be brute-forceable.
    """
    if not verifier:
        return False
    if not (_MIN_VERIFIER_LEN <= len(verifier) <= _MAX_VERIFIER_LEN):
        return False
    return bool(re.match(r"^[A-Za-z0-9._~-]+$", verifier))


def verifier_matches(verifier: str, challenge: str) -> bool:
    """Does this verifier redeem this challenge?

    Constant time: a plain ``==`` on the derived challenge would leak how
    much of a guess was correct, which is the whole game when someone is
    grinding at a stolen code.
    """
    if not is_valid_verifier(verifier) or not is_valid_challenge(challenge):
        return False
    return hmac.compare_digest(challenge_for(verifier), challenge)


def is_expired(created_at: datetime, now: datetime) -> bool:
    """Has a code aged out?

    Both arguments are naive UTC, matching the ``datetime.utcnow`` default
    the models use. A missing timestamp counts as expired rather than
    eternal — the safe direction to fail.
    """
    if created_at is None:
        return True
    return now >= created_at + timedelta(seconds=CODE_TTL_SECONDS)


def deep_link_for(code: str) -> str:
    """The URL the browser is redirected to, handing control back.

    Kept here so the scheme is written down once and matches the
    ``protocols`` block in desktop/package.json.
    """
    return f"intelliplan://auth?code={code}"
