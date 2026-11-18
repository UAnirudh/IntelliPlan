"""
canvas_oauth.py — Canvas LMS OAuth2 flow for IntelliPlan

Canvas's OAuth is per-instance (Instructure-hosted SaaS, school-hosted, or
Canvas Free For Teacher) so the auth/token URLs are derived from the
user's Canvas base URL (e.g. https://canvas.instructure.com or
https://canvas.school.edu).

Mirrors the pattern in google_calendar_helper.py:
    get_canvas_auth_url(state, canvas_base, redirect_uri=None)
    exchange_canvas_code(code, canvas_base, redirect_uri=None)
    refresh_canvas_token(token_dict, canvas_base)

The IntelliPlan admin must register a Developer Key on the Canvas
instance and set:
    CANVAS_CLIENT_ID, CANVAS_CLIENT_SECRET
    CANVAS_REDIRECT_URI (defaults to https://intelliplan.tech/oauth/canvas/callback)

Multi-tenant Canvas note: a single developer key only works against the
Canvas instance that issued it. For schools running their own Canvas, the
admin must register an additional dev key and set a complete
CANVAS_CLIENT_ID_ORIGIN_<HEX> / CANVAS_CLIENT_SECRET_ORIGIN_<HEX> pair.
"""

from __future__ import annotations

import os
import urllib.parse

import net_guard

DEFAULT_CANVAS_BASE = os.getenv("CANVAS_DEFAULT_BASE", "https://canvas.instructure.com")
DEFAULT_REDIRECT_URI = "https://intelliplan.tech/oauth/canvas/callback"

#: The read-only Canvas endpoints IntelliPlan actually calls. A Developer Key
#: with "Enforce Scopes" turned off ignores these, but a key with it turned on
#: rejects the whole authorization unless every endpoint we later call was
#: named here at authorize time. School Canvas admins routinely enforce scopes
#: before approving a third-party key, so asking for exactly this list is what
#: makes a school-hosted instance work at all.
#:
#: Keep this in sync with canvas_helper.py: an endpoint called but not listed
#: fails at request time with a 401 that looks like an expired token.
DEFAULT_SCOPES = (
    "url:GET|/api/v1/courses",
    "url:GET|/api/v1/courses/:course_id/assignments",
    "url:GET|/api/v1/courses/:course_id/assignments/:id",
    "url:GET|/api/v1/files/:id",
    "url:GET|/api/v1/courses/:course_id/enrollments",
    "url:GET|/api/v1/users/:user_id/enrollments",
    "url:GET|/api/v1/users/:user_id/courses",
    "url:GET|/api/v1/courses/:course_id/students/submissions",
    "url:GET|/api/v1/courses/:course_id/assignment_groups",
    "url:GET|/api/v1/users/self/upcoming_events",
)


def _redirect_uri(redirect_uri=None):
    return redirect_uri or os.getenv("CANVAS_REDIRECT_URI") or DEFAULT_REDIRECT_URI


def configured_scopes():
    """The scopes to request, as a tuple. Empty means "send no scope param".

    Scoping is opt-in, and deliberately so. Canvas rejects an authorization
    that names scopes when the Developer Key has "Enforce Scopes" turned off,
    so sending our list by default would break every unscoped key that works
    today. The admin who turns enforcement on is the one who knows, so they
    are the one who says so:

        CANVAS_SCOPES unset      -> no scope param (unscoped key, the default)
        CANVAS_SCOPES=default    -> the DEFAULT_SCOPES list above
        CANVAS_SCOPES=<list>     -> exactly that list, space- or comma-separated

    Anything set on a per-origin basis (``CANVAS_SCOPES_ORIGIN_<HEX>``)
    wins for that origin, because enforcement is a per-instance decision: one
    school can enforce scopes while the public Canvas does not.
    """
    return _scopes_for(None)


def _scopes_for(canvas_base):
    suffix = _host_key_suffix(canvas_base) if canvas_base else None
    raw = None
    if suffix:
        raw = os.getenv(f"CANVAS_SCOPES_ORIGIN_{suffix}")
    if raw is None:
        raw = os.getenv("CANVAS_SCOPES")
    if raw is None:
        return ()
    if raw.strip().lower() in ("default", "auto", "1", "true", "yes", "on"):
        return tuple(DEFAULT_SCOPES)
    parts = [p.strip() for p in raw.replace(",", " ").split() if p.strip()]
    return tuple(parts)


def _host_key_suffix(canvas_base):
    """Return a collision-resistant suffix for one normalized Canvas origin."""
    base = _normalize_base(canvas_base)
    try:
        parsed = urllib.parse.urlsplit(base)
        if not parsed.hostname:
            return ""
        scheme = parsed.scheme.lower()
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError:
        return ""
    origin = f"{scheme}://{parsed.hostname.lower()}:{port}"
    return origin.encode("utf-8").hex().upper()


def _client_credentials(canvas_base):
    """Return only credentials explicitly bound to this Canvas origin."""
    base = _normalize_base(canvas_base)
    suffix = _host_key_suffix(base)
    instance_id = os.getenv(f"CANVAS_CLIENT_ID_ORIGIN_{suffix}") if suffix else None
    instance_secret = os.getenv(f"CANVAS_CLIENT_SECRET_ORIGIN_{suffix}") if suffix else None
    if instance_id or instance_secret:
        # A partial override is authoritative. Never combine it with a
        # different origin's global key.
        return instance_id, instance_secret
    if _same_origin(base, DEFAULT_CANVAS_BASE):
        return os.getenv("CANVAS_CLIENT_ID"), os.getenv("CANVAS_CLIENT_SECRET")
    return None, None


def _same_origin(left, right):
    """Compare URL origins, including scheme and effective port."""
    try:
        a = urllib.parse.urlsplit(_normalize_base(left))
        b = urllib.parse.urlsplit(_normalize_base(right))
        if not a.hostname or not b.hostname:
            return False
        a_port = a.port or (443 if a.scheme.lower() == "https" else 80)
        b_port = b.port or (443 if b.scheme.lower() == "https" else 80)
        return (a.scheme.lower(), a.hostname.lower(), a_port) == (
            b.scheme.lower(), b.hostname.lower(), b_port)
    except ValueError:
        return False


def _normalize_base(canvas_base):
    """Strip trailing slashes; default to Instructure SaaS if blank."""
    base = (canvas_base or "").strip().rstrip("/")
    if not base:
        return DEFAULT_CANVAS_BASE
    if not base.startswith("http://") and not base.startswith("https://"):
        base = "https://" + base
    return base


def get_canvas_auth_url(state, canvas_base=None, redirect_uri=None, scopes=None):
    """Build the Canvas OAuth authorization URL.

    Canvas accepts these query params:
      - client_id (developer key id)
      - response_type=code
      - redirect_uri
      - state
      - scope (optional, space-delimited)
      - purpose (optional, shown to user)
    """
    base = _normalize_base(canvas_base)
    client_id, client_secret = _client_credentials(base)
    if not client_id or not client_secret:
        raise RuntimeError(
            f"No Canvas Developer Key registered for {base}. Set the global "
            f"CANVAS_CLIENT_ID/CANVAS_CLIENT_SECRET pair for the configured "
            f"default origin, or a complete CANVAS_CLIENT_ID_ORIGIN_{_host_key_suffix(base)} "
            f"/ CANVAS_CLIENT_SECRET_ORIGIN_{_host_key_suffix(base)} pair for this origin."
        )
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": _redirect_uri(redirect_uri),
        "state": state,
        "purpose": "IntelliPlan study planner",
    }
    # An explicit argument wins; otherwise fall back to what this host is
    # configured for, which is nothing unless an admin opted into scoping.
    if scopes is None:
        scopes = _scopes_for(base)
    if scopes:
        params["scope"] = " ".join(scopes)
    return f"{base}/login/oauth2/auth?{urllib.parse.urlencode(params)}"


def exchange_canvas_code(code, canvas_base, redirect_uri=None):
    """Exchange the authorization `code` for an access + refresh token.

    Canvas's response includes user info (the `user` field) which we keep
    so we can show the linked account in IntelliPlan settings without an
    extra API call.
    """
    base = _normalize_base(canvas_base)
    client_id, client_secret = _client_credentials(base)
    if not client_id or not client_secret:
        raise RuntimeError(f"Canvas OAuth credentials missing for {base}.")
    form = urllib.parse.urlencode({
        "grant_type": "authorization_code", "client_id": client_id,
        "client_secret": client_secret, "redirect_uri": _redirect_uri(redirect_uri),
        "code": code,
    })
    resp = net_guard.pinned_https_request(
        f"{base}/login/oauth2/token",
        "POST", headers={"Content-Type": "application/x-www-form-urlencoded"}, data=form,
        timeout=20,
    )
    data = resp.json()
    if resp.status_code >= 400 or "error" in data:
        raise Exception(f"Canvas token exchange failed: {data}")
    return {
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        "token_type": data.get("token_type", "Bearer"),
        "expires_in": data.get("expires_in"),
        "user": data.get("user") or {},
        "canvas_base": base,
    }


def refresh_canvas_token(refresh_token, canvas_base):
    """Refresh an expired Canvas access token. Canvas access tokens
    expire (typically 1 hour) so this MUST be called before each batch
    of API calls if the cached token is older than ~55 minutes.
    """
    base = _normalize_base(canvas_base)
    client_id, client_secret = _client_credentials(base)
    if not client_id or not client_secret:
        raise RuntimeError(f"Canvas OAuth credentials missing for {base}.")
    form = urllib.parse.urlencode({
        "grant_type": "refresh_token", "client_id": client_id,
        "client_secret": client_secret, "refresh_token": refresh_token,
    })
    resp = net_guard.pinned_https_request(
        f"{base}/login/oauth2/token",
        "POST", headers={"Content-Type": "application/x-www-form-urlencoded"}, data=form,
        timeout=20,
    )
    data = resp.json()
    if resp.status_code >= 400 or "error" in data:
        raise Exception(f"Canvas token refresh failed: {data}")
    return {
        "access_token": data.get("access_token"),
        "token_type": data.get("token_type", "Bearer"),
        "expires_in": data.get("expires_in"),
    }


def revoke_canvas_token(access_token, canvas_base):
    """Revoke a Canvas access token (best-effort)."""
    base = _normalize_base(canvas_base)
    try:
        net_guard.pinned_https_request(
            f"{base}/login/oauth2/token",
            "DELETE",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
    except Exception:
        pass


def oauth_is_configured(canvas_base=None):
    """True when the global Canvas Developer Key is set OR a per-instance
    override exists for the given canvas_base. Used to decide whether to
    show the "Continue with Canvas" button vs. the token-paste fallback."""
    cid, sec = _client_credentials(canvas_base)
    return bool(cid and sec)


def oauth_any_configured():
    """True when *some* Canvas Developer Key exists -- global or for any
    single school.

    The login page needs this rather than ``oauth_is_configured()`` with no
    argument. That call only sees the global key, so a deploy carrying only
    per-instance keys reported False and the template hid the entire OAuth
    block -- including the probe script whose whole job is to ask, per URL,
    whether that school is registered. The per-instance feature could
    therefore never light up for anyone unless a global key happened to
    exist too, which is the opposite of what it is for.

    Rendering the block on this looser test is safe: the button starts
    disabled and the probe enables it only for a base that really is
    registered, so a student at an unregistered school still sees the
    token instructions.
    """
    if oauth_is_configured():
        return True
    for name in os.environ:
        if not name.startswith("CANVAS_CLIENT_ID_"):
            continue
        suffix = name[len("CANVAS_CLIENT_ID_"):]
        if os.getenv(name) and os.getenv(f"CANVAS_CLIENT_SECRET_{suffix}"):
            return True
    return False
