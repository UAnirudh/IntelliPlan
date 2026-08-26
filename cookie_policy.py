"""What IntelliPlan stores in a browser, and on what legal basis.

Kept as data rather than prose so the banner, the policy page, the consent
gate and the tests all read the same list. A cookie policy that has drifted
from the code is worse than none: it is a written statement that happens to
be false.

Two categories, and the split is the whole point:

``essential``   Needed for the site to work at all. No consent required
                under ePrivacy, and nothing here tracks anyone.
``analytics``   Everything else. Loaded only after the visitor says yes, and
                never for a child under 13.

Adding anything that writes to a browser means adding it here first.
"""

from __future__ import annotations

import os
from typing import Any

ESSENTIAL = "essential"
ANALYTICS = "analytics"

CATEGORIES: dict[str, dict[str, str]] = {
    ESSENTIAL: {
        "name": "Strictly necessary",
        "summary": "Keeps you signed in and keeps the site secure. "
                   "IntelliPlan cannot work without these, so they are always on.",
    },
    ANALYTICS: {
        "name": "Analytics",
        "summary": "Helps us see which parts of IntelliPlan are confusing or "
                   "broken. Off unless you turn it on, and never used for "
                   "advertising.",
    },
}


#: Every cookie or browser-storage key IntelliPlan is responsible for.
#: ``storage`` distinguishes real cookies from localStorage, because
#: ePrivacy covers both and calling localStorage "not a cookie" is the
#: oldest dodge in the book.
COOKIES: list[dict[str, Any]] = [
    {
        "name": "session",
        "category": ESSENTIAL,
        "storage": "cookie",
        "provider": "IntelliPlan",
        "purpose": "Keeps you signed in and holds your guest session so work "
                   "you do before making an account is not lost.",
        "duration": "Session, or 31 days if you choose to stay signed in",
    },
    {
        "name": "remember_token",
        "category": ESSENTIAL,
        "storage": "cookie",
        "provider": "IntelliPlan",
        "purpose": "Remembers you between visits when you tick "
                   "“stay signed in”.",
        "duration": "31 days",
    },
    {
        "name": "ip_cookie_consent",
        "category": ESSENTIAL,
        "storage": "cookie",
        "provider": "IntelliPlan",
        "purpose": "Records this exact choice, so we stop asking. Removing it "
                   "makes the banner return.",
        "duration": "12 months",
    },
    {
        "name": "theme, ip_theme_palette, ip_a11y*",
        "category": ESSENTIAL,
        "storage": "localStorage",
        "provider": "IntelliPlan",
        "purpose": "Your appearance and accessibility settings — dark mode, "
                   "colour palette, dyslexia-friendly font, reading level, "
                   "language. Stored on your device only; never sent to us.",
        "duration": "Until you clear your browser data",
    },
    {
        "name": "ip_study, ip_focus_*, ip_checklist_state_v2, intelliplan_srs_v1",
        "category": ESSENTIAL,
        "storage": "localStorage",
        "provider": "IntelliPlan",
        "purpose": "Your place in a study session, focus timer, checklists and "
                   "flashcard schedule, so a refresh does not lose your work. "
                   "Stored on your device only.",
        "duration": "Until you clear your browser data",
    },
    {
        "name": "ip_tour_done, installBannerDismissed, ip_fb_hidden",
        "category": ESSENTIAL,
        "storage": "localStorage",
        "provider": "IntelliPlan",
        "purpose": "Remembers that you have dismissed a tour, prompt or banner "
                   "so it does not reappear on every page.",
        "duration": "Until you clear your browser data",
    },
    {
        "name": "_clck, _clsk, CLID",
        "category": ANALYTICS,
        "storage": "cookie",
        "provider": "Microsoft Clarity",
        "purpose": "Records how pages are used — clicks, scrolling, and "
                   "session replays — so we can find what is broken or "
                   "confusing. Not used for advertising.",
        "duration": "Up to 12 months",
        "policy_url": "https://privacy.microsoft.com/privacystatement",
    },
]


def clarity_project_id() -> str:
    """The Microsoft Clarity project, or empty to disable it entirely.

    Was hardcoded in the page template, which meant the only way to stop
    session recording was to edit and redeploy HTML. As an env var, turning
    analytics off across the whole product is a config change.
    """
    return (os.getenv("CLARITY_PROJECT_ID") or "").strip()


def analytics_available() -> bool:
    return bool(clarity_project_id())


def cookies_for(category: str) -> list[dict[str, Any]]:
    return [c for c in COOKIES if c["category"] == category]


def categories_payload() -> list[dict[str, Any]]:
    """Everything the banner and the policy page need to render."""
    out = []
    for key, meta in CATEGORIES.items():
        if key == ANALYTICS and not analytics_available():
            # Nothing to consent to, so do not offer a switch that does nothing.
            continue
        out.append({
            "key": key,
            "name": meta["name"],
            "summary": meta["summary"],
            "required": key == ESSENTIAL,
            "cookies": cookies_for(key),
        })
    return out


LAST_UPDATED = "26 August 2026"

#: Bumped when the categories or their purposes change materially, which
#: makes previously-given consent stale and re-asks.
CONSENT_VERSION = 1

CONSENT_COOKIE = "ip_cookie_consent"
CONSENT_MAX_AGE = 60 * 60 * 24 * 365  # 12 months


def parse_consent(raw: str | None) -> dict[str, Any] | None:
    """Read the consent cookie. ``None`` means we still have to ask.

    Format is ``v<version>:<granted categories, comma separated>``. Kept
    deliberately small and opaque-free: a value a user can read and
    understand is one they can meaningfully withdraw.
    """
    if not raw:
        return None
    try:
        version_part, _, granted_part = str(raw).partition(":")
        if not version_part.startswith("v"):
            return None
        version = int(version_part[1:])
    except (TypeError, ValueError):
        return None

    if version != CONSENT_VERSION:
        return None  # our terms changed; ask again

    granted = {g for g in granted_part.split(",") if g in CATEGORIES}
    granted.add(ESSENTIAL)  # always on, by definition
    return {"version": version, "granted": sorted(granted)}


def serialize_consent(granted: list[str]) -> str:
    allowed = sorted({g for g in granted if g in CATEGORIES} | {ESSENTIAL})
    return f"v{CONSENT_VERSION}:" + ",".join(allowed)


def has_analytics_consent(raw: str | None) -> bool:
    parsed = parse_consent(raw)
    return bool(parsed and ANALYTICS in parsed["granted"])
