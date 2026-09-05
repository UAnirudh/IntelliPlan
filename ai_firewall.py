"""Cost and abuse control in front of every AI call.

Three holes this closes, all of them live in production before this module:

1. Guests were unlimited. ``_check_and_increment_tutor_limit`` returned
   ``(True, None, None)`` for anyone not signed in, so the tutor answered
   forever with no account and no counter. One script could spend the whole
   Gemini and Groq allowance for every real student.

2. Rate limits lived in process memory. Flask-Limiter says so at startup, and
   Railway runs more than one instance: the effective ceiling was the
   configured one multiplied by the instance count, and every deploy reset it
   to zero.

3. Keying was by IP alone, which punishes a school behind one NAT and is
   beaten by any VPN.

The counters here live in the database, so they survive a deploy and are
shared by every instance. They are keyed on the account first and fall back
to a signed device cookie, so a guest keeps their identity across IP changes
and a household on one address is not treated as one student.

Nothing in here is a client-side check. The caller passes the feature and the
tokens it wants; the answers come from the database and from the config, and
a request that has spent its allowance never reaches a provider.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from flask import current_app, g, request
from sqlalchemy import (Column, DateTime, Index, Integer, MetaData, String,
                        Table, text)

logger = logging.getLogger(__name__)

GUEST_COOKIE = "ip_dev"
#: How long a guest identity survives. Long enough that clearing it is a
#: deliberate act, short enough that a shared library machine recycles.
GUEST_COOKIE_DAYS = 180

_META = MetaData()

#: One row per (subject, window, metric). Incremented atomically; never read
#: for anything but a limit decision, so it holds no message content.
QUOTA_TABLE = Table(
    "ai_quota_counters", _META,
    Column("id", Integer, primary_key=True),
    Column("subject", String(96), nullable=False),
    Column("window_key", String(32), nullable=False),
    Column("metric", String(16), nullable=False),
    Column("count", Integer, nullable=False, default=0),
    Column("updated_at", DateTime, nullable=False, default=lambda: _now()),
    Index("ux_ai_quota_subject_window", "subject", "window_key", "metric", unique=True),
)

_TABLE_READY = False


def _now() -> datetime:
    """Naive UTC. The column is naive, and mixing the two raises on Postgres."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Plan resolution ───────────────────────────────────────────────

def plan_for(user) -> str:
    """``"paid"`` or ``"free"`` for this user.

    Billing does not exist yet, so this reads whichever marker lands first --
    a plan column, the pro_active flag chatbot_api already probes, or an
    explicit allowlist for testing the paid path before checkout ships.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return "free"
    plan = (getattr(user, "plan", "") or "").lower()
    if plan in ("paid", "pro", "premium"):
        return "paid"
    if getattr(user, "pro_active", False):
        return "paid"
    allow = {e.strip().lower() for e in (os.getenv("PAID_USER_EMAILS") or "").split(",") if e.strip()}
    return "paid" if (getattr(user, "email", "") or "").lower() in allow else "free"


# ── Limits ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Allowance:
    """What one class of caller may spend.

    Requests are the abuse ceiling; tokens are the cost ceiling. A caller can
    stay under the request count and still burn the budget with enormous
    prompts, which is why both are counted and why max_tokens is clamped
    rather than trusted.
    """

    requests_per_hour: int
    requests_per_day: int
    tokens_per_day: int
    max_output_tokens: int
    max_input_chars: int


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def allowance_for(kind: str) -> Allowance:
    """``kind`` is "guest", "free" or "paid"."""
    if kind == "paid":
        return Allowance(
            requests_per_hour=_env_int("AI_PAID_RPH", 120),
            requests_per_day=_env_int("AI_PAID_RPD", 600),
            tokens_per_day=_env_int("AI_PAID_TPD", 1_500_000),
            max_output_tokens=_env_int("AI_PAID_MAX_OUTPUT", 4000),
            max_input_chars=_env_int("AI_PAID_MAX_INPUT_CHARS", 24000),
        )
    if kind == "free":
        return Allowance(
            requests_per_hour=_env_int("AI_FREE_RPH", 15),
            requests_per_day=_env_int("AI_FREE_RPD", 40),
            tokens_per_day=_env_int("AI_FREE_TPD", 60000),
            max_output_tokens=_env_int("AI_FREE_MAX_OUTPUT", 2600),
            max_input_chars=_env_int("AI_FREE_MAX_INPUT_CHARS", 12000),
        )
    # Guests get enough to see that the product works and not enough to run a
    # free AI service on someone else's key.
    return Allowance(
        requests_per_hour=_env_int("AI_GUEST_RPH", 4),
        requests_per_day=_env_int("AI_GUEST_RPD", 8),
        tokens_per_day=_env_int("AI_GUEST_TPD", 12000),
        max_output_tokens=_env_int("AI_GUEST_MAX_OUTPUT", 1200),
        max_input_chars=_env_int("AI_GUEST_MAX_INPUT_CHARS", 6000),
    )


class AIBlocked(Exception):
    """The request may not proceed. ``reason`` is a stable machine code."""

    def __init__(self, reason: str, message: str, retry_after: int | None = None,
                 status: int = 429):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.retry_after = retry_after
        self.status = status


# ── Identity ──────────────────────────────────────────────────────

def _sign(value: str) -> str:
    # No hardcoded fallback: App.py refuses to boot without a real
    # SECRET_KEY, so current_app.secret_key is always set here. A fallback
    # to a value published in this repo would let anyone forge guest ids.
    key = current_app.secret_key
    if isinstance(key, str):
        key = key.encode()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()[:16]


def issue_guest_id() -> str:
    """A random id plus a signature, so a client cannot mint fresh identities
    that we would treat as distinct devices without also forging the HMAC."""
    raw = secrets.token_urlsafe(12)
    return f"{raw}.{_sign(raw)}"


def _valid_guest_id(value: str | None) -> bool:
    if not value or "." not in value:
        return False
    raw, sig = value.rsplit(".", 1)
    return hmac.compare_digest(sig, _sign(raw))


def identify(user) -> tuple[str, str]:
    """Return ``(subject, kind)``.

    The subject is what the counters are keyed on: the account when there is
    one, otherwise the signed device cookie. A guest with no valid cookie is
    keyed on their IP for this request and handed a cookie on the way out, so
    the very first call still counts against something.
    """
    if user is not None and getattr(user, "is_authenticated", False):
        return f"user:{user.get_id()}", plan_for(user)

    cookie = request.cookies.get(GUEST_COOKIE)
    if _valid_guest_id(cookie):
        return f"dev:{cookie.rsplit('.', 1)[0]}", "guest"

    fresh = issue_guest_id()
    g.ai_issue_guest_cookie = fresh
    ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
          or request.remote_addr or "unknown")
    return f"ip:{hashlib.sha256(ip.encode()).hexdigest()[:24]}", "guest"


def attach_guest_cookie(response):
    """Set the device cookie when identify() minted one this request."""
    fresh = getattr(g, "ai_issue_guest_cookie", None)
    if fresh:
        response.set_cookie(
            GUEST_COOKIE, fresh,
            max_age=GUEST_COOKIE_DAYS * 86400,
            httponly=True, samesite="Lax",
            secure=(current_app.config.get("SESSION_COOKIE_SECURE", False)),
        )
    return response


# ── Counters ──────────────────────────────────────────────────────

def _db():
    return current_app.extensions["sqlalchemy"]


def ensure_tables() -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    _META.create_all(bind=_db().engine, tables=[QUOTA_TABLE])
    _TABLE_READY = True


def _windows(now: datetime) -> tuple[str, str]:
    return now.strftime("%Y-%m-%dT%H"), now.strftime("%Y-%m-%d")


def _bump(subject: str, window_key: str, metric: str, amount: int) -> int:
    """Atomic increment that returns the new total.

    One statement, so two instances racing on the same student cannot both
    read 39 and both write 40. ON CONFLICT works the same on Postgres and on
    SQLite 3.24+, which is what the dev database is.
    """
    db = _db()
    sql = text(
        "INSERT INTO ai_quota_counters (subject, window_key, metric, count, updated_at) "
        "VALUES (:s, :w, :m, :n, :t) "
        "ON CONFLICT (subject, window_key, metric) "
        "DO UPDATE SET count = ai_quota_counters.count + :n, updated_at = :t "
        "RETURNING count"
    )
    row = db.session.execute(sql, {
        "s": subject, "w": window_key, "m": metric, "n": amount,
        "t": _now(),
    }).first()
    db.session.commit()
    return int(row[0]) if row else amount


def _peek(subject: str, window_key: str, metric: str) -> int:
    db = _db()
    row = db.session.execute(
        text("SELECT count FROM ai_quota_counters "
             "WHERE subject = :s AND window_key = :w AND metric = :m"),
        {"s": subject, "w": window_key, "m": metric},
    ).first()
    return int(row[0]) if row else 0


def purge_old_counters(days: int = 8) -> int:
    """Counters older than the longest window are dead weight."""
    ensure_tables()
    db = _db()
    cutoff = _now() - timedelta(days=days)
    res = db.session.execute(
        text("DELETE FROM ai_quota_counters WHERE updated_at < :c"), {"c": cutoff})
    db.session.commit()
    return res.rowcount or 0


# ── Prompt screening ──────────────────────────────────────────────

#: Asks that are not studying. IntelliPlan is a study product; generating an
#: application, a repository or a book-length document is somebody using a
#: free education account as a general-purpose code and content factory, and
#: each one costs more than a month of honest use.
_BULK_PATTERNS = [
    r"\b(write|build|create|generate|make)\s+(me\s+)?(a\s+)?(complete|full|entire|whole|production[- ]ready)\s+"
    r"(app|application|website|web ?app|game|project|program|system|platform|saas|clone)\b",
    r"\b(entire|complete|full)\s+(codebase|repository|repo|project|source code)\b",
    r"\bclone\s+(of\s+)?(twitter|instagram|facebook|tiktok|uber|airbnb|netflix|amazon)\b",
    r"\b(\d{3,})\s*(lines|pages)\s+of\s+(code|text)\b",
    r"\bwrite\s+(me\s+)?(a|the)\s+(whole|entire|full|complete)\s+(book|novel|thesis|dissertation)\b",
    r"\bfor each of the (following )?\d{2,}\b",
]
_BULK_RE = [re.compile(p, re.I) for p in _BULK_PATTERNS]

#: Attempts to talk the model out of its instructions. Blocked at the door so
#: the classifier downstream is not the only thing standing between a student
#: prompt and the system prompt.
_INJECTION_RE = [
    re.compile(r"\bignore (all |any |your )?(previous|prior|above|earlier) (instructions|prompts|rules)\b", re.I),
    re.compile(r"\b(reveal|print|show|repeat|output) (me )?(your |the )?(system prompt|initial instructions|instructions above)\b", re.I),
    re.compile(r"\byou are now (a|an|in) (?!student)", re.I),
    re.compile(r"\bdeveloper mode\b|\bDAN mode\b", re.I),
]


@dataclass
class Screen:
    ok: bool
    reason: str = ""
    message: str = ""
    matched: str = ""


def screen_prompt(texts: list[str], allowance: Allowance) -> Screen:
    """Cheap deterministic checks before a request costs anything.

    This is not the safety classifier -- chatbot_api still runs that on the
    content. This is the cost gate: size, bulk generation, and prompt
    injection, all of which are decidable without a model call.
    """
    joined = "\n".join(t for t in texts if t)
    if len(joined) > allowance.max_input_chars:
        return Screen(False, "input_too_large",
                      "That is longer than this plan can send in one go. "
                      "Split it into smaller pieces.")
    for rx in _BULK_RE:
        m = rx.search(joined)
        if m:
            return Screen(False, "bulk_generation",
                          "Plani is a study tutor, so it does not build whole "
                          "projects or write full-length documents. Ask about "
                          "the part you are stuck on and it will teach it.",
                          m.group(0)[:80])
    for rx in _INJECTION_RE:
        m = rx.search(joined)
        if m:
            return Screen(False, "prompt_injection",
                          "That request tries to change how Plani works rather "
                          "than ask it something.", m.group(0)[:80])
    return Screen(True)


# ── The gate ──────────────────────────────────────────────────────

def kill_switch_on() -> bool:
    """One environment variable turns every AI feature off.

    Worth having on the day a key leaks or a bill runs away: it does not need
    a deploy to be effective if the platform supports live variables.
    """
    return (os.getenv("AI_KILL_SWITCH", "") or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Decision:
    subject: str
    kind: str
    allowance: Allowance
    max_output_tokens: int
    remaining_day: int
    plan: str = "free"
    started: float = field(default_factory=time.monotonic)


def guard(user, *, prompts: list[str], want_output_tokens: int,
          feature: str = "ai") -> Decision:
    """Authorise one AI call, or raise AIBlocked.

    Counts the request immediately rather than after the provider answers: a
    caller who disconnects mid-stream has still spent the upstream call, and
    counting on success is how a retry loop gets free requests.
    """
    if kill_switch_on():
        raise AIBlocked("ai_disabled",
                        "AI features are switched off right now. Everything else still works.",
                        status=503)

    ensure_tables()
    subject, kind = identify(user)
    allowance = allowance_for(kind)

    screen = screen_prompt(prompts, allowance)
    if not screen.ok:
        logger.info("ai_firewall blocked %s on %s (%s) match=%r",
                    subject, feature, screen.reason, screen.matched)
        raise AIBlocked(screen.reason, screen.message, status=400)

    now = _now()
    hour_key, day_key = _windows(now)

    tokens_today = _peek(subject, day_key, "tokens")
    if tokens_today >= allowance.tokens_per_day:
        raise AIBlocked("token_budget_spent",
                        "You have used this account's AI budget for today. "
                        "It resets at midnight UTC.",
                        retry_after=_seconds_to_midnight(now))

    day_count = _bump(subject, day_key, "requests", 1)
    if day_count > allowance.requests_per_day:
        raise AIBlocked("daily_limit",
                        _limit_message(kind, "today"),
                        retry_after=_seconds_to_midnight(now))

    hour_count = _bump(subject, hour_key, "requests", 1)
    if hour_count > allowance.requests_per_hour:
        raise AIBlocked("hourly_limit",
                        _limit_message(kind, "in the last hour"),
                        retry_after=_seconds_to_next_hour(now))

    return Decision(
        subject=subject,
        kind=kind,
        allowance=allowance,
        # The client asks; the server decides. An oversized max_tokens is the
        # cheapest way to turn one request into ten requests' worth of spend.
        max_output_tokens=min(int(want_output_tokens or 0) or allowance.max_output_tokens,
                              allowance.max_output_tokens),
        remaining_day=max(0, allowance.requests_per_day - day_count),
        plan="paid" if kind == "paid" else "free",
    )


def record_tokens(decision: Decision, prompt_chars: int, reply_chars: int) -> None:
    """Charge the day's token budget once the provider has answered.

    Character count over four is the usual rough token estimate and is enough
    for a budget ceiling: no provider in the chain returns usage consistently,
    and an estimate that is applied to everyone is fairer than a number that
    exists for one provider.
    """
    try:
        est = max(1, (int(prompt_chars) + int(reply_chars)) // 4)
        _, day_key = _windows(_now())
        _bump(decision.subject, day_key, "tokens", est)
    except Exception:  # never fail a served answer over accounting
        logger.warning("ai_firewall could not record token usage", exc_info=True)


def _limit_message(kind: str, window: str) -> str:
    if kind == "guest":
        return (f"You have used the guest AI allowance {window}. "
                "Sign in for a student account and the limit goes up.")
    if kind == "free":
        return (f"You have used your free AI allowance {window}. "
                "It resets automatically.")
    return f"You have hit the fair-use ceiling {window}."


def _seconds_to_midnight(now: datetime) -> int:
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((nxt - now).total_seconds()))


def _seconds_to_next_hour(now: datetime) -> int:
    nxt = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return max(1, int((nxt - now).total_seconds()))


def usage_snapshot(user) -> dict:
    """What this caller has spent. Powers an honest counter in the UI."""
    ensure_tables()
    subject, kind = identify(user)
    allowance = allowance_for(kind)
    now = _now()
    hour_key, day_key = _windows(now)
    return {
        "kind": kind,
        "requests_today": _peek(subject, day_key, "requests"),
        "requests_this_hour": _peek(subject, hour_key, "requests"),
        "tokens_today": _peek(subject, day_key, "tokens"),
        "limits": {
            "requests_per_hour": allowance.requests_per_hour,
            "requests_per_day": allowance.requests_per_day,
            "tokens_per_day": allowance.tokens_per_day,
            "max_output_tokens": allowance.max_output_tokens,
        },
    }
