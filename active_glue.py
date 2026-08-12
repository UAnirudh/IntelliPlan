"""Glue between App.py and the Active study blueprint.

Imported by App.py after model setup. Every App import is LAZY (inside a
function), matching ``command_center_glue.py``, so there is no circular
import: by the time any of these callables run, App is fully loaded.

This module is also where the feedback loop is closed. When a session
finishes, three things happen:

1. The session row is already written (the blueprint did that).
2. A ``TaskFeedback`` row is mirrored, so the legacy surfaces that read it
   — Study DNA, the stats page — stay correct without being rewritten.
3. The cached plan for that student is invalidated, so the next schedule
   read re-fits the estimation model against the sitting that just ended.
"""

from __future__ import annotations

import logging
from typing import Any

from flask_login import current_user

from intelliplan.api.active import ActiveDeps, create_active_blueprint
from intelliplan.repositories.active_sessions import ActiveSessionRepository

logger = logging.getLogger(__name__)


# ── identity ──────────────────────────────────────────────────────────


def _current_user_id() -> int | None:
    try:
        if current_user.is_authenticated:
            return int(current_user.id)
    except Exception:
        pass
    return None


def _guest_session_id() -> str | None:
    from App import get_guest_session_id

    try:
        return get_guest_session_id()
    except Exception:
        return None


def _feature_enabled(key: str) -> bool:
    from App import feature_enabled

    try:
        return feature_enabled(key)
    except Exception:
        # A missing flag table must not take the study page down; the flag
        # is a kill switch, and its default is on.
        return True


# ── repository ────────────────────────────────────────────────────────


def _get_repository() -> ActiveSessionRepository:
    from App import ActiveFocusSample, ActiveSession, db

    return ActiveSessionRepository(ActiveSession, ActiveFocusSample, db.session)


# ── plan access ───────────────────────────────────────────────────────


def _get_plan() -> dict[str, Any] | None:
    """The student's current saved schedule, as ``schedule_data``."""
    from App import SavedSchedule, db
    import json

    uid = _current_user_id()
    gid = None if uid else _guest_session_id()
    try:
        query = SavedSchedule.query.filter(SavedSchedule.is_active.is_(True))
        if uid:
            query = query.filter(SavedSchedule.user_id == uid)
        elif gid:
            query = query.filter(
                SavedSchedule.user_id.is_(None),
                SavedSchedule.guest_session_id == gid,
            )
        else:
            return None
        row = query.order_by(SavedSchedule.created_at.desc()).first()
        if row is None:
            return None
        data = json.loads(row.schedule_data) if isinstance(row.schedule_data, str) else row.schedule_data
        if not isinstance(data, dict):
            return None
        _mark_done_blocks(data, row.progress_json)
        return data
    except Exception as exc:
        logger.warning("plan lookup failed: %s", exc)
        try:
            db.session.rollback()
        except Exception:
            pass
        return None


def _mark_done_blocks(data: dict[str, Any], progress_json: Any) -> None:
    """Flag blocks the student has already checked off.

    Without this, ``/api/active/next`` cheerfully offers the block they
    finished an hour ago as the next thing to do.
    """
    import json

    progress = progress_json
    if isinstance(progress, str):
        try:
            progress = json.loads(progress)
        except (TypeError, ValueError):
            progress = None
    if not isinstance(progress, dict):
        return
    for day in data.get("schedule", []) or []:
        for block in day.get("blocks", []) or []:
            entry = progress.get(str(block.get("id")))
            if entry is True or (isinstance(entry, dict) and entry.get("done")):
                block["done"] = True


# ── feedback loop ─────────────────────────────────────────────────────


def _on_session_finished(row: Any) -> None:
    """Fan a finished sitting out to everything that should react to it."""
    _mirror_task_feedback(row)
    _invalidate_plan_cache()


def _mirror_task_feedback(row: Any) -> None:
    """Write the legacy ``TaskFeedback`` row for this sitting.

    The new estimation model reads ``active_sessions`` directly, but the
    older surfaces (Study DNA, the stats page, the prompt builder) read
    ``task_feedback``. Mirroring keeps both correct during the transition
    instead of forcing a big-bang rewrite of every consumer.

    Only completed sittings are mirrored. ``task_feedback`` has no notion
    of an abandoned attempt, so writing one there would tell the old
    readers that a 12-minute abandonment was a 12-minute task.
    """
    from App import TaskFeedback, db

    if not getattr(row, "completed_work", False):
        return
    minutes = row.active_minutes
    if minutes <= 0 or not (row.planned_minutes or 0):
        return
    try:
        started = row.started_at
        db.session.add(
            TaskFeedback(
                user_id=row.user_id,
                guest_session_id=row.guest_session_id,
                title=(row.title or "Study session")[:512],
                course=(row.course or "")[:256],
                estimated_time=int(row.planned_minutes or 0),
                actual_time=minutes,
                difficulty=(row.difficulty or "medium").title()[:16],
                completed_at=row.ended_at or started,
                day_of_week=started.strftime("%a") if started else "",
                time_of_day=_slot_for(started),
            )
        )
        db.session.commit()
    except Exception as exc:
        logger.warning("task feedback mirror failed (session=%s): %s", row.id, exc)
        try:
            db.session.rollback()
        except Exception:
            pass


def _slot_for(moment: Any) -> str:
    if moment is None:
        return ""
    hour = moment.hour
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


def _invalidate_plan_cache() -> None:
    """Drop any cached plan so the next read re-fits the model.

    The whole promise of the feedback loop is that finishing a session
    changes the next plan. A cache that outlives the session breaks that
    promise in the most visible way possible.
    """
    try:
        from App import invalidate_schedule_cache

        invalidate_schedule_cache(_current_user_id(), _guest_session_id())
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("plan cache invalidation failed: %s", exc)


def _emit_signal(**kwargs: Any) -> None:
    from App import StudentSignal, db
    from intelliplan.repositories.signals import SignalRepository

    uid = _current_user_id()
    if uid is None:
        return
    try:
        SignalRepository(StudentSignal, db.session).emit(
            uid,
            kwargs.pop("kind", "active_session"),
            subject_type="active_session",
            subject_ref=str(kwargs.pop("subject_ref", "")),
            value=kwargs.pop("value", None),
        )
    except Exception:
        pass


active_bp = create_active_blueprint(
    ActiveDeps(
        get_repository=_get_repository,
        current_user_id=_current_user_id,
        guest_session_id=_guest_session_id,
        feature_enabled=_feature_enabled,
        get_plan=_get_plan,
        on_session_finished=_on_session_finished,
        emit_signal=_emit_signal,
    )
)
