"""What a usage event may contain, and which requests become one.

Pure. No Flask, no database, no clock. The rules that decide whether
something is recorded at all live here so they can be read in one place --
this is the module a privacy review should be able to finish in five
minutes.

Three principles, enforced below rather than promised:

* **Routes, not URLs.** Events carry the Flask *rule* ("/groups/<int:id>"),
  never the resolved path. A student's own identifiers, tokens and search
  terms live in resolved URLs, and once they are in an events table they
  are in every export of it.
* **An allowlist, not a blocklist.** Client-sent event names and property
  keys are matched against fixed sets. A blocklist means the next feature
  ships whatever it likes and nobody notices.
* **Counts, not content.** Property values are numbers, booleans and short
  enum-ish strings. Never free text the student typed.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

__all__ = [
    "CLIENT_EVENTS",
    "ALLOWED_PROP_KEYS",
    "SENSITIVE_RULE_PATTERNS",
    "VIEW",
    "ACTION",
    "sanitize_props",
    "is_allowed_event",
    "record_kind",
    "should_record_rule",
    "classify_channel",
    "referrer_host",
]

VIEW = "view"
ACTION = "action"

#: Client-reported events. Each one answers a question the server cannot:
#: something the student saw, chose, or abandoned without a request.
CLIENT_EVENTS: frozenset[str] = frozenset({
    "onboarding_step_shown",
    "onboarding_abandoned",
    "connect_school_opened",
    "connect_school_failed",
    "plan_viewed",
    "plan_block_checked",
    "invite_link_copied",
    "invite_shared",
    "paywall_shown",
    "paywall_upgrade_clicked",
    "prompt_shown",
    "prompt_answered",
    "prompt_dismissed",
    "install_prompt_shown",
    "install_accepted",
})

#: Property keys a client event may carry. Values are coerced in
#: sanitize_props; anything not listed is dropped silently rather than
#: rejected, so a stale client cannot lose the whole event.
ALLOWED_PROP_KEYS: frozenset[str] = frozenset({
    "step", "provider", "reason", "surface", "count", "seconds",
    "source", "variant", "ok", "index",
})

#: Rules never recorded, however the gate is configured. Auth and billing
#: endpoints carry secrets in their paths; cron and webhooks are machines;
#: the consent endpoints must not themselves be tracked.
SENSITIVE_RULE_PATTERNS: tuple[str, ...] = (
    "/login", "/logout", "/register", "/password", "/reset", "/verify",
    "/oauth", "/callback", "/token", "/consent", "/cookies", "/unsubscribe",
    "/cron", "/webhook", "/admin", "/pay/", "/email/", "/api/sync",
    # The insight endpoints themselves: recording the recorder produces a
    # row per event and a funnel made of our own instrumentation.
    "/api/insight",
    "/static", "/ref/", "/desktop", "/.well-known",
)

_SAFE_VALUE = re.compile(r"^[A-Za-z0-9 _.:-]{0,40}$")
#: Known referrer hosts worth naming. Everything else becomes its
#: registrable-ish host, which is public information about the link, not
#: about the person following it.
_CHANNELS: tuple[tuple[str, str], ...] = (
    ("google.", "search"), ("bing.", "search"), ("duckduckgo.", "search"),
    ("ecosia.", "search"), ("search.yahoo.", "search"),
    ("tiktok.", "tiktok"), ("instagram.", "instagram"),
    ("youtube.", "youtube"), ("youtu.be", "youtube"),
    ("reddit.", "reddit"), ("discord.", "discord"),
    ("snapchat.", "snapchat"), ("facebook.", "facebook"),
    ("x.com", "x"), ("twitter.", "x"), ("t.co", "x"),
    ("linkedin.", "linkedin"), ("pinterest.", "pinterest"),
    ("chatgpt.", "ai-assistant"), ("perplexity.", "ai-assistant"),
    ("classroom.google", "google-classroom"), ("instructure.", "canvas"),
)


def is_allowed_event(name: Any) -> bool:
    return isinstance(name, str) and name in CLIENT_EVENTS


def sanitize_props(props: Any, *, max_keys: int = 8) -> dict[str, Any]:
    """Keep allowlisted keys with small, safe values. Drop everything else."""
    if not isinstance(props, Mapping):
        return {}
    out: dict[str, Any] = {}
    for key, value in props.items():
        if key not in ALLOWED_PROP_KEYS or len(out) >= max_keys:
            continue
        if isinstance(value, bool):
            out[key] = value
        elif isinstance(value, int) and not isinstance(value, bool):
            out[key] = max(-10**6, min(10**6, value))
        elif isinstance(value, float):
            out[key] = round(max(-10**6.0, min(10**6.0, value)), 2)
        elif isinstance(value, str) and _SAFE_VALUE.match(value):
            # Matched, not truncated: a string that needed truncating was
            # not an enum value, and is the shape free text arrives in.
            out[key] = value
    return out


def should_record_rule(rule: Any, patterns: Iterable[str] = SENSITIVE_RULE_PATTERNS) -> bool:
    """False for unmatched routes and for anything on the sensitive list.

    An unmatched request (404, or a probe) has no rule, and recording its
    raw path is exactly the leak the rule-not-URL principle exists to stop.
    """
    if not isinstance(rule, str) or not rule.startswith("/"):
        return False
    lowered = rule.lower()
    return not any(p in lowered for p in patterns)


def record_kind(method: str, is_html: bool) -> str | None:
    """A page a student looked at, a thing they did, or neither."""
    upper = (method or "").upper()
    if upper == "GET":
        return VIEW if is_html else None
    if upper in {"POST", "PUT", "PATCH", "DELETE"}:
        return ACTION
    return None


def referrer_host(referrer: Any, own_host: str = "") -> str:
    """Host only, and never our own -- an internal link is not a channel."""
    if not isinstance(referrer, str) or "//" not in referrer:
        return ""
    host = referrer.split("//", 1)[1].split("/", 1)[0].split(":")[0].lower()
    if not host or (own_host and host == own_host.lower()):
        return ""
    return host[:80]


def classify_channel(utm_source: Any = "", referrer: Any = "", own_host: str = "") -> str:
    """Where a visit came from, in words we can act on.

    ``direct`` means "we do not know", which is the honest label: it covers
    typed URLs, messaging apps that strip referrers, and every link opened
    from a native app. Reading it as "brand strength" is how attribution
    reports start lying.
    """
    if isinstance(utm_source, str) and utm_source.strip():
        cleaned = utm_source.strip().lower()[:40]
        return cleaned if _SAFE_VALUE.match(cleaned) else "other"
    host = referrer_host(referrer, own_host)
    if not host:
        return "direct"
    for needle, label in _CHANNELS:
        if needle in host:
            return label
    return host
