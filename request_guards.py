"""Cross-site request forgery defence, and request-size sanity.

IntelliPlan's session cookie is ``SameSite=Lax``, which already stops a
browser sending it on a cross-site POST — that is the bulk of CSRF defence
and it is already in place. This adds the second layer, because Lax alone
has known gaps: it does not cover a same-site subdomain that has been taken
over, some older browsers do not honour it, and a cookie set before the
attribute existed can outlive it.

The approach is an Origin/Referer check rather than per-form tokens. Both
are accepted mitigations; tokens are stronger in principle but only when
every form and every ``fetch`` carries one, and retro-fitting them across an
app this size mostly produces endpoints that are broken or silently exempt.
A header check applies uniformly the moment it is installed, and browsers
have sent ``Origin`` on state-changing requests for years.

What is deliberately allowed through:

* Safe methods. GET/HEAD/OPTIONS change nothing.
* Requests with no ``Origin`` and no ``Referer`` at all. Those are not
  browsers — they are curl, the mobile app, health checks — and none of them
  carry a session cookie an attacker could ride. A browser form POST always
  sends at least one.
* The browser extension and OAuth callbacks, which are genuinely cross-origin
  by design and authenticate by other means.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from flask import request

#: Methods that can change state and therefore need checking.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Paths where a cross-origin caller is the entire point. Each authenticates
#: by something other than the session cookie, so riding the cookie gains an
#: attacker nothing.
EXEMPT_PREFIXES = (
    "/oauth",            # provider redirects back to us
    "/oauth2callback",
    "/api/lms/callback",
    "/api/auth/",        # extension bearer-token endpoints
    "/api/extension/",
    "/webhook",          # provider-signed callbacks
)


def _host_of(value: str) -> str:
    try:
        return (urlparse(value).netloc or "").lower()
    except Exception:
        return ""


def allowed_hosts() -> set[str]:
    """Hosts whose pages may make state-changing requests to us.

    The request's own host is included so the check keeps working on any
    deployment — staging, a preview URL, localhost — without configuration.
    """
    hosts = set()
    for value in (os.getenv("APP_BASE_URL", ""),
                  os.getenv("ALLOWED_ORIGINS", "")):
        for candidate in value.split(","):
            host = _host_of(candidate.strip())
            if host:
                hosts.add(host)
    try:
        if request.host:
            hosts.add(request.host.lower())
    except Exception:
        pass
    return hosts


def is_exempt(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in EXEMPT_PREFIXES)


def cross_site_violation() -> str | None:
    """Return a reason string when this request looks forged, else ``None``.

    Kept as a predicate rather than an abort so the caller decides what to do
    with it, and so it can be tested without a response cycle.
    """
    if request.method not in UNSAFE_METHODS:
        return None

    path = request.path or ""
    if is_exempt(path):
        return None

    origin = request.headers.get("Origin", "")
    referer = request.headers.get("Referer", "")

    # A browser doing a cross-site form POST always sends one of these. A
    # request with neither is a non-browser client, which cannot be made to
    # carry someone else's cookies.
    if not origin and not referer:
        return None

    # The extension legitimately posts from its own origin.
    if origin.startswith("chrome-extension://") or origin.startswith("moz-extension://"):
        return None

    permitted = allowed_hosts()
    source = _host_of(origin) or _host_of(referer)
    if not source:
        # Present but unparseable. Treat as suspicious rather than assume.
        return "unreadable origin"
    if source not in permitted:
        return f"cross-site request from {source}"
    return None
