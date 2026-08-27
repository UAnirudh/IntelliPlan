"""Versioned Terms and Privacy Policy changes, and what changed in each.

A policy change is only meaningful if the people bound by it are told, in
words they can act on, before they keep using the product. That needs three
things this module holds together:

  * a version number the app can compare against what a user last accepted
  * a plain-language summary of what moved
  * the changed clauses verbatim, because a summary is an interpretation and
    the binding text is the text

Adding a version:
  1. Append an entry to ``TERMS_VERSIONS`` or ``PRIVACY_VERSIONS``.
  2. Bump ``version`` past every earlier entry.
  3. Fill ``summary`` with what a student would care about, and ``clauses``
     with the exact wording that changed.
Users who accepted an older version are then asked to read and accept.

The very first version is the baseline: everyone is treated as having
accepted it, so shipping this does not interrupt existing users. Only a
later version prompts.
"""

from __future__ import annotations

from typing import Any

TERMS = "terms"
PRIVACY = "privacy"

#: Document key -> human name and canonical URL.
POLICY_DOCS: dict[str, dict[str, str]] = {
    TERMS: {"name": "Terms of Service", "url": "/terms"},
    PRIVACY: {"name": "Privacy Policy", "url": "/privacy"},
}


TERMS_VERSIONS: list[dict[str, Any]] = [
    {
        "version": 1,
        "effective": "2025-01-01",
        "baseline": True,
        "summary": ["The original Terms of Service."],
        "clauses": [],
    },
]

PRIVACY_VERSIONS: list[dict[str, Any]] = [
    {
        "version": 1,
        "effective": "2025-01-01",
        "baseline": True,
        "summary": ["The original Privacy Policy."],
        "clauses": [],
    },
    {
        # Microsoft Clarity — session replay and heatmaps — had been loading
        # on every page while §1 stated we did not use session replay. The
        # tracker was removed rather than disclosed, so the original promise
        # is true again. Telling people is the other half of that: a policy
        # that changes silently is not a policy anyone can rely on, and this
        # is exactly the change the notice mechanism was built for.
        "version": 2,
        "effective": "2026-08-26",
        "summary": [
            "We removed a third-party analytics tool (Microsoft Clarity) that "
            "had been loading in your browser. No third-party analytics script "
            "runs on IntelliPlan any more.",
            "We were already telling you we do not use session replay. For a "
            "period that was not accurate, and we are telling you rather than "
            "quietly correcting it.",
            "We added a Cookie Policy listing everything stored in your "
            "browser, what each item does, and how long it lasts.",
            "Nothing new is collected. This change only removes things and "
            "describes what remains more precisely.",
        ],
        "clauses": [
            {
                "heading": "1. Information we collect — Usage telemetry",
                "before": (
                    "Usage telemetry — anonymous error logs and basic event "
                    "counts (e.g. “schedule generated”). We do not use "
                    "session-replay, screen recording, keystroke loggers, or "
                    "third-party advertising trackers."
                ),
                "after": (
                    "Usage telemetry — anonymous error logs and basic event "
                    "counts (e.g. “schedule generated”), recorded on our "
                    "own servers. We do not use session-replay, screen "
                    "recording, heatmaps, keystroke loggers, third-party "
                    "advertising trackers, retargeting pixels, or any "
                    "cross-site tracking. No third-party analytics script runs "
                    "in your browser."
                ),
            },
            {
                "heading": "10a. Cookies & browser storage (new section)",
                "before": "No cookie section existed.",
                "after": (
                    "We use cookies to keep you signed in and to remember your "
                    "settings. We run no analytics, advertising or tracking "
                    "cookies of any kind, so there is nothing optional to "
                    "consent to and no cookie banner to dismiss.\n\n"
                    "Strictly necessary — your sign-in session, the “stay "
                    "signed in” token, and the cookie that records your "
                    "cookie choice. These cannot be switched off, because "
                    "without them there is no sign-in.\n\n"
                    "On-device preferences — your theme, accessibility "
                    "settings, study-session progress and dismissed prompts "
                    "are kept in your browser's localStorage. These never "
                    "reach our servers. We list them anyway, because the law "
                    "covers storage on your device whatever it is called.\n\n"
                    "No advertising cookies — we run no ad tech, no "
                    "retargeting pixels, and no cross-site tracking of any "
                    "kind."
                ),
            },
        ],
    },
]

_VERSIONS: dict[str, list[dict[str, Any]]] = {
    TERMS: TERMS_VERSIONS,
    PRIVACY: PRIVACY_VERSIONS,
}


def current_version(doc: str) -> int:
    """The version a user must have accepted to be up to date."""
    versions = _VERSIONS.get(doc) or []
    return max((v["version"] for v in versions), default=1)


def baseline_version(doc: str) -> int:
    """The highest version that predates the acknowledgement system.

    Existing users are treated as having accepted this, so turning the
    feature on does not confront everyone with a notice about a document
    that has not actually changed for them.
    """
    versions = [v["version"] for v in (_VERSIONS.get(doc) or []) if v.get("baseline")]
    return max(versions, default=0)


def versions_after(doc: str, accepted: int) -> list[dict[str, Any]]:
    """Every version newer than what the user accepted, oldest first.

    Returned in order so someone who missed two updates reads them in the
    sequence they happened rather than only the latest.
    """
    versions = _VERSIONS.get(doc) or []
    return sorted(
        (v for v in versions if v["version"] > accepted and not v.get("baseline")),
        key=lambda v: v["version"],
    )


def describe(doc: str, accepted: int) -> dict[str, Any] | None:
    """What to show a user whose accepted version is out of date.

    ``None`` when they are current. Otherwise the merged summary and the
    verbatim clauses from every version they have not seen.
    """
    pending = versions_after(doc, accepted)
    if not pending:
        return None

    summary: list[str] = []
    clauses: list[dict[str, str]] = []
    for version in pending:
        summary.extend(version.get("summary") or [])
        clauses.extend(version.get("clauses") or [])

    return {
        "doc": doc,
        "name": POLICY_DOCS[doc]["name"],
        "url": POLICY_DOCS[doc]["url"],
        "from_version": accepted,
        "version": pending[-1]["version"],
        "effective": pending[-1].get("effective"),
        "summary": summary,
        "clauses": clauses,
    }


def all_docs() -> list[str]:
    return list(POLICY_DOCS)
