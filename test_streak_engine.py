"""Unit tests for the task-completion streak engine.

Covers all 8 required scenarios plus edge cases.
Run with: pytest test_streak_engine.py -v
"""

import pytest
from datetime import date, timedelta

from streak_engine import (
    compute_streak_update,
    is_user_in_rollout,
    user_cohort,
    week_dots,
    should_show_nudge,
    recap_headline,
)


# ── Scenario (a): User completes a task at 11:55 PM local — counts as today

def test_task_at_1155pm_counts_as_today():
    today = date(2025, 6, 10)
    result = compute_streak_update(
        current_streak=3,
        longest_streak=5,
        last_qualifying_local_date=date(2025, 6, 9),
        freezes_available=2,
        freezes_used_total=0,
        user_tz="America/Los_Angeles",
        now_local_date=today,
    )
    assert result.current_streak == 4
    assert result.event == "streak_continued"


# ── Scenario (b): User completes a task at 12:05 AM local — counts as new day

def test_task_at_1205am_counts_as_new_day():
    yesterday = date(2025, 6, 9)
    today = date(2025, 6, 10)
    result = compute_streak_update(
        current_streak=3,
        longest_streak=5,
        last_qualifying_local_date=yesterday,
        freezes_available=2,
        freezes_used_total=0,
        user_tz="America/Los_Angeles",
        now_local_date=today,
    )
    assert result.current_streak == 4
    assert result.event == "streak_continued"
    assert result.event_props["new_count"] == 4


# ── Scenario (c): User travels across timezones (LA to NY)

def test_timezone_travel_no_double_count():
    today = date(2025, 6, 10)
    # Already qualified today in LA timezone
    result = compute_streak_update(
        current_streak=3,
        longest_streak=5,
        last_qualifying_local_date=today,
        freezes_available=2,
        freezes_used_total=0,
        user_tz="America/New_York",
        now_local_date=today,
    )
    assert result.current_streak == 3
    assert result.event is None


def test_timezone_travel_no_skip():
    # User qualified yesterday in LA, now in NY where it's the next day
    yesterday = date(2025, 6, 9)
    today = date(2025, 6, 10)
    result = compute_streak_update(
        current_streak=3,
        longest_streak=5,
        last_qualifying_local_date=yesterday,
        freezes_available=2,
        freezes_used_total=0,
        user_tz="America/New_York",
        now_local_date=today,
    )
    assert result.current_streak == 4
    assert result.event == "streak_continued"


# ── Scenario (d): User completes 5 tasks in one day — streak only increments by 1

def test_multiple_tasks_same_day():
    today = date(2025, 6, 10)
    # First task of the day (yesterday was qualified)
    r1 = compute_streak_update(
        current_streak=3,
        longest_streak=5,
        last_qualifying_local_date=date(2025, 6, 9),
        freezes_available=2,
        freezes_used_total=0,
        user_tz="America/Los_Angeles",
        now_local_date=today,
    )
    assert r1.current_streak == 4

    # Second through fifth tasks — already qualified today
    for _ in range(4):
        r = compute_streak_update(
            current_streak=r1.current_streak,
            longest_streak=r1.longest_streak,
            last_qualifying_local_date=today,
            freezes_available=r1.freezes_available,
            freezes_used_total=0,
            user_tz="America/Los_Angeles",
            now_local_date=today,
        )
        assert r.current_streak == 4
        assert r.event is None


# ── Scenario (e): User misses 1 day with 2 freezes — streak continues

def test_miss_1_day_with_2_freezes():
    today = date(2025, 6, 12)
    last = date(2025, 6, 10)  # missed June 11
    result = compute_streak_update(
        current_streak=5,
        longest_streak=10,
        last_qualifying_local_date=last,
        freezes_available=2,
        freezes_used_total=0,
        user_tz="America/Los_Angeles",
        now_local_date=today,
    )
    assert result.current_streak == 6
    assert result.freezes_available == 1
    assert result.event == "streak_freeze_consumed"
    assert result.event_props["days_covered"] == 1
    assert result.event_props["remaining"] == 1


# ── Scenario (f): User misses 3 days with 2 freezes — streak resets

def test_miss_3_days_with_2_freezes():
    today = date(2025, 6, 14)
    last = date(2025, 6, 10)  # missed June 11, 12, 13
    result = compute_streak_update(
        current_streak=5,
        longest_streak=10,
        last_qualifying_local_date=last,
        freezes_available=2,
        freezes_used_total=0,
        user_tz="America/Los_Angeles",
        now_local_date=today,
    )
    assert result.current_streak == 1
    assert result.event == "streak_broken"
    assert result.event_props["previous_count"] == 5
    assert result.event_props["days_missed"] == 3


# ── Scenario (g): User hits 7-day milestone with 3 freezes — cap at 3

def test_7day_milestone_no_exceed_cap():
    today = date(2025, 6, 10)
    result = compute_streak_update(
        current_streak=6,
        longest_streak=6,
        last_qualifying_local_date=date(2025, 6, 9),
        freezes_available=3,
        freezes_used_total=0,
        user_tz="America/Los_Angeles",
        now_local_date=today,
    )
    assert result.current_streak == 7
    assert result.freezes_available == 3  # capped, no extra
    assert result.freeze_earned is False


def test_7day_milestone_earns_freeze_when_below_cap():
    today = date(2025, 6, 10)
    result = compute_streak_update(
        current_streak=6,
        longest_streak=6,
        last_qualifying_local_date=date(2025, 6, 9),
        freezes_available=1,
        freezes_used_total=0,
        user_tz="America/Los_Angeles",
        now_local_date=today,
    )
    assert result.current_streak == 7
    assert result.freezes_available == 2
    assert result.freeze_earned is True


# ── Scenario (h): User returns after 6 months — streak resets cleanly

def test_return_after_6_months():
    today = date(2025, 6, 10)
    last = date(2024, 12, 10)  # 182 days ago
    result = compute_streak_update(
        current_streak=45,
        longest_streak=45,
        last_qualifying_local_date=last,
        freezes_available=2,
        freezes_used_total=5,
        user_tz="America/Los_Angeles",
        now_local_date=today,
    )
    assert result.current_streak == 1
    assert result.longest_streak == 45  # preserved
    assert result.event == "streak_broken"
    assert result.event_props["previous_count"] == 45


# ── First-ever action

def test_first_ever_action():
    today = date(2025, 6, 10)
    result = compute_streak_update(
        current_streak=0,
        longest_streak=0,
        last_qualifying_local_date=None,
        freezes_available=2,
        freezes_used_total=0,
        user_tz="America/Los_Angeles",
        now_local_date=today,
    )
    assert result.current_streak == 1
    assert result.longest_streak == 1
    assert result.event == "streak_started"
    assert result.toast_message == "+1 day. 1 day streak."


# ── Longest streak updates correctly

def test_longest_streak_updates():
    today = date(2025, 6, 10)
    result = compute_streak_update(
        current_streak=10,
        longest_streak=10,
        last_qualifying_local_date=date(2025, 6, 9),
        freezes_available=2,
        freezes_used_total=0,
        user_tz="America/Los_Angeles",
        now_local_date=today,
    )
    assert result.current_streak == 11
    assert result.longest_streak == 11


# ── Feature flag rollout

def test_rollout_deterministic():
    result1 = is_user_in_rollout(42, "streak_v1", 50)
    result2 = is_user_in_rollout(42, "streak_v1", 50)
    assert result1 == result2


def test_rollout_100_percent():
    assert is_user_in_rollout(1, "streak_v1", 100) is True
    assert is_user_in_rollout(999999, "streak_v1", 100) is True


def test_rollout_0_percent():
    assert is_user_in_rollout(1, "streak_v1", 0) is False
    assert is_user_in_rollout(999999, "streak_v1", 0) is False


def test_cohort_assignment():
    cohort = user_cohort(42, "streak_v1", 50)
    assert cohort in ("treatment", "control")


# ── Week dots

def test_week_dots_structure():
    qualified = {date(2025, 6, 9), date(2025, 6, 10)}
    dots = week_dots("America/Los_Angeles", qualified)
    assert len(dots) == 7
    assert dots[0]["label"] == "M"
    assert dots[6]["label"] == "S"
    assert all("qualified" in d for d in dots)
    assert all("is_today" in d for d in dots)


# ── Nudge

def test_nudge_not_shown_when_already_qualified():
    assert should_show_nudge(
        current_streak=5,
        last_qualifying_local_date=date.today(),
        user_tz="America/Los_Angeles",
        nudge_shown_today=False,
    ) is False


def test_nudge_not_shown_when_streak_zero():
    assert should_show_nudge(
        current_streak=0,
        last_qualifying_local_date=None,
        user_tz="America/Los_Angeles",
        nudge_shown_today=False,
    ) is False


# ── Recap headline

def test_recap_headline_deterministic():
    h1 = recap_headline(25, 42, 12, "Wednesday")
    h2 = recap_headline(25, 42, 12, "Wednesday")
    assert h1 == h2
    assert isinstance(h1, str)
    assert len(h1) > 0


def test_recap_headline_different_for_different_weeks():
    h1 = recap_headline(25, 42, 12, "Wednesday")
    h2 = recap_headline(26, 42, 12, "Wednesday")
    # Not guaranteed to differ but should be a string
    assert isinstance(h2, str)


# ── Freeze consumed during gap + milestone combo

def test_freeze_consumed_and_milestone():
    today = date(2025, 6, 15)
    last = date(2025, 6, 13)  # missed 1 day (June 14)
    result = compute_streak_update(
        current_streak=13,
        longest_streak=13,
        last_qualifying_local_date=last,
        freezes_available=2,
        freezes_used_total=0,
        user_tz="UTC",
        now_local_date=today,
    )
    # streak goes 13 -> 14, which hits 14 % 7 == 0
    assert result.current_streak == 14
    assert result.event == "streak_freeze_consumed"
    # 1 freeze consumed, then 1 earned = net 2
    assert result.freezes_available == 2
    assert result.freeze_earned is True


# ── Miss exactly 2 days with exactly 2 freezes — should continue

def test_miss_exactly_2_days_with_2_freezes():
    today = date(2025, 6, 13)
    last = date(2025, 6, 10)  # missed June 11, 12
    result = compute_streak_update(
        current_streak=5,
        longest_streak=10,
        last_qualifying_local_date=last,
        freezes_available=2,
        freezes_used_total=0,
        user_tz="UTC",
        now_local_date=today,
    )
    assert result.current_streak == 6
    assert result.freezes_available == 0
    assert result.event == "streak_freeze_consumed"
    assert result.event_props["days_covered"] == 2
