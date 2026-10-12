"""Glue between App.py and the Command Center blueprint.

This module is imported by App.py at the end of model setup. All App
imports inside it are LAZY (inside functions) — the same pattern
chatbot_api.py uses — so there is no circular import: by the time any
of these callables run, App is fully loaded.
"""

from __future__ import annotations

from datetime import date, timedelta
from time_utils import utcnow

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
        if not _grade_sources_allowed(user_id):
            return ()
        rows = ImportedGrade.query.filter_by(user_id=user_id).all()
        grades = tuple(
            CourseGrade(course=r.course, current_pct=float(r.percentage))
            for r in rows
            if r.percentage is not None
        )
        return grades if _grade_sources_allowed(user_id) else ()
    except Exception:
        return ()


def _grade_sources_allowed(user_id: int) -> bool:
    from App import User
    from intelliplan.learning import policy

    policy.invalidate_cache()
    user = User.query.populate_existing().filter_by(id=user_id).first()
    return user is not None and all(policy.decision(user, source)[0] for source in policy.LMS_SOURCES)


def _planner_allowed(user_id: int) -> bool:
    from App import User
    from intelliplan.learning import policy

    try:
        policy.invalidate_cache()
        user = User.query.populate_existing().filter_by(id=user_id).first()
        return user is not None and policy.decision(user, 'scheduler')[0]
    except Exception:
        return False


def _filter_stored_tasks(user_id: int, rows: list) -> list:
    from App import User
    from intelliplan.learning import policy

    policy.invalidate_cache()
    user = User.query.populate_existing().filter_by(id=user_id).first()
    if user is None:
        return []
    allowed = {source for source in policy.SOURCE_CAPABILITIES if policy.decision(user, source)[0]}
    result = []
    for row in rows:
        source = (getattr(row, 'import_source', '') or '').strip().lower()
        if source in {'', 'quickadd', 'course'}:
            result.append(row)
            continue
        named = policy.source_capability(source.removeprefix('scraper:'))
        if (named in allowed if named else allowed == policy.SOURCE_CAPABILITIES):
            result.append(row)
    return result


def _today_policy_stamp(user_id: int) -> str:
    import json
    from App import User
    from intelliplan.learning import policy

    policy.invalidate_cache()
    user = User.query.populate_existing().filter_by(id=user_id).first()
    if user is None or not policy.decision(user, 'scheduler')[0]:
        raise PermissionError('Planner permission unavailable.')
    settings = policy.effective(user)
    return json.dumps({'policy': settings, 'personalization': bool(user.ai_personalization_opt_in)},
                      sort_keys=True, default=lambda value: sorted(value))


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
        rows = _filter_stored_tasks(user_id, rows)
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


def _briefing_permission(user_id: int) -> frozenset:
    from App import User
    from intelliplan.learning import policy

    policy.invalidate_cache()
    user = User.query.populate_existing().filter_by(id=user_id).first()
    if (user is None or not user.ai_personalization_opt_in
            or not policy.decision(user, 'scheduler')[0]):
        return frozenset()
    # Imported grade aggregates do not preserve enough source provenance to
    # selectively remove restricted facts from narration or its old cache.
    if any(not policy.decision(user, source)[0] for source in policy.SOURCE_CAPABILITIES):
        return frozenset()
    return frozenset(policy.permitted_providers(user))


def _briefing_ai(user_id: int, messages, **kwargs):
    from App import User
    from ai_provider import chat_json
    from intelliplan.learning.courses import provider_user

    user = User.query.populate_existing().filter_by(id=user_id).first()
    if user is None or not _briefing_permission(user_id):
        raise RuntimeError('Briefing processing is restricted.')
    token = provider_user.set(user)
    try:
        return chat_json(messages, **kwargs)
    finally:
        provider_user.reset(token)


def _build_service() -> TodayService:
    from App import BriefingCache, HealthSnapshot, ManualTask, db

    briefing = BriefingService(BriefingCache, db.session,
                              authorize_ai=_briefing_permission, owner_ai=_briefing_ai)
    repo = AssignmentRepository(ManualTask, db.session, lms_fetcher=_lms_fetcher,
                                manual_filter=_filter_stored_tasks)
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


def _has_guest_session() -> bool:
    from flask import session

    try:
        return bool(session.get("login_type"))
    except Exception:
        return False


def _stale_briefing_user_ids() -> list[int]:
    from App import BriefingCache

    try:
        now = utcnow()
        rows = BriefingCache.query.filter(BriefingCache.expires_at < now).all()
        return [r.user_id for r in rows]
    except Exception:
        return []


def _needs_onboarding(user_id: int) -> bool:
    """True for a student account that has not finished /onboarding.

    Fails open: /onboarding sends people back here when it cannot load the
    identity row, so answering True on an error would bounce them between
    the two pages forever.
    """
    from App import User, _get_or_create_identity, db

    try:
        user = User.query.get(user_id)
        if user is None or (getattr(user, "role", "") or "student") != "student":
            return False
        identity = _get_or_create_identity(user_id)
        return identity is not None and not bool(identity.completed)
    except Exception as e:
        print(f"[command_center] onboarding check failed: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return False


def _page_context(user_id: int) -> dict:
    """What the first-run panel needs: is there anything to plan from, and
    can IntelliPlan reach this student when they are not looking at it."""
    from App import User
    from intelliplan.email.onboarding import _exists, _has_connected_account

    context = {"has_work_source": True, "reminders_on": True}
    try:
        context["has_work_source"] = (
            _has_connected_account(user_id) or _exists("ManualTask", user_id)
        )
        user = User.query.get(user_id)
        context["reminders_on"] = bool(
            user is not None
            and (
                getattr(user, "email_reminders_opt_in", False)
                or getattr(user, "push_reminders_opt_in", False)
                or getattr(user, "sms_reminders_opt_in", False)
            )
        )
    except Exception as e:
        # Either flag wrong only hides a prompt; the page must still render.
        print(f"[command_center] page context failed: {e}")
    return context


command_center_bp = create_command_center_blueprint(
    CommandCenterDeps(
        get_service=_build_service,
        feature_enabled=_feature_enabled,
        current_user_id=_current_user_id,
        emit_signal=_emit_signal,
        stale_briefing_user_ids=_stale_briefing_user_ids,
        can_refresh=_planner_allowed,
        policy_stamp=_today_policy_stamp,
        has_guest_session=_has_guest_session,
        needs_onboarding=_needs_onboarding,
        page_context=_page_context,
    )
)
