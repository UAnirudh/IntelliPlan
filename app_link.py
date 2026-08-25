"""Handing a mobile session to the system browser.

The mirror image of ``desktop_auth.py``. That module hands a *browser*
sign-in back to a desktop app; this one hands an *app* sign-in forward to
a browser.

It exists because connecting Canvas or Google cannot happen inside the
app. Both are OAuth flows that begin at a Flask route guarded by
``current_user`` — they read the session cookie, not the bearer token the
phone holds — and Google additionally refuses to run OAuth inside an
embedded web view at all, hardest of all against the supervised Family
Link accounts these students often have. So the app must open the real
browser, and that browser arrives with no session: it does not know who
the student is, and the OAuth flow would attach the connection to nobody.

A one-time code closes the gap. The app, holding a valid bearer token,
asks for a code; it opens the browser at a URL carrying that code; the
server spends the code, logs that browser in, and forwards to the OAuth
start. When the provider finishes, the callback redirects to
``intelliplan://connected`` and the app closes the browser and refreshes.

The code is a bearer credential travelling in a URL, so it is built to be
worth as little as possible to anyone who sees it:

* **Single use.** Spent on first redemption; a replay finds it burned.
* **Short life.** Ninety seconds is longer than the redirect takes and
  shorter than an attacker can act on a leaked log line.
* **Stored hashed.** Only the SHA-256 is kept, so read access to the table
  does not confer the ability to redeem.
* **Constant-time comparison,** so a grinder learns nothing from timing.
* **Allow-listed destinations.** The ``next`` path is matched against a
  fixed set of internal OAuth entry points. Without that, the endpoint is
  an open redirect that also hands over a live session — the two halves of
  a session-fixation attack in one URL.

Pure by design: no Flask, no database, no clock of its own, so the
security decisions are testable without standing up an app.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode

#: How long a minted code stays redeemable. The browser opens immediately,
#: so this only has to cover a cold start and a slow network.
CODE_TTL_SECONDS = 90

#: 32 bytes, urlsafe-base64'd. It rides in a URL and is never typed.
_CODE_BYTES = 32

#: The app's deep-link scheme. Registered in mobile/app.json and in
#: desktop/package.json; written down once here so they cannot drift.
APP_SCHEME = "intelliplan"

#: Where a hand-off may send the browser next.
#:
#: An allow-list rather than a pattern: every entry is a route that starts
#: an OAuth flow we own, and the whole point of the endpoint is that it
#: redirects *while authenticated*. A caller-supplied path would let a
#: stranger pick the destination for somebody else's live session.
_ALLOWED_NEXT = {
    "canvas": "/oauth/canvas",
    "google": "/oauth/google",
    "notion": "/oauth/notion",
    "settings": "/settings",
    "integrations": "/integrations",
}

#: Providers whose connect flow may be started this way.
LINKABLE = ("canvas", "google", "notion")

_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def new_code() -> str:
    """A fresh one-time hand-off code."""
    return secrets.token_urlsafe(_CODE_BYTES)


def hash_code(code: str) -> str:
    """What gets stored. Never store the code itself."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def is_valid_code(code: str | None) -> bool:
    """Reject anything that could not be one of ours.

    This runs on a path segment a stranger controls, so it is the boundary
    between arbitrary input and a database lookup. Shape only — the code
    is opaque beyond its alphabet and length.
    """
    return bool(code) and bool(_CODE_RE.match(code))


def codes_match(supplied: str, stored_hash: str) -> bool:
    """Constant-time check that a supplied code hashes to the stored value.

    A plain ``==`` on the hash leaks how much of a guess was right, which
    is the entire game against someone grinding at the endpoint.
    """
    if not is_valid_code(supplied) or not stored_hash:
        return False
    return hmac.compare_digest(hash_code(supplied), stored_hash)


def is_expired(created_at: datetime | None, now: datetime) -> bool:
    """Has a code aged out?

    Both naive UTC, matching the models. A missing timestamp counts as
    expired rather than eternal — the safe direction to fail.
    """
    if created_at is None:
        return True
    return now >= created_at + timedelta(seconds=CODE_TTL_SECONDS)


def resolve_next(target: str | None) -> str | None:
    """The internal path a hand-off may forward to, or None if not allowed.

    Returns a path, never a caller-supplied string, so nothing a client
    sends can reach a redirect verbatim.
    """
    return _ALLOWED_NEXT.get((target or "").strip().lower())


def deep_link(event: str, **params: str) -> str:
    """A URL that returns control to the app.

    ``intelliplan://connected?provider=canvas`` and friends. The app
    listens for these to know the browser is done and the list is worth
    refetching.
    """
    query = urlencode({k: v for k, v in params.items() if v})
    return f"{APP_SCHEME}://{event}" + (f"?{query}" if query else "")
