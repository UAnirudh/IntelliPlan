"""Lightweight PostHog analytics wrapper.

Initialises the PostHog Python SDK from the ``POSTHOG_API_KEY`` env var.
If the key is missing, every call silently no-ops so dev environments
work without configuration.
"""

from __future__ import annotations

import os
from typing import Any, Optional


_client: Any = None
_disabled: bool = False
#: Consent gate, installed by the web app (see insight_glue). Until one is
#: set, every call is a no-op even with a key configured: PostHog is a
#: third party receiving a user id, and sending one for a visitor who has
#: not agreed -- or for a child -- is the thing the cookie banner exists to
#: prevent. A missing gate means "we do not know", and "we do not know" is
#: not consent.
_gate: Any = None


def set_consent_gate(gate: Any) -> None:
    """Install a ``() -> bool`` consulted before every event."""
    global _gate
    _gate = gate


def _permitted() -> bool:
    if _gate is None:
        return False
    try:
        return bool(_gate())
    except Exception:
        return False


def _init() -> None:
    global _client, _disabled
    if _client is not None or _disabled:
        return
    api_key = os.environ.get("POSTHOG_API_KEY", "")
    host = os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com")
    if not api_key:
        _disabled = True
        return
    try:
        import posthog
        posthog.project_api_key = api_key
        posthog.host = host
        _client = posthog
    except ImportError:
        print("[analytics] posthog package not installed — events will be no-ops")
        _disabled = True


def track(
    user_id: int | str,
    event: str,
    properties: Optional[dict] = None,
) -> None:
    """Fire an analytics event. No-ops when PostHog is not configured."""
    if not _permitted():
        return
    _init()
    if _disabled or _client is None:
        return
    _client.capture(
        distinct_id=str(user_id),
        event=event,
        properties=properties or {},
    )


def identify(
    user_id: int | str,
    properties: Optional[dict] = None,
) -> None:
    """Set user properties (e.g. cohort assignment)."""
    if not _permitted():
        return
    _init()
    if _disabled or _client is None:
        return
    _client.identify(
        distinct_id=str(user_id),
        properties=properties or {},
    )


def flush() -> None:
    """Force-flush the event queue (useful at shutdown)."""
    _init()
    if _disabled or _client is None:
        return
    try:
        _client.flush()
    except Exception:
        pass
