"""Tests for streak retention mechanics: risk assessment, perfect week, freeze offer."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import streak_engine
from streak_engine import (
    assess_streak_risk,
    perfect_week_bonus,
    streak_freeze_offer,
)


# ── assess_streak_risk ───────────────────────────────────────────────


def _at(hour: int, *, year=2026, month=6, day=14) -> datetime:
    """Pin a datetime so the tests don't depend on wall-clock time."""
    return datetime(year, month, day, hour, 0, 0, tzinfo=ZoneInfo("UTC"))


def test_risk_safe_when_done_today():
    today = date(2026, 6, 14)
    risk = assess_streak_risk(
        current_streak=5,
        last_qualifying_local_date=today,
        user_tz="UTC",
        now_local=_at(18, year=2026, month=6, day=14),
    )
    assert risk.level == "safe"
    assert risk.urgency_score == 0


def test_risk_due_today_morning():
    """Streak alive, lots of day left → urgency low/medium."""
    yesterday = date(2026, 6, 13)
    risk = assess_streak_risk(
        current_streak=5,
        last_qualifying_local_date=yesterday,
        user_tz="UTC",
        now_local=_at(10, year=2026, month=6, day=14),
    )
    assert risk.level == "due_today"
    assert 0 < risk.urgency_score < 90


def test_risk_danger_late_evening():
    """< 4 hours left and not done → danger."""
    yesterday = date(2026, 6, 13)
    risk = assess_streak_risk(
        current_streak=10,
        last_qualifying_local_date=yesterday,
        user_tz="UTC",
        now_local=_at(21, year=2026, month=6, day=14),
    )
    assert risk.level == "danger"
    assert risk.urgency_score >= 85
    assert "save" in risk.message.lower()


def test_risk_broken_after_two_day_gap():
    two_days_ago = date(2026, 6, 12)
    risk = assess_streak_risk(
        current_streak=10,
        last_qualifying_local_date=two_days_ago,
        user_tz="UTC",
        now_local=_at(10, year=2026, month=6, day=14),
    )
    assert risk.level == "broken"
    assert risk.urgency_score == 100


def test_risk_no_streak_yet():
    risk = assess_streak_risk(
        current_streak=0,
        last_qualifying_local_date=None,
        user_tz="UTC",
        now_local=_at(12, year=2026, month=6, day=14),
    )
    assert risk.level == "due_today"
    assert "start" in risk.message.lower()


# ── perfect_week_bonus ──────────────────────────────────────────────


def _dots(qualified_count: int, past_days: int = 7) -> list[dict]:
    """Build week_dots with `qualified_count` of `past_days` qualified."""
    dots = []
    for i in range(7):
        is_future = i >= past_days
        dots.append({
            "label": "M",
            "qualified": i < qualified_count and not is_future,
            "is_today": i == past_days - 1,
            "is_future": is_future,
        })
    return dots


def test_perfect_week_detected():
    dots = _dots(qualified_count=7, past_days=7)
    result = perfect_week_bonus(dots)
    assert result["perfect"] is True
    assert result["bonus_xp"] == 200


def test_not_perfect_with_one_miss():
    dots = _dots(qualified_count=6, past_days=7)
    result = perfect_week_bonus(dots)
    assert result["perfect"] is False
    assert result["bonus_xp"] == 0
    assert "perfect-week bonus" in result["message"].lower()


def test_partial_week_in_progress():
    """Wednesday — 3 past days, all qualified, 4 future days."""
    dots = _dots(qualified_count=3, past_days=3)
    result = perfect_week_bonus(dots)
    # Mid-week is never "perfect" — that's awarded only at week-end
    assert result["perfect"] is False
    assert result["progress"] == 3


# ── streak_freeze_offer ─────────────────────────────────────────────


def test_offer_shown_in_danger_with_freezes():
    yesterday = date(2026, 6, 13)
    offer = streak_freeze_offer(
        current_streak=14,
        last_qualifying_local_date=yesterday,
        freezes_available=2,
        user_tz="UTC",
        now_local=_at(21, year=2026, month=6, day=14),
    )
    assert offer is not None
    assert offer["show"] is True
    assert offer["freezes_after"] == 1


def test_offer_hidden_without_freezes():
    yesterday = date(2026, 6, 13)
    offer = streak_freeze_offer(
        current_streak=14,
        last_qualifying_local_date=yesterday,
        freezes_available=0,
        user_tz="UTC",
        now_local=_at(21, year=2026, month=6, day=14),
    )
    assert offer is None


def test_offer_hidden_when_safe():
    today = date(2026, 6, 14)
    offer = streak_freeze_offer(
        current_streak=14,
        last_qualifying_local_date=today,
        freezes_available=2,
        user_tz="UTC",
        now_local=_at(12, year=2026, month=6, day=14),
    )
    assert offer is None


def test_offer_hidden_when_plenty_of_time():
    """Morning, due today but 14+ hours left → no aggressive offer yet."""
    yesterday = date(2026, 6, 13)
    offer = streak_freeze_offer(
        current_streak=14,
        last_qualifying_local_date=yesterday,
        freezes_available=2,
        user_tz="UTC",
        now_local=_at(8, year=2026, month=6, day=14),
    )
    assert offer is None
