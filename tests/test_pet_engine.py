"""Tests for pet_engine — pure XP/stage/mood logic.

These tests are fully isolated from App.py — pet_engine is a pure module.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import pet_engine


# ── Stage resolution ─────────────────────────────────────────────────


def test_zero_xp_returns_egg_stage():
    stage = pet_engine.stage_for_xp(0)
    assert stage["id"] == "egg"


def test_50_xp_returns_hatchling():
    stage = pet_engine.stage_for_xp(50)
    assert stage["id"] == "hatchling"


def test_49_xp_still_egg():
    stage = pet_engine.stage_for_xp(49)
    assert stage["id"] == "egg"


def test_top_xp_returns_cosmic():
    stage = pet_engine.stage_for_xp(10_000)
    assert stage["id"] == "cosmic"


def test_stage_for_xp_boundaries():
    """Each stage's min_xp must resolve exactly to that stage."""
    for stage in pet_engine.STAGES:
        s = pet_engine.stage_for_xp(stage["min_xp"])
        assert s["id"] == stage["id"]


# ── Level calculation ────────────────────────────────────────────────


def test_level_starts_at_1():
    assert pet_engine.level_for_xp(0) == 1


def test_level_grows_with_xp():
    assert pet_engine.level_for_xp(30) == 2
    assert pet_engine.level_for_xp(60) == 3


def test_negative_xp_clamps_to_level_1():
    # Defensive: shouldn't happen in practice but must not crash
    assert pet_engine.level_for_xp(-5) == 1


# ── Mood ─────────────────────────────────────────────────────────────


def test_mood_happy_when_visited_today():
    today = date(2026, 6, 13)
    mood, days = pet_engine.compute_mood(today, today)
    assert mood == "happy"
    assert days == 0


def test_mood_neutral_after_one_day():
    today = date(2026, 6, 13)
    yesterday = today - timedelta(days=1)
    mood, days = pet_engine.compute_mood(yesterday, today)
    assert mood == "neutral"
    assert days == 1


def test_mood_sad_after_two_days():
    today = date(2026, 6, 13)
    mood, days = pet_engine.compute_mood(today - timedelta(days=3), today)
    assert mood == "sad"


def test_mood_sleepy_after_four_days():
    today = date(2026, 6, 13)
    mood, days = pet_engine.compute_mood(today - timedelta(days=4), today)
    assert mood == "sleepy"


def test_mood_sleepy_when_never_visited():
    today = date(2026, 6, 13)
    mood, days = pet_engine.compute_mood(None, today)
    assert mood == "sleepy"
    assert days == 999


# ── Next stage ───────────────────────────────────────────────────────


def test_next_stage_after_egg_is_hatchling():
    nxt = pet_engine.next_stage("egg")
    assert nxt is not None
    assert nxt["id"] == "hatchling"


def test_next_stage_for_cosmic_is_none():
    assert pet_engine.next_stage("cosmic") is None


def test_next_stage_for_unknown_returns_none():
    assert pet_engine.next_stage("nonexistent-stage") is None


# ── Full state resolution ────────────────────────────────────────────


def test_resolve_pet_state_egg_zero_xp():
    state = pet_engine.resolve_pet_state(xp=0, last_visit_local=None, user_tz="UTC")
    assert state.stage_id == "egg"
    assert state.level == 1
    assert state.progress_to_next == 0.0
    assert state.xp_for_next_stage == 50


def test_resolve_pet_state_hatchling_progress():
    state = pet_engine.resolve_pet_state(xp=100, last_visit_local=None, user_tz="UTC")
    assert state.stage_id == "hatchling"
    # 100 XP - 50 (hatchling min) = 50 into stage
    # Next stage (sprout) starts at 150 → 100 XP gap
    assert state.xp_into_stage == 50
    assert state.xp_for_next_stage == 100
    assert state.progress_to_next == pytest.approx(0.5)


def test_resolve_pet_state_cosmic_is_maxed():
    state = pet_engine.resolve_pet_state(xp=10_000, last_visit_local=None, user_tz="UTC")
    assert state.stage_id == "cosmic"
    assert state.next_stage_id is None
    assert state.progress_to_next == 1.0


# ── Daily visit gating ───────────────────────────────────────────────


def test_should_grant_daily_visit_when_never_visited():
    assert pet_engine.should_grant_daily_visit(None, "UTC") is True


def test_should_not_grant_daily_visit_today_twice():
    today = pet_engine.datetime.now(pet_engine.ZoneInfo("UTC")).date()
    assert pet_engine.should_grant_daily_visit(today, "UTC") is False


def test_should_grant_after_yesterday():
    yesterday = pet_engine.datetime.now(pet_engine.ZoneInfo("UTC")).date() - timedelta(days=1)
    assert pet_engine.should_grant_daily_visit(yesterday, "UTC") is True


# ── Streak milestones ────────────────────────────────────────────────


def test_streak_milestone_at_3():
    assert pet_engine.streak_milestone_event(3) == "streak_milestone_3"


def test_streak_milestone_at_7():
    assert pet_engine.streak_milestone_event(7) == "streak_milestone_7"


def test_no_milestone_at_unsupported_day():
    assert pet_engine.streak_milestone_event(4) is None
    assert pet_engine.streak_milestone_event(0) is None


def test_milestone_event_keys_exist_in_rewards():
    """Every milestone event must have a configured XP reward."""
    for day in (3, 7, 14, 30, 60, 100):
        key = pet_engine.streak_milestone_event(day)
        assert key in pet_engine.XP_REWARDS
        assert pet_engine.XP_REWARDS[key] > 0


# ── XP reward sanity ─────────────────────────────────────────────────


def test_all_xp_rewards_are_positive():
    for key, value in pet_engine.XP_REWARDS.items():
        assert value > 0, f"{key} has non-positive reward: {value}"


def test_xp_rewards_monotonic_for_streak_milestones():
    """Higher milestones must give more XP than lower ones."""
    milestones = [3, 7, 14, 30, 60, 100]
    rewards = [pet_engine.XP_REWARDS[f"streak_milestone_{d}"] for d in milestones]
    assert rewards == sorted(rewards), "Streak milestone XP must be non-decreasing"
