"""Google reCAPTCHA verification for the forms strangers can reach.

Three endpoints are reachable without signing in and each is abusable in a
different way: registration mints accounts, password reset sends mail to an
address the requester names, and sign-in is where credential stuffing lands.
Rate limits already cap all three per IP; this raises the cost per attempt
for whoever is willing to rotate addresses.

Version-agnostic on purpose. ``siteverify`` is the same endpoint for v2 and
v3 and the response differs only in that v3 adds a ``score``. So the check is
"did it verify, and if a score came back, is it above the floor" — which is
correct for either, and means a key swap does not need a code change. The
*frontend* does differ, so ``RECAPTCHA_VERSION`` selects the widget.

Failure handling is deliberately asymmetric:

*A missing or invalid token fails closed.* That is the case this exists for.

*A network failure fails open.* If Google is unreachable, the alternative is
that nobody can sign in or register until it comes back. An outage at Google
must not become an outage here, and a bot that can also take down
``recaptcha.net`` was never going to be stopped by this.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"

#: v3 returns 0.0–1.0, where 1.0 is most likely human. Google's own default
#: is 0.5; below that a real student on a shared or VPN'd connection starts
#: getting refused, which costs more than the bots it stops.
DEFAULT_SCORE_THRESHOLD = 0.5

#: Seconds. Short: this sits in front of sign-in, and a slow answer here is
#: indistinguishable from a broken site.
TIMEOUT = 5


def site_key() -> str:
    return (os.getenv("RECAPTCHA_SITE_KEY") or "").strip()


def secret_key() -> str:
    return (os.getenv("RECAPTCHA_SECRET_KEY") or "").strip()


def version() -> str:
    """``v2`` (checkbox) or ``v3`` (invisible, scored)."""
    value = (os.getenv("RECAPTCHA_VERSION") or "v2").strip().lower()
    return value if value in {"v2", "v3"} else "v2"


def score_threshold() -> float:
    try:
        return float(os.getenv("RECAPTCHA_SCORE_THRESHOLD")
                     or DEFAULT_SCORE_THRESHOLD)
    except (TypeError, ValueError):
        return DEFAULT_SCORE_THRESHOLD


def is_enabled() -> bool:
    """Both halves must be present. One alone is a misconfiguration, and
    enforcing with a site key but no secret would reject every submission."""
    return bool(site_key() and secret_key())


def verify(token: str, remote_ip: str | None = None,
           expected_action: str | None = None) -> tuple[bool, str]:
    """Check a reCAPTCHA token. Returns ``(ok, reason)``.

    ``reason`` is for the log, never for the page: telling a bot which check
    it failed is free tuning feedback.
    """
    if not is_enabled():
        return True, "disabled"

    if not token:
        return False, "no token submitted"

    payload = {"secret": secret_key(), "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        request = urllib.request.Request(
            VERIFY_URL,
            data=urllib.parse.urlencode(payload).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        # Fail open. An outage at Google is not a reason nobody can sign in.
        print(f"[recaptcha] verification unavailable, allowing: {exc}")
        return True, f"verifier unreachable ({exc})"

    if not body.get("success"):
        codes = ",".join(body.get("error-codes") or []) or "unknown"
        return False, f"rejected ({codes})"

    # v3 only. A missing score means v2, where success is the whole answer.
    score = body.get("score")
    if score is not None:
        try:
            score = float(score)
        except (TypeError, ValueError):
            return True, "unreadable score, accepted"
        if score < score_threshold():
            return False, f"score {score:.2f} below {score_threshold():.2f}"

    # v3 signs the action name, so a token minted on one form cannot be
    # replayed against another.
    action = body.get("action")
    if expected_action and action and action != expected_action:
        return False, f"action {action!r} != {expected_action!r}"

    return True, f"ok (score={score})" if score is not None else "ok"


def widget_context() -> dict:
    """What the template needs to render the right widget, or nothing."""
    return {
        "recaptcha_enabled": is_enabled(),
        "recaptcha_site_key": site_key(),
        "recaptcha_version": version(),
    }
