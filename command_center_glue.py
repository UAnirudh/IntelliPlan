"""Glue between App.py and the Command Center blueprint.

This module is imported by App.py at the end of model setup. All App
imports inside it are LAZY (inside functions) — the same pattern
chatbot_api.py uses — so there is no circular import: by the time any
of these callables run, App is fully loaded.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from flask_login import current_user

from intelliplan.api.command_center import (
    CommandCenterDeps,
    availability_to_minutes,
    create_command_center_blueprint,
)
from intelliplan.intelligence.health import CourseGrade
from intelliplan.repositories.assignments import AssignmentRepository
from intelliplan.repositories.signals import SignalRepository
from intelliplan.services.briefing import BriefingService
from intelliplan.services.today import StudentInfo, TodayService


# ── dependency callables (lazy App imports) ──────────────────────────


def _current_user_id() -> int | None:
    try:
        if current_user.is_authenticated:
            return int(current_user.id)
    except Exception:
        pass
    return None


def _feature_enabled(key: str) -> bool:
    from App import feature_enabled

    return feature_enabled(key)


def _get_student(user_id: int) -> StudentInfo:
    from App import User, _get_or_create_identity

    name, grade_level, avail, personal = "", "", None, False
    try:
        user = User.query.get(user_id)
        if user is not None:
            name = user.name or (user.email or "").split("@")[0]
            personal = bool(getattr(user, "ai_personalization_opt_in", False))
    except Exception:
        pass
    try:
        identity = _get_or_create_identity(user_id)
        if identity is not None:
            grade_level = identity.grade_level or ""
            avail = availability_to_minutes(identity.avail_dict())
    except Exception:
        pass
    return StudentInfo(
        user_id=user_id,
        name=name,
        grade_level=grade_level,
        availability=avail,
        personalization_enabled=personal,
    )


def _get_grades(user_id: int) -> tuple[CourseGrade, ...]:
    from App import ImportedGrade

    try:
        rows = ImportedGrade.query.filter_by(user_id=user_id).all()
        return tuple(
            CourseGrade(course=r.course, current_pct=float(r.percentage))
            for r in rows
            if r.percentage is not None
        )
    except Exception:
        return ()


def _get_completion_rate_7d(user_id: int) -> float:
    """Done-ratio of manual tasks due in the trailing 7 days."""
    from App import ManualTask

    try:
        cutoff = (date.today() - timedelta(days=7)).isoformat()
        today_s = date.today().isoformat()
        rows = (
            ManualTask.query
            .filter(ManualTask.user_id == user_id)
            .filter(ManualTask.due_date >= cutoff)
            .filter(ManualTask.due_date <= today_s)
            .all()
        )
        if not rows:
            return 0.0
        done = sum(1 for r in rows if r.done)
        return done / len(rows)
    except Exception:
        return 0.0


def _lms_fetcher(user_id: int) -> list[dict]:
    """Pull live LMS assignments — Canvas, StudentVue, Schoology, Classroom,
    Blackboard, Moodle — for the given user. Safe to call outside a request
    context (the underlying helper handles that)."""
    from App import collect_lms_assignments_for_user

    try:
        return collect_lms_assignments_for_user(user_id)
    except Exception as e:
        print(f"[command_center] lms fetch failed: {e}")
        return []


def _build_service() -> TodayService:
    from App import BriefingCache, HealthSnapshot, ManualTask, db

    briefing = BriefingService(BriefingCache, db.session)
    repo = AssignmentRepository(ManualTask, db.session, lms_fetcher=_lms_fetcher)
    return TodayService(
        assignments_repo=repo,
        briefing_service=briefing,
        get_student=_get_student,
        get_grades=_get_grades,
        get_completion_rate_7d=_get_completion_rate_7d,
        health_snapshot_model=HealthSnapshot,
        session=db.session,
    )


def _emit_signal(user_id: int, kind: str, **kwargs) -> None:
    from App import StudentSignal, db

    SignalRepository(StudentSignal, db.session).emit(user_id, kind, **kwargs)


def _stale_briefing_user_ids() -> list[int]:
    from App import BriefingCache

    try:
        now = datetime.utcnow()
        rows = BriefingCache.query.filter(BriefingCache.expires_at < now).all()
        return [r.user_id for r in rows]
    except Exception:
        return []


command_center_bp = create_command_center_blueprint(
    CommandCenterDeps(
        get_service=_build_service,
        feature_enabled=_feature_enabled,
        current_user_id=_current_user_id,
        emit_signal=_emit_signal,
        stale_briefing_user_ids=_stale_briefing_user_ids,
    )
)
