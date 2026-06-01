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
admin would register an additional dev key and set CANVAS_CLIENT_ID_<HOST>
overrides — kept simple for now with a single global key.
"""

from __future__ import annotations

import os
import urllib.parse

import requests as http_requests

DEFAULT_CANVAS_BASE = os.getenv("CANVAS_DEFAULT_BASE", "https://canvas.instructure.com")
DEFAULT_REDIRECT_URI = "https://intelliplan.tech/oauth/canvas/callback"


def _redirect_uri(redirect_uri=None):
    return redirect_uri or os.getenv("CANVAS_REDIRECT_URI") or DEFAULT_REDIRECT_URI


def _host_key_suffix(canvas_base):
    """Build the env-var suffix used to look up per-instance Developer Keys.

    e.g. https://canvas.school.edu -> "CANVAS_SCHOOL_EDU"
         https://canvas.instructure.com -> "CANVAS_INSTRUCTURE_COM"

    Multi-tenant deploys can set:
        CANVAS_CLIENT_ID_CANVAS_INSTRUCTURE_COM
        CANVAS_CLIENT_SECRET_CANVAS_INSTRUCTURE_COM
        CANVAS_CLIENT_ID_CANVAS_SCHOOL_EDU
        CANVAS_CLIENT_SECRET_CANVAS_SCHOOL_EDU
    to register one Developer Key per Canvas instance. Falls back to the
    global CANVAS_CLIENT_ID / CANVAS_CLIENT_SECRET when no override exists.
    """
    base = _normalize_base(canvas_base)
    host = base.split("://", 1)[-1].split("/", 1)[0]
    return host.replace(".", "_").replace("-", "_").upper()


def _client_credentials(canvas_base):
    """Return (client_id, client_secret) for this Canvas base, preferring
    a per-instance override before the global key."""
    suffix = _host_key_suffix(canvas_base) if canvas_base else None
    client_id = (
        (os.getenv(f"CANVAS_CLIENT_ID_{suffix}") if suffix else None)
        or os.getenv("CANVAS_CLIENT_ID")
    )
    client_secret = (
        (os.getenv(f"CANVAS_CLIENT_SECRET_{suffix}") if suffix else None)
        or os.getenv("CANVAS_CLIENT_SECRET")
    )
    return client_id, client_secret


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
    client_id, _ = _client_credentials(base)
    if not client_id:
        raise RuntimeError(
            f"No Canvas Developer Key registered for {base}. Set CANVAS_CLIENT_ID "
            f"(global) or CANVAS_CLIENT_ID_{_host_key_suffix(base)} (per-instance)."
        )
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": _redirect_uri(redirect_uri),
        "state": state,
        "purpose": "IntelliPlan study planner",
    }
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
    resp = http_requests.post(
        f"{base}/login/oauth2/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": _redirect_uri(redirect_uri),
            "code": code,
        },
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
    resp = http_requests.post(
        f"{base}/login/oauth2/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
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
        http_requests.delete(
            f"{base}/login/oauth2/token",
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
