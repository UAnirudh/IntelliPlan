"""Task-completion streak engine.

Resolves streak state using the user's local timezone, never UTC.
All mutations return a ``StreakResult`` describing what changed so the
caller can fire the correct analytics events and render UI feedback.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from zoneinfo import ZoneInfo


# ── Result value objects ─────────────────────────────────────────────

@dataclass(frozen=True)
class StreakResult:
    current_streak: int
    longest_streak: int
    freezes_available: int
    event: Optional[str] = None          # analytics event name
    event_props: dict = field(default_factory=dict)
    freeze_earned: bool = False
    toast_message: Optional[str] = None


# ── Pure streak logic ────────────────────────────────────────────────

def resolve_local_date(tz_name: str) -> date:
    """Return today's date in the given IANA timezone."""
    tz = ZoneInfo(tz_name)
    return datetime.now(tz).date()


def compute_streak_update(
    *,
    current_streak: int,
    longest_streak: int,
    last_qualifying_local_date: Optional[date],
    freezes_available: int,
    freezes_used_total: int,
    user_tz: str,
    now_local_date: Optional[date] = None,
) -> StreakResult:
    """Compute the new streak state after a qualifying action.

    Parameters mirror the ``user_streaks`` row. ``now_local_date`` can be
    injected for testing; otherwise it is resolved from ``user_tz``.
    """
    today = now_local_date or resolve_local_date(user_tz)

    # ── Same day: no streak change ───────────────────────────────
    if last_qualifying_local_date == today:
        return StreakResult(
            current_streak=current_streak,
            longest_streak=longest_streak,
            freezes_available=freezes_available,
            event=None,
        )

    # ── Consecutive day ──────────────────────────────────────────
    if last_qualifying_local_date == today - timedelta(days=1):
        new_streak = current_streak + 1
        new_longest = max(longest_streak, new_streak)
        freeze_earned = False
        new_freezes = freezes_available
        event_props: dict = {"new_count": new_streak}
        if new_streak % 7 == 0 and freezes_available < 3:
            new_freezes = freezes_available + 1
            freeze_earned = True
        return StreakResult(
            current_streak=new_streak,
            longest_streak=new_longest,
            freezes_available=new_freezes,
            event="streak_continued",
            event_props=event_props,
            freeze_earned=freeze_earned,
            toast_message=f"+1 day. {new_streak} day streak.",
        )

    # ── Gap of 2+ days (or first-ever action) ────────────────────
    if last_qualifying_local_date is None:
        return StreakResult(
            current_streak=1,
            longest_streak=max(longest_streak, 1),
            freezes_available=freezes_available,
            event="streak_started",
            event_props={"new_count": 1},
            toast_message="+1 day. 1 day streak.",
        )

    days_missed = (today - last_qualifying_local_date).days - 1
    if days_missed <= 0:
        days_missed = 0

    if freezes_available >= days_missed and days_missed > 0:
        new_freezes = freezes_available - days_missed
        new_streak = current_streak + 1
        new_longest = max(longest_streak, new_streak)
        freeze_earned = False
        if new_streak % 7 == 0 and new_freezes < 3:
            new_freezes += 1
            freeze_earned = True
        return StreakResult(
            current_streak=new_streak,
            longest_streak=new_longest,
            freezes_available=new_freezes,
            event="streak_freeze_consumed",
            event_props={
                "days_covered": days_missed,
                "remaining": new_freezes,
                "new_count": new_streak,
            },
            freeze_earned=freeze_earned,
            toast_message=f"+1 day. {new_streak} day streak.",
        )

    # Not enough freezes — streak resets
    previous = current_streak
    return StreakResult(
        current_streak=1,
        longest_streak=max(longest_streak, 1),
        freezes_available=freezes_available,
        event="streak_broken",
        event_props={"previous_count": previous, "days_missed": days_missed},
        toast_message="+1 day. 1 day streak.",
    )


# ── Feature flag: deterministic percentage rollout ───────────────────

def is_user_in_rollout(user_id: int, flag_key: str, percentage: int) -> bool:
    """Deterministic hash-based percentage rollout.

    Returns True if the user falls within *percentage* (0-100).
    The hash is stable: same user_id + flag_key always yields the same
    bucket, so assignment doesn't shift as other users sign up.
    """
    if percentage >= 100:
        return True
    if percentage <= 0:
        return False
    raw = f"{flag_key}:{user_id}"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    bucket = int(digest[:8], 16) % 100
    return bucket < percentage


def user_cohort(user_id: int, flag_key: str, percentage: int) -> str:
    """Return 'treatment' or 'control' for analytics tagging."""
    return "treatment" if is_user_in_rollout(user_id, flag_key, percentage) else "control"


# ── Week dots helper ─────────────────────────────────────────────────

def week_dots(user_tz: str, qualified_dates: set[date]) -> list[dict]:
    """Return Mon-Sun dot data for the current local week."""
    today = resolve_local_date(user_tz)
    monday = today - timedelta(days=today.weekday())
    dots = []
    day_labels = ["M", "T", "W", "T", "F", "S", "S"]
    for i in range(7):
        d = monday + timedelta(days=i)
        dots.append({
            "label": day_labels[i],
            "date": d.isoformat(),
            "qualified": d in qualified_dates,
            "is_today": d == today,
            "is_future": d > today,
        })
    return dots


# ── Nudge eligibility ────────────────────────────────────────────────

def should_show_nudge(
    *,
    current_streak: int,
    last_qualifying_local_date: Optional[date],
    user_tz: str,
    nudge_shown_today: bool,
) -> bool:
    """True when the evening nudge should appear."""
    if current_streak <= 0:
        return False
    today = resolve_local_date(user_tz)
    if last_qualifying_local_date == today:
        return False
    now = datetime.now(ZoneInfo(user_tz))
    if now.hour < 18:
        return False
    return not nudge_shown_today


# ── Weekly recap headline templates ──────────────────────────────────

_RECAP_HEADLINES = [
    "You crushed {busiest_day}.",
    "Steady week.",
    "{tasks_done} tasks? Nice.",
    "Momentum building.",
    "Quiet week, recharge mode.",
    "Strong finish.",
    "One week closer to your goals.",
    "Your rhythm is locking in.",
]


def recap_headline(week_number: int, user_id: int, tasks_done: int, busiest_day: str) -> str:
    """Deterministic rule-based headline for the weekly recap."""
    raw = f"{week_number}:{user_id}"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    idx = int(digest[:8], 16) % len(_RECAP_HEADLINES)
    template = _RECAP_HEADLINES[idx]
    return template.format(busiest_day=busiest_day, tasks_done=tasks_done)


# ── Retention helpers ────────────────────────────────────────────────


@dataclass(frozen=True)
class StreakRisk:
    """Risk assessment for the current streak — drives the at-risk banner."""

    level: str          # "safe" | "due_today" | "danger" | "broken"
    hours_until_break: int  # full local hours remaining today to act
    message: str        # short, actionable
    urgency_score: int  # 0-100 for picking colors / push priority


def assess_streak_risk(
    *,
    current_streak: int,
    last_qualifying_local_date: date | None,
    user_tz: str,
    now_local: datetime | None = None,
) -> StreakRisk:
    """Return a StreakRisk describing the user's current standing.

    "safe"        — already done today
    "due_today"   — hasn't acted yet, plenty of time
    "danger"      — < 4 hours of day remaining and still un-qualified
    "broken"      — last action was 2+ days ago (engine resets next call)
    """
    tz = ZoneInfo(user_tz) if user_tz else ZoneInfo("UTC")
    now = now_local or datetime.now(tz)
    today = now.date()

    # No streak yet
    if current_streak <= 0:
        return StreakRisk(
            level="due_today",
            hours_until_break=max(0, 24 - now.hour),
            message="Start your first streak today!",
            urgency_score=40,
        )

    if last_qualifying_local_date == today:
        return StreakRisk(
            level="safe",
            hours_until_break=24,
            message=f"{current_streak}-day streak locked for today.",
            urgency_score=0,
        )

    yesterday = today - timedelta(days=1)
    if last_qualifying_local_date and last_qualifying_local_date < yesterday:
        return StreakRisk(
            level="broken",
            hours_until_break=0,
            message=f"Your {current_streak}-day streak is broken.",
            urgency_score=100,
        )

    hours_left = max(0, 24 - now.hour)
    if hours_left <= 4:
        return StreakRisk(
            level="danger",
            hours_until_break=hours_left,
            message=(
                f"⚠ {current_streak}-day streak ends in {hours_left}h. "
                "Complete one task to save it."
            ),
            urgency_score=90,
        )
    return StreakRisk(
        level="due_today",
        hours_until_break=hours_left,
        message=f"Keep your {current_streak}-day streak alive — finish one task today.",
        urgency_score=60,
    )


def perfect_week_bonus(week_dots: list[dict]) -> dict:
    """Detect a Mon-Sun perfect week (every weekday qualified through today).

    Returns dict with ``perfect`` (bool), ``progress`` (qualified-day count),
    ``message`` (short copy for the UI), and ``bonus_xp`` (awarded amount when
    perfect). The caller is responsible for paying out and idempotency.
    """
    qualified_days = sum(1 for d in week_dots if d.get("qualified"))
    past_or_today = sum(1 for d in week_dots if not d.get("is_future", False))
    perfect = qualified_days == past_or_today and past_or_today >= 7

    if perfect:
        return {
            "perfect": True,
            "progress": qualified_days,
            "total_days": 7,
            "message": "Perfect week — +200 XP bonus!",
            "bonus_xp": 200,
        }
    return {
        "perfect": False,
        "progress": qualified_days,
        "total_days": past_or_today,
        "message": f"{qualified_days}/{past_or_today} days this week. {7 - qualified_days} to lock the perfect-week bonus.",
        "bonus_xp": 0,
    }


def streak_freeze_offer(
    *,
    current_streak: int,
    last_qualifying_local_date: date | None,
    freezes_available: int,
    user_tz: str,
    now_local: datetime | None = None,
) -> dict | None:
    """When the streak is in real danger, return an offer dict so the UI can
    pop a 'Save your streak' modal. ``None`` means no offer needed."""
    risk = assess_streak_risk(
        current_streak=current_streak,
        last_qualifying_local_date=last_qualifying_local_date,
        user_tz=user_tz,
        now_local=now_local,
    )
    if risk.level == "danger" and freezes_available > 0:
        return {
            "show": True,
            "message": f"Use 1 freeze to lock your {current_streak}-day streak overnight?",
            "freezes_after": freezes_available - 1,
            "expires_in_hours": risk.hours_until_break,
        }
    return None
