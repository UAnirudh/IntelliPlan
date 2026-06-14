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
