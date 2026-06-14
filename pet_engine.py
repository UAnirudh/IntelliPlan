"""Plani Pet — virtual creature that grows with site usage.

A Duolingo-style mascot that levels up the more the user attends.
Stages evolve based on cumulative XP. Mood reflects recent activity.

Pure functions only — DB writes happen in App.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from zoneinfo import ZoneInfo


# ── Evolution stages ─────────────────────────────────────────────────
# Each stage gates on cumulative XP. Stage drives the SVG rendering.

STAGES = [
    {"id": "egg",       "name": "Egg",        "min_xp": 0,     "color": "#fbcfe8", "title": "A mysterious egg"},
    {"id": "hatchling", "name": "Hatchling",  "min_xp": 50,    "color": "#fde68a", "title": "Just hatched!"},
    {"id": "sprout",    "name": "Sprout",     "min_xp": 150,   "color": "#a7f3d0", "title": "A curious sprout"},
    {"id": "cub",       "name": "Cub",        "min_xp": 350,   "color": "#bae6fd", "title": "Playful cub"},
    {"id": "scholar",   "name": "Scholar",    "min_xp": 700,   "color": "#c4b5fd", "title": "Bookish scholar"},
    {"id": "sage",      "name": "Sage",       "min_xp": 1300,  "color": "#fcd34d", "title": "Wise sage"},
    {"id": "guardian",  "name": "Guardian",   "min_xp": 2200,  "color": "#f0abfc", "title": "Knowledge guardian"},
    {"id": "mythic",    "name": "Mythic",     "min_xp": 3500,  "color": "#34d399", "title": "Mythic companion"},
    {"id": "cosmic",    "name": "Cosmic",     "min_xp": 5500,  "color": "#60a5fa", "title": "Cosmic legend"},
]

# XP awards keyed by event type. Caller is responsible for triggering once
# per event (e.g. one daily-visit per local day).
XP_REWARDS = {
    "daily_visit":         15,
    "task_completed":      20,
    "schedule_generated":  35,
    "streak_milestone_3":  50,
    "streak_milestone_7":  100,
    "streak_milestone_14": 150,
    "streak_milestone_30": 300,
    "streak_milestone_60": 500,
    "streak_milestone_100": 1000,
    "study_session":       25,
    "tutor_chat":          5,
    "grades_imported":     40,
}


@dataclass(frozen=True)
class PetState:
    xp: int
    level: int
    stage_id: str
    stage_name: str
    stage_title: str
    stage_color: str
    progress_to_next: float    # 0.0 - 1.0 toward next stage
    xp_into_stage: int
    xp_for_next_stage: int      # 0 if maxed
    next_stage_id: Optional[str]
    next_stage_name: Optional[str]
    mood: str                    # "happy" | "neutral" | "sad" | "sleepy"
    days_since_visit: int


# ── Pure logic ───────────────────────────────────────────────────────

def stage_for_xp(xp: int) -> dict:
    """Return the stage dict the pet currently belongs to."""
    current = STAGES[0]
    for stage in STAGES:
        if xp >= stage["min_xp"]:
            current = stage
        else:
            break
    return current


def next_stage(current_stage_id: str) -> Optional[dict]:
    """Return the next stage after the current one, or None if maxed."""
    found = False
    for stage in STAGES:
        if found:
            return stage
        if stage["id"] == current_stage_id:
            found = True
    return None


def level_for_xp(xp: int) -> int:
    """Pet level grows by ~30 XP per level. Display-only number."""
    return max(1, 1 + xp // 30)


def compute_mood(last_visit_local: Optional[date], today_local: date) -> tuple[str, int]:
    """Pet mood reflects recent activity."""
    if last_visit_local is None:
        return ("sleepy", 999)
    days = (today_local - last_visit_local).days
    if days <= 0:
        return ("happy", 0)
    if days == 1:
        return ("neutral", 1)
    if days <= 3:
        return ("sad", days)
    return ("sleepy", days)


def resolve_pet_state(*, xp: int, last_visit_local: Optional[date], user_tz: str) -> PetState:
    """Build a full PetState from persisted columns."""
    tz = ZoneInfo(user_tz) if user_tz else ZoneInfo("UTC")
    today = datetime.now(tz).date()

    stage = stage_for_xp(xp)
    nxt = next_stage(stage["id"])
    xp_into = max(0, xp - stage["min_xp"])
    xp_needed = (nxt["min_xp"] - stage["min_xp"]) if nxt else 0
    progress = min(1.0, xp_into / xp_needed) if xp_needed else 1.0

    mood, days_since = compute_mood(last_visit_local, today)

    return PetState(
        xp=xp,
        level=level_for_xp(xp),
        stage_id=stage["id"],
        stage_name=stage["name"],
        stage_title=stage["title"],
        stage_color=stage["color"],
        progress_to_next=progress,
        xp_into_stage=xp_into,
        xp_for_next_stage=xp_needed,
        next_stage_id=nxt["id"] if nxt else None,
        next_stage_name=nxt["name"] if nxt else None,
        mood=mood,
        days_since_visit=days_since,
    )


def should_grant_daily_visit(last_visit_local: Optional[date], user_tz: str) -> bool:
    """True if the user hasn't claimed today's visit XP yet."""
    tz = ZoneInfo(user_tz) if user_tz else ZoneInfo("UTC")
    today = datetime.now(tz).date()
    return last_visit_local != today


def streak_milestone_event(streak_days: int) -> Optional[str]:
    """Return the XP event key if this streak count hits a milestone."""
    milestones = {3, 7, 14, 30, 60, 100}
    if streak_days in milestones:
        return f"streak_milestone_{streak_days}"
    return None


# ── Care actions (Tamagotchi mechanic) ───────────────────────────────
# Each action has a cooldown so users can't grind. Cooldowns are tracked
# by the App.py persistence layer; the engine just defines the rules.

CARE_ACTIONS = {
    "feed": {
        "xp": 8,
        "cooldown_hours": 6,
        "mood_boost": "happy",
        "label": "Feed",
        "emoji": "🍎",
        "copy": "Yum! +8 XP",
    },
    "play": {
        "xp": 12,
        "cooldown_hours": 8,
        "mood_boost": "happy",
        "label": "Play",
        "emoji": "🎾",
        "copy": "So fun! +12 XP",
    },
    "pet": {
        "xp": 4,
        "cooldown_hours": 2,
        "mood_boost": "happy",
        "label": "Pet",
        "emoji": "💕",
        "copy": "Purr! +4 XP",
    },
    "study_with": {
        "xp": 20,
        "cooldown_hours": 12,
        "mood_boost": "happy",
        "label": "Study together",
        "emoji": "📚",
        "copy": "Plani learned! +20 XP",
    },
}


def can_perform_care_action(action: str, last_performed_at: Optional[datetime]) -> tuple[bool, int]:
    """Return (allowed, seconds_until_next_use)."""
    if action not in CARE_ACTIONS:
        return (False, 0)
    cooldown_h = CARE_ACTIONS[action]["cooldown_hours"]
    if last_performed_at is None:
        return (True, 0)
    elapsed = (datetime.now() - last_performed_at).total_seconds()
    cooldown_s = cooldown_h * 3600
    if elapsed >= cooldown_s:
        return (True, 0)
    return (False, int(cooldown_s - elapsed))


# ── Daily check-in chest ─────────────────────────────────────────────
# Reward escalates the longer the user comes back consecutively. The
# App.py layer tracks `chest_streak_days` separately so we don't conflate
# pet visits with task-completion streaks.

def daily_chest_reward(chest_streak_days: int) -> dict:
    """Return the reward for opening today's chest given chest-streak length."""
    chest_streak_days = max(1, chest_streak_days)
    # Day 1: +10 XP. Day 7: +50 XP. Cap at 100.
    xp = min(100, 10 + (chest_streak_days - 1) * 6)
    tier = "common"
    if chest_streak_days >= 7:
        tier = "epic"
    elif chest_streak_days >= 3:
        tier = "rare"
    return {
        "xp": xp,
        "tier": tier,
        "day": chest_streak_days,
        "next_day_xp": min(100, 10 + chest_streak_days * 6),
    }


# ── Accessories unlocked at stage milestones ─────────────────────────

ACCESSORIES = [
    {"id": "scholar_glasses", "name": "Scholar glasses", "unlock_stage": "scholar", "emoji": "👓"},
    {"id": "sage_hat", "name": "Sage's hat", "unlock_stage": "sage", "emoji": "🎩"},
    {"id": "guardian_cape", "name": "Guardian cape", "unlock_stage": "guardian", "emoji": "🦸"},
    {"id": "mythic_horn", "name": "Mythic horn", "unlock_stage": "mythic", "emoji": "🦄"},
    {"id": "cosmic_halo", "name": "Cosmic halo", "unlock_stage": "cosmic", "emoji": "✨"},
]


def accessories_for(stage_id: str) -> list[dict]:
    """Return all accessories unlocked at or before the given stage."""
    stage_order = [s["id"] for s in STAGES]
    if stage_id not in stage_order:
        return []
    idx = stage_order.index(stage_id)
    unlocked = []
    for acc in ACCESSORIES:
        unlock_idx = stage_order.index(acc["unlock_stage"]) if acc["unlock_stage"] in stage_order else -1
        if unlock_idx >= 0 and idx >= unlock_idx:
            unlocked.append(acc)
    return unlocked


def evolution_payload(before_stage: str, after_stage: str) -> Optional[dict]:
    """If an evolution happened, return the celebration payload for the UI."""
    if before_stage == after_stage:
        return None
    new_stage = next((s for s in STAGES if s["id"] == after_stage), None)
    if not new_stage:
        return None
    return {
        "evolved": True,
        "from": before_stage,
        "to": after_stage,
        "stage_name": new_stage["name"],
        "stage_title": new_stage["title"],
        "stage_color": new_stage["color"],
        "headline": f"Your Plani evolved into a {new_stage['name']}!",
        "copy": new_stage["title"],
    }
