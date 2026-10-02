"""Slot new work into the plan a student already has, and say where it went.

Shared by quick-add and "Break it down". Both create work *after* a plan
exists, and both owe the student the same answer: not "added", but
"Scheduled Wed 4:00–5:00 PM". The placement itself is pure
(:func:`scheduler_engine.insert_blocks`); this module supplies the owner's
saved plan, free windows and wall clock, and saves the result.

Works for three kinds of caller: a signed-in browser (``current_user``), a
guest browser (guest session id), and the extension, which authenticates by
token and so names its owner explicitly. Every App import is lazy, matching
the other ``*_glue`` modules.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Callable, Sequence

import scheduler_engine
from time_utils import local_now, valid_timezone

logger = logging.getLogger(__name__)

#: Nothing is placed sooner than this from now. Nobody starts a block that
#: begins in ninety seconds; the planner uses the same margin for today.
LEAD_MINUTES = 15


def student_timezone(user_id: int | None, hint: str | None = None) -> str:
    """The student's IANA zone: stored (streak settings) first, then the
    browser's hint, then UTC."""
    if user_id:
        try:
            from App import UserStreak

            row = UserStreak.query.filter_by(user_id=user_id).first()
            stored = valid_timezone(row.timezone if row else None)
            if stored:
                return stored
        except Exception as exc:
            logger.warning("timezone lookup failed: %s", exc)
    return valid_timezone(hint) or "UTC"


def student_now(user_id: int | None, hint: str | None = None) -> datetime:
    return local_now(student_timezone(user_id, hint))


def active_plan_row(user_id: int | None, guest_id: str | None) -> Any | None:
    from App import SavedSchedule

    query = SavedSchedule.query.filter(SavedSchedule.is_active.is_(True))
    if user_id:
        query = query.filter(SavedSchedule.user_id == user_id)
    elif guest_id:
        query = query.filter(SavedSchedule.user_id.is_(None),
                             SavedSchedule.guest_session_id == guest_id)
    else:
        return None
    return query.order_by(SavedSchedule.created_at.desc()).first()


def _busy_by_date(user_id: int | None) -> dict:
    """Dated calendar events, only when the request *is* this student — the
    calendar helpers read ``current_user``, and the extension path has none."""
    try:
        from flask_login import current_user

        from App import _planner_busy_by_date

        if user_id and current_user.is_authenticated and int(current_user.id) == int(user_id):
            return _planner_busy_by_date() or {}
    except Exception as exc:
        logger.warning("calendar busy lookup failed: %s", exc)
    return {}


def insert_into_plan(
    user_id: int | None,
    guest_id: str | None,
    blocks: Sequence[dict[str, Any]],
    *,
    now: datetime,
    deadline: date | None = None,
    prune: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Place ``blocks`` into the owner's active plan and save it.

    ``prune`` gets the plan first, to drop blocks the new ones replace (a
    re-broken-down assignment's old sittings). Returns
    ``{"status": "ok"|"no_plan"|"no_room"|"error", "placed": [...],
    "label": "Scheduled Wed 4:00–5:00 PM"}``.
    """
    from App import (build_scheduler_personalization, db,
                     invalidate_schedule_cache)

    row = active_plan_row(user_id, guest_id)
    if row is None:
        return {"status": "no_plan", "placed": [], "label": ""}
    try:
        data = json.loads(row.schedule_data) if row.schedule_data else {}
        if not isinstance(data, dict):
            data = {}
    except (TypeError, ValueError):
        return {"status": "error", "placed": [], "label": ""}

    first_id = scheduler_engine._next_block_id(data.get("schedule") or [])
    try:
        progress = json.loads(row.progress_json) if row.progress_json else {}
        for key in progress if isinstance(progress, dict) else ():
            if str(key).startswith("b") and str(key)[1:].isdigit():
                first_id = max(first_id, int(str(key)[1:]) + 1)
    except (TypeError, ValueError):
        pass
    if prune is not None:
        prune(data)

    try:
        _dna, availability, commitments = build_scheduler_personalization(
            user_id=user_id, guest_id=None if user_id else guest_id,
        )
    except Exception as exc:
        logger.warning("personalization load failed: %s", exc)
        availability, commitments = {}, ""
    preferred = str(data.get("preferred_time") or "evening")
    busy = _busy_by_date(user_id)

    def windows_for(day: date):
        return scheduler_engine.windows_for_date(
            day, availability, preferred, commitments, now=now, busy_by_date=busy,
        )

    placed = scheduler_engine.insert_blocks(
        data, [dict(b) for b in blocks],
        windows_for=windows_for,
        start=now.date(),
        deadline=deadline,
        not_before=now + timedelta(minutes=LEAD_MINUTES),
        first_id=first_id,
    )
    landed = [b for b in placed if not b.get("unplaced")]
    if landed or prune is not None:
        try:
            row.schedule_data = json.dumps(data)
            db.session.commit()
            invalidate_schedule_cache(user_id=user_id, guest_id=guest_id)
        except Exception as exc:
            db.session.rollback()
            logger.warning("plan save after insert failed: %s", exc)
            return {"status": "error", "placed": [], "label": ""}
    return {
        "status": "ok" if landed else "no_room",
        "placed": [_public(b) for b in placed],
        "label": placement_label(placed),
    }


def _public(block: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": block.get("id"),
        "title": block.get("assignment"),
        "minutes": block.get("duration_minutes"),
        "start": block.get("start_iso") or "",
        "end": block.get("end_iso") or "",
        "label": block.get("placed_label") or "",
        "unplaced": bool(block.get("unplaced")),
        "step_ids": list(block.get("step_ids") or []),
    }


def placement_label(placed: Sequence[dict[str, Any]]) -> str:
    """One line for the toast: where it went, or that it did not fit."""
    landed = [b.get("placed_label") for b in placed if b.get("placed_label")]
    missing = sum(1 for b in placed if b.get("unplaced"))
    if not landed:
        return "No free time before it's due — add availability or move something." if missing else ""
    if len(landed) == 1:
        text = f"Scheduled {landed[0]}"
    elif len(landed) == 2:
        text = f"Scheduled {landed[0]} and {landed[1]}"
    else:
        text = f"Scheduled {len(landed)} blocks, starting {landed[0]}"
    if missing:
        text += f" ({missing} didn't fit)"
    return text
