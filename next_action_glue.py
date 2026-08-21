"""Glue between App.py and the adaptive-scheduler blueprint.

Imported by App.py at the bottom, next to the other blueprint registrations.
Every App import is LAZY (inside a function) — the same pattern
``command_center_glue`` uses — so there is no circular import: by the time
any of these callables run, App is fully loaded.

Nothing here computes anything. It reads the student's real data out of the
ORM and hands it to the pure engines. If a provider raises, the service
catches it and degrades that one input rather than failing the request.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from flask_login import current_user

from intelliplan.api.next_action import NextActionDeps, create_next_action_blueprint
from intelliplan.domain.student import MasteryEstimate
from intelliplan.repositories.schedule_audit import ScheduleAuditRepository
from intelliplan.services.next_action import NextActionService

logger = logging.getLogger(__name__)

#: How far back the behavioural model reads sittings. Its own 45-day decay
#: does the real weighting; this only bounds the query.
HISTORY_DAYS = 120

#: Rows loaded per source. The estimation model already caps at 300 and these
#: match, so the two models see the same slice of a student's history and
#: cannot disagree about what happened.
HISTORY_ROWS = 300


# ── identity ─────────────────────────────────────────────────────────


def _current_user_id() -> int | None:
    try:
        if current_user.is_authenticated:
            return int(current_user.id)
    except Exception:
        pass
    return None


def _feature_enabled_for_user(key: str, user_id: int) -> bool:
    from App import feature_enabled_for_user

    return feature_enabled_for_user(key, user_id)


def _now() -> datetime:
    """Local wall-clock, deliberately not UTC.

    "What should I do right now" is a question about the student's evening.
    Answering it in UTC puts a student in California into tomorrow's plan at
    5 PM, and the placement engine already works in local time — the two have
    to agree or "you have 45 minutes left" is measured against a different
    day than the blocks it is talking about.
    """
    return datetime.now()


# ── plan ─────────────────────────────────────────────────────────────


def _active_schedule(user_id: int) -> dict | None:
    import json

    from App import SavedSchedule

    try:
        row = (
            SavedSchedule.query
            .filter_by(user_id=user_id, is_active=True)
            .order_by(SavedSchedule.created_at.desc())
            .first()
        )
        if row is None or not row.schedule_data:
            return None
        return json.loads(row.schedule_data)
    except Exception as exc:
        logger.warning("active schedule load failed for %s: %s", user_id, exc)
        return None


def _save_schedule(user_id: int, data: dict) -> bool:
    """Write the plan back in place, keeping its identity.

    Updates the existing active row rather than inserting a new one: the
    plan's name, creation date, and synced progress all hang off that row,
    and an override is an edit to a plan, not a new plan.
    """
    import json

    from App import SavedSchedule, db, invalidate_schedule_cache

    try:
        row = (
            SavedSchedule.query
            .filter_by(user_id=user_id, is_active=True)
            .order_by(SavedSchedule.created_at.desc())
            .first()
        )
        if row is None:
            return False
        row.schedule_data = json.dumps(data)
        db.session.commit()
        try:
            invalidate_schedule_cache(user_id=user_id, guest_id=None)
        except Exception:
            pass
        return True
    except Exception as exc:
        logger.warning("schedule save failed for %s: %s", user_id, exc)
        try:
            db.session.rollback()
        except Exception:
            pass
        return False


# ── assignments ──────────────────────────────────────────────────────


def _assignments(user_id: int) -> list[dict]:
    """Everything outstanding, from the sources already unified upstream.

    Reuses ``collect_lms_assignments_for_user`` and ``ManualTask`` rather than
    adding a ninth ingest path — the whole point of the repository layer is
    that "what work exists" has one answer.
    """
    from App import ManualTask, collect_lms_assignments_for_user

    rows: list[dict] = []
    try:
        for t in ManualTask.query.filter_by(user_id=user_id, done=False).limit(300):
            rows.append({
                "id": f"manual:{t.id}",
                "title": t.title,
                "course": t.course or "",
                "due_date": t.due_date or "",
                "priority": {"High": 80, "Medium": 50, "Low": 30}.get(t.priority, 50),
                "est_minutes": int(t.estimated_time or 60),
                "status": "not_started",
                "kind": "homework",
            })
    except Exception as exc:
        logger.warning("manual task load failed for %s: %s", user_id, exc)

    try:
        for a in collect_lms_assignments_for_user(user_id) or []:
            if not isinstance(a, dict):
                continue
            rows.append({
                "id": str(a.get("id") or a.get("source_ref") or a.get("title") or ""),
                "title": a.get("title") or "",
                "course": a.get("course") or "",
                "due_date": a.get("due_date") or "",
                "priority": a.get("priority") or 50,
                "est_minutes": int(a.get("est_minutes") or 45),
                "status": a.get("status") or "not_started",
                "kind": a.get("kind") or "",
            })
    except Exception as exc:
        logger.warning("lms assignment load failed for %s: %s", user_id, exc)

    return rows


# ── mastery ──────────────────────────────────────────────────────────


def _mastery(user_id: int, today: date) -> list[MasteryEstimate]:
    """Concept mastery, decayed to today and joined to upcoming assessments.

    The stored ``mastery_score`` is what was true at ``last_reviewed``.
    Presenting it unchanged weeks later would claim a student still knows
    something we last saw evidence of in September, so it is decayed here
    using the row's own ``decay_rate`` before anyone reads it.
    """
    from App import ConceptMastery

    try:
        rows = ConceptMastery.query.filter_by(user_id=user_id).limit(400).all()
    except Exception as exc:
        logger.warning("mastery load failed for %s: %s", user_id, exc)
        return []

    assessments = _assessment_days_by_course(user_id, today)
    out: list[MasteryEstimate] = []
    for r in rows:
        raw = float(getattr(r, "mastery_score", 0.0) or 0.0)
        last = getattr(r, "last_reviewed", None)
        days_since = None
        decayed = raw
        if isinstance(last, datetime):
            days_since = max(0, (today - last.date()).days)
            rate = float(getattr(r, "decay_rate", 0.05) or 0.05)
            # Exponential forgetting, floored: a concept that was once learned
            # does not decay to nothing, and modelling it as if it does makes
            # the scheduler re-teach material the student half-remembers.
            decayed = max(raw * 0.35, raw * (1.0 - rate) ** (days_since / 7.0))

        subject = getattr(r, "subject", "") or ""
        days_away = assessments.get(subject.strip().lower())
        seen = int(getattr(r, "times_seen", 0) or 0)
        # Confidence in the *estimate*, not in the student. It rises with
        # observations and falls as the last observation ages.
        confidence = min(0.95, seen / 8.0)
        if days_since is not None:
            confidence *= 0.5 ** (days_since / 60.0)

        out.append(
            MasteryEstimate(
                subject=subject,
                topic=getattr(r, "topic", "") or "",
                concept=getattr(r, "concept", "") or "",
                mastery=round(max(0.0, min(1.0, decayed)), 3),
                confidence=round(max(0.0, min(1.0, confidence)), 3),
                days_since_review=days_since,
                assessment_days_away=days_away,
                risk=round(max(0.0, min(1.0, (1.0 - decayed) * (0.6 if days_away is None else 1.0))), 3),
                sources=("concept_mastery",),
            )
        )
    return out


def _assessment_days_by_course(user_id: int, today: date) -> dict[str, int]:
    """Days until the soonest exam or quiz per course.

    Read from the same assignment list the rest of the product uses, so a
    quiz that has not been imported yet cannot silently create urgency here
    that exists nowhere else on the screen.
    """
    out: dict[str, int] = {}
    for row in _assignments(user_id):
        kind = str(row.get("kind") or "").lower()
        title = str(row.get("title") or "").lower()
        looks_like_assessment = kind in ("exam", "test", "quiz") or any(
            word in title for word in ("exam", "test", "quiz", "midterm", "final")
        )
        if not looks_like_assessment:
            continue
        try:
            due = date.fromisoformat(str(row.get("due_date") or "")[:10])
        except ValueError:
            continue
        days = (due - today).days
        if days < 0:
            continue
        course = str(row.get("course") or "").strip().lower()
        if course and (course not in out or days < out[course]):
            out[course] = days
    return out


# ── behaviour inputs ─────────────────────────────────────────────────


def _session_rows(user_id: int) -> list[dict]:
    """Active-study sittings, with the start time the behavioural model needs.

    ``ActiveSessionRepository.observations`` is shaped for the estimation
    model and does not carry ``started_at``. Time of day is exactly what this
    model is about, so the rows are read here rather than reshaped downstream
    from a field that is not there.
    """
    from App import ActiveSession

    try:
        since = datetime.now() - timedelta(days=HISTORY_DAYS)
        rows = (
            ActiveSession.query
            .filter(ActiveSession.user_id == user_id)
            .filter(ActiveSession.state.in_(("completed", "abandoned")))
            .filter(ActiveSession.started_at >= since)
            .filter(ActiveSession.active_seconds > 60)
            .order_by(ActiveSession.started_at.desc())
            .limit(HISTORY_ROWS)
            .all()
        )
    except Exception as exc:
        logger.warning("session row load failed for %s: %s", user_id, exc)
        return []

    out = []
    for r in rows:
        out.append({
            "actual": int(getattr(r, "active_minutes", 0) or 0),
            "planned_minutes": int(getattr(r, "planned_minutes", 0) or 0),
            "course": getattr(r, "course", "") or "",
            "kind": getattr(r, "kind", "") or "",
            "started_at": getattr(r, "started_at", None),
            "completed": bool(getattr(r, "completed_work", False)),
        })
    return out


def _feedback_rows(user_id: int) -> list[dict]:
    from App import TaskFeedback

    try:
        rows = (
            TaskFeedback.query
            .filter(TaskFeedback.user_id == user_id)
            .order_by(TaskFeedback.completed_at.desc())
            .limit(HISTORY_ROWS)
            .all()
        )
    except Exception as exc:
        logger.warning("feedback row load failed for %s: %s", user_id, exc)
        return []
    return [{
        "actual": int(r.actual_time or 0),
        "estimated": int(r.estimated_time or 0),
        "course": r.course or "",
        "completed_at": r.completed_at,
        # A feedback row exists because the student marked something done.
        "completed": True,
    } for r in rows]


def _daily_minutes(user_id: int) -> dict[str, int]:
    """Minutes actually completed per day — the workload-tolerance input."""
    totals: dict[str, int] = {}
    for row in _session_rows(user_id):
        started = row.get("started_at")
        if not isinstance(started, datetime) or not row.get("completed"):
            continue
        key = started.date().isoformat()
        totals[key] = totals.get(key, 0) + int(row.get("actual") or 0)
    return totals


def _in_progress(user_id: int) -> tuple[frozenset[str], str, int]:
    """Task ids under way, the current course, and the unbroken run so far."""
    from App import ActiveSession

    try:
        running = (
            ActiveSession.query
            .filter(ActiveSession.user_id == user_id)
            .filter(ActiveSession.state.in_(("running", "paused")))
            .order_by(ActiveSession.started_at.desc())
            .limit(5)
            .all()
        )
    except Exception:
        return frozenset(), "", 0

    ids = {str(getattr(r, "task_id", "") or "") for r in running}
    ids.discard("")
    course = ""
    continuous = 0
    if running:
        course = getattr(running[0], "course", "") or ""
        continuous = int(getattr(running[0], "active_minutes", 0) or 0)
    return frozenset(ids), course, continuous


def _remaining_minutes(user_id: int, now: datetime) -> int:
    """Study minutes left in today's availability, after commitments.

    Uses the same window engine the placement stage uses, so "you have 45
    minutes" and "this block fits" cannot disagree.
    """
    import scheduler_engine
    from App import build_scheduler_personalization

    try:
        _dna, availability, commitments = build_scheduler_personalization(
            user_id=user_id, guest_id=None
        )
        windows = scheduler_engine.windows_for_date(
            now.date(), availability, "evening", commitments, now=now,
            busy_by_date=_busy_by_date(user_id),
        )
        return sum(w.minutes for w in windows)
    except Exception as exc:
        logger.warning("remaining-minutes lookup failed for %s: %s", user_id, exc)
        return 0


def _busy_by_date(user_id: int) -> dict:
    from App import _planner_busy_by_date

    del user_id  # the helper reads the request-scoped user itself
    try:
        return _planner_busy_by_date()
    except Exception:
        return {}


def _windows_for(user_id: int):
    """A ``windows_for(day)`` callable for the feasibility checker."""
    import scheduler_engine
    from App import build_scheduler_personalization

    try:
        _dna, availability, commitments = build_scheduler_personalization(
            user_id=user_id, guest_id=None
        )
    except Exception:
        return None

    def windows(day: date):
        return scheduler_engine.windows_for_date(
            day, availability, "evening", commitments, now=None
        )

    return windows


# ── audit ────────────────────────────────────────────────────────────


def _audit() -> ScheduleAuditRepository:
    from App import ScheduleDecision, ScheduleVersion, db

    return ScheduleAuditRepository(ScheduleVersion, ScheduleDecision, db.session)


def _record_decisions(user_id: int, actions, **kwargs):
    return _audit().record_decisions(user_id, actions, **kwargs)


def _record_override(user_id: int, **kwargs):
    return _audit().record_override(user_id, **kwargs)


def _record_version(user_id: int, **kwargs):
    return _audit().record_version(user_id, **kwargs)


def _resolve_decision(user_id: int, task_id: str, accepted: bool) -> bool:
    return _audit().resolve(user_id, task_id, accepted)


def _versions_for(user_id: int, limit: int) -> list:
    return _audit().versions_for(user_id, limit)


def _student_profile(user_id: int) -> tuple[str, bool]:
    """Display name and the AI-personalisation opt-in.

    The opt-in gates the reasoning layer entirely. A student who has not
    agreed to it still gets an explanation — the deterministic one — and
    nothing about them leaves the server.
    """
    from App import User

    try:
        user = User.query.get(user_id)
        if user is None:
            return "", False
        name = user.name or (user.email or "").split("@")[0]
        return name, bool(getattr(user, "ai_personalization_opt_in", False))
    except Exception:
        return "", False


def _emit_signal(user_id: int, kind: str, **kwargs) -> None:
    from App import StudentSignal, db

    from intelliplan.repositories.signals import SignalRepository

    SignalRepository(StudentSignal, db.session).emit(user_id, kind, **kwargs)


# ── service ──────────────────────────────────────────────────────────


def _build_service() -> NextActionService:
    return NextActionService(
        get_active_schedule=_active_schedule,
        get_assignments=_assignments,
        get_mastery=_mastery,
        get_session_rows=_session_rows,
        get_feedback_rows=_feedback_rows,
        get_daily_minutes=_daily_minutes,
        get_in_progress=_in_progress,
        get_remaining_minutes=_remaining_minutes,
    )


next_action_bp = create_next_action_blueprint(
    NextActionDeps(
        get_service=_build_service,
        feature_enabled_for_user=_feature_enabled_for_user,
        current_user_id=_current_user_id,
        now=_now,
        get_active_schedule=_active_schedule,
        save_schedule=_save_schedule,
        windows_for=_windows_for,
        busy_by_date=_busy_by_date,
        record_decisions=_record_decisions,
        record_override=_record_override,
        record_version=_record_version,
        resolve_decision=_resolve_decision,
        versions_for=_versions_for,
        emit_signal=_emit_signal,
        student_profile=_student_profile,
    )
)
