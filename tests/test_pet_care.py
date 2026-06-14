"""Tests for the Plani pet care mechanics: cooldowns, chest, accessories, evolution."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import pet_engine
from pet_engine import (
    ACCESSORIES,
    CARE_ACTIONS,
    accessories_for,
    can_perform_care_action,
    daily_chest_reward,
    evolution_payload,
)


# ── Care actions configuration ──────────────────────────────────────


def test_care_actions_exist():
    for key in ("feed", "play", "pet", "study_with"):
        assert key in CARE_ACTIONS
        cfg = CARE_ACTIONS[key]
        assert cfg["xp"] > 0
        assert cfg["cooldown_hours"] > 0
        assert cfg["label"]
        assert cfg["emoji"]


def test_study_with_gives_most_xp():
    """Highest commitment action should give the most XP."""
    assert CARE_ACTIONS["study_with"]["xp"] >= CARE_ACTIONS["play"]["xp"]
    assert CARE_ACTIONS["play"]["xp"] >= CARE_ACTIONS["feed"]["xp"]
    assert CARE_ACTIONS["feed"]["xp"] >= CARE_ACTIONS["pet"]["xp"]


# ── Cooldown gating ─────────────────────────────────────────────────


def test_can_perform_when_never_done():
    allowed, wait = can_perform_care_action("feed", None)
    assert allowed is True
    assert wait == 0


def test_can_perform_after_cooldown_elapsed():
    long_ago = datetime.now() - timedelta(hours=24)
    allowed, wait = can_perform_care_action("feed", long_ago)
    assert allowed is True
    assert wait == 0


def test_blocked_during_cooldown():
    recent = datetime.now() - timedelta(minutes=10)
    allowed, wait = can_perform_care_action("feed", recent)
    assert allowed is False
    assert wait > 0
    assert wait <= CARE_ACTIONS["feed"]["cooldown_hours"] * 3600


def test_unknown_action_rejected():
    allowed, wait = can_perform_care_action("ride_bike", None)
    assert allowed is False


# ── Daily chest ─────────────────────────────────────────────────────


def test_chest_day_1_common():
    reward = daily_chest_reward(1)
    assert reward["xp"] == 10
    assert reward["tier"] == "common"
    assert reward["day"] == 1


def test_chest_escalates():
    """XP must monotonically increase as the chest streak grows."""
    prev = 0
    for day in range(1, 10):
        xp = daily_chest_reward(day)["xp"]
        assert xp >= prev
        prev = xp


def test_chest_caps_at_100():
    """Engagement reward shouldn't spiral — capped to prevent gaming."""
    reward = daily_chest_reward(50)
    assert reward["xp"] <= 100


def test_chest_tier_progression():
    assert daily_chest_reward(1)["tier"] == "common"
    assert daily_chest_reward(3)["tier"] == "rare"
    assert daily_chest_reward(7)["tier"] == "epic"


def test_chest_streak_zero_still_returns_day_1():
    """Defensive: a 0/negative streak should still pay the day-1 reward."""
    reward = daily_chest_reward(0)
    assert reward["day"] == 1
    assert reward["xp"] == 10


# ── Accessories ─────────────────────────────────────────────────────


def test_no_accessories_for_egg():
    assert accessories_for("egg") == []


def test_scholar_unlocks_glasses():
    accs = accessories_for("scholar")
    ids = {a["id"] for a in accs}
    assert "scholar_glasses" in ids


def test_cosmic_unlocks_all_accessories():
    accs = accessories_for("cosmic")
    assert len(accs) == len(ACCESSORIES)


def test_unknown_stage_returns_empty():
    assert accessories_for("dragon_lord_supreme") == []


# ── Evolution payload ───────────────────────────────────────────────


def test_no_evolution_when_same_stage():
    assert evolution_payload("egg", "egg") is None


def test_evolution_payload_when_stage_changes():
    payload = evolution_payload("egg", "hatchling")
    assert payload is not None
    assert payload["evolved"] is True
    assert payload["from"] == "egg"
    assert payload["to"] == "hatchling"
    assert "Hatchling" in payload["headline"]


def test_evolution_unknown_target_returns_none():
    assert evolution_payload("egg", "made_up_stage") is None


def test_evolution_payload_carries_color():
    payload = evolution_payload("hatchling", "sprout")
    assert payload is not None
    assert payload["stage_color"].startswith("#")
