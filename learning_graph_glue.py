"""Glue between App.py and the Learning Intelligence Layer blueprint.

Same pattern as ``command_center_glue.py``: all App imports are LAZY
(inside functions) to avoid circular imports. By the time any of these
callables run, App is fully loaded.

Every hook wrapper swallows exceptions — telemetry must never break
user-facing requests.
"""

from __future__ import annotations

import logging
from typing import Any

from flask_login import current_user

from intelliplan.api.learning_graph import (
    LearningGraphDeps,
    create_learning_graph_blueprint,
)
from intelliplan.repositories.concept_mastery import ConceptMasteryRepository
from intelliplan.repositories.learning_events import LearningEventRepository
from intelliplan.repositories.student_profile import StudentProfileRepository
from intelliplan.services.learning_graph import LearningGraphService

logger = logging.getLogger(__name__)


# ── Dependency callables (lazy App imports) ──────────────────────────


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


def _get_study_mastery(user_id: int) -> list[Any]:
    from App import StudyMastery
    try:
        return StudyMastery.query.filter_by(user_id=user_id).all()
    except Exception:
        return []


def _get_task_feedback(user_id: int) -> list[Any]:
    from App import TaskFeedback
    try:
        return TaskFeedback.query.filter_by(user_id=user_id).all()
    except Exception:
        return []


def _get_grades(user_id: int) -> list[Any]:
    from App import ImportedGrade
    try:
        return ImportedGrade.query.filter_by(user_id=user_id).all()
    except Exception:
        return []


def _get_study_sessions(user_id: int) -> list[Any]:
    from App import StudySession
    try:
        return StudySession.query.filter_by(user_id=user_id).all()
    except Exception:
        return []


def _get_streak(user_id: int) -> Any | None:
    from App import UserStreak
    try:
        return UserStreak.query.filter_by(user_id=user_id).first()
    except Exception:
        return None


def _get_health(user_id: int) -> Any | None:
    from App import HealthSnapshot
    from datetime import date
    try:
        return (
            HealthSnapshot.query
            .filter_by(user_id=user_id)
            .order_by(HealthSnapshot.snapshot_date.desc())
            .first()
        )
    except Exception:
        return None


def _build_service() -> LearningGraphService:
    from App import ConceptMastery, LearningEvent, StudentProfile, db

    event_repo = LearningEventRepository(LearningEvent, db.session)
    concept_repo = ConceptMasteryRepository(ConceptMastery, db.session)
    profile_repo = StudentProfileRepository(StudentProfile, db.session)

    return LearningGraphService(
        event_repo=event_repo,
        concept_repo=concept_repo,
        profile_repo=profile_repo,
        get_study_mastery=_get_study_mastery,
        get_task_feedback=_get_task_feedback,
        get_grades=_get_grades,
        get_study_sessions=_get_study_sessions,
        get_streak=_get_streak,
        get_health=_get_health,
    )


# ── Fire-and-forget hooks (called from App.py routes) ────────────────


def _learning_graph_on_task_completed(user_id: int, data: dict) -> None:
    try:
        svc = _build_service()
        svc.on_task_completed(user_id, data)
    except Exception as exc:
        logger.warning("learning graph: on_task_completed failed: %s", exc)


def _learning_graph_on_grade_changed(
    user_id: int, course: str, old_pct: float | None, new_pct: float,
) -> None:
    try:
        svc = _build_service()
        svc.on_grade_changed(user_id, course, old_pct, new_pct)
    except Exception as exc:
        logger.warning("learning graph: on_grade_changed failed: %s", exc)


def _learning_graph_on_tutor_interaction(
    user_id: int, subject: str, concepts: list[dict],
) -> None:
    try:
        svc = _build_service()
        svc.on_tutor_interaction(user_id, subject, concepts)
    except Exception as exc:
        logger.warning("learning graph: on_tutor_interaction failed: %s", exc)


def _learning_graph_on_study_session_ended(user_id: int, data: dict) -> None:
    try:
        svc = _build_service()
        svc.on_study_session_ended(user_id, data)
    except Exception as exc:
        logger.warning("learning graph: on_study_session_ended failed: %s", exc)


def _learning_graph_on_concept_reviewed(user_id: int, data: dict) -> None:
    try:
        svc = _build_service()
        svc.on_concept_reviewed(user_id, data)
    except Exception as exc:
        logger.warning("learning graph: on_concept_reviewed failed: %s", exc)


# ── Blueprint ────────────────────────────────────────────────────────


learning_graph_bp = create_learning_graph_blueprint(
    LearningGraphDeps(
        get_service=_build_service,
        feature_enabled=_feature_enabled,
        current_user_id=_current_user_id,
    )
)
