"""LearningGraphService — composition root for the Learning Intelligence Layer.

All dependencies are injected. The service orchestrates repositories,
prediction engines, and profile recomputation. Integration hooks are
thin methods called from App.py routes via the glue layer.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Callable

from intelliplan.domain.learning import (
    ConceptSnapshot,
    LearningDashboardPayload,
    PredictionResult,
    StudentProfileSnapshot,
    SubjectMastery,
)
from intelliplan.intelligence import predictions as pred
from intelliplan.repositories.concept_mastery import ConceptMasteryRepository
from intelliplan.repositories.learning_events import LearningEventRepository
from intelliplan.repositories.student_profile import StudentProfileRepository

logger = logging.getLogger(__name__)


def _safe_json_loads(text: str, default: Any = None) -> Any:
    try:
        return json.loads(text or "null") or default
    except (TypeError, json.JSONDecodeError):
        return default


class LearningGraphService:
    def __init__(
        self,
        *,
        event_repo: LearningEventRepository,
        concept_repo: ConceptMasteryRepository,
        profile_repo: StudentProfileRepository,
        get_study_mastery: Callable[[int], list[Any]],
        get_task_feedback: Callable[[int], list[Any]],
        get_grades: Callable[[int], list[Any]],
        get_study_sessions: Callable[[int], list[Any]],
        get_streak: Callable[[int], Any | None],
        get_health: Callable[[int], Any | None],
        now: Callable[[], datetime] = datetime.utcnow,
    ) -> None:
        self._events = event_repo
        self._concepts = concept_repo
        self._profiles = profile_repo
        self._get_study_mastery = get_study_mastery
        self._get_task_feedback = get_task_feedback
        self._get_grades = get_grades
        self._get_study_sessions = get_study_sessions
        self._get_streak = get_streak
        self._get_health = get_health
        self._now = now

    # ── Integration hooks ────────────────────────────────────────────

    def on_task_completed(self, user_id: int, data: dict[str, Any]) -> None:
        course = data.get("course", "")
        self._events.emit(
            user_id,
            "assignment_completed",
            "feedback",
            subject=course,
            reference_id=str(data.get("title", "")),
            value=data,
        )
        if course:
            self._concepts.upsert(
                user_id, course, "general", "task_completion",
                correct=True, confidence_delta=0.02,
            )

    def on_grade_changed(
        self,
        user_id: int,
        course: str,
        old_pct: float | None,
        new_pct: float,
    ) -> None:
        self._events.emit(
            user_id,
            "grade_changed",
            "grade_import",
            subject=course,
            value={"old_pct": old_pct, "new_pct": new_pct},
        )
        if course:
            improving = old_pct is not None and new_pct > old_pct
            delta = 0.05 if improving else -0.03
            self._concepts.upsert(
                user_id, course, "general", "course_grade",
                correct=improving, confidence_delta=delta,
            )

    def on_tutor_interaction(
        self,
        user_id: int,
        subject: str,
        concepts: list[dict[str, Any]],
    ) -> None:
        self._events.emit(
            user_id,
            "tutor_interaction",
            "tutor",
            subject=subject,
            value={"concepts": concepts},
        )
        for c in concepts:
            topic = c.get("topic", subject)
            concept_name = c.get("concept", topic)
            understood = c.get("understood", None)
            self._concepts.upsert(
                user_id, subject, topic, concept_name,
                correct=understood,
                confidence_delta=0.03 if understood else -0.02,
            )

    def on_study_session_ended(self, user_id: int, data: dict[str, Any]) -> None:
        self._events.emit(
            user_id,
            "study_session_ended",
            "study",
            value=data,
        )

    def on_concept_reviewed(self, user_id: int, data: dict[str, Any]) -> None:
        topic = data.get("topic", "")
        verdict = data.get("verdict", "")
        if not topic:
            return
        self._events.emit(
            user_id,
            "concept_reviewed",
            "study",
            subject=topic,
            reference_id=data.get("question_key", ""),
            value=data,
        )
        self._concepts.upsert(
            user_id,
            topic,
            topic,
            data.get("question_key", topic)[:256],
            correct=verdict == "correct",
            confidence_delta=0.05 if verdict == "correct" else -0.03,
        )

    # ── Aggregation ──────────────────────────────────────────────────

    def sync_concept_mastery(self, user_id: int) -> int:
        """Roll up StudyMastery rows into ConceptMastery. Returns count synced."""
        rows = self._get_study_mastery(user_id)
        count = 0
        for row in rows:
            topic = getattr(row, "topic", "") or "General"
            q_key = getattr(row, "question_key", "")[:256] or topic
            times_correct = getattr(row, "times_correct", 0)
            times_seen = getattr(row, "times_seen", 0)
            correct = times_correct > (times_seen - times_correct)
            self._concepts.upsert(
                user_id, topic, topic, q_key,
                correct=correct, confidence_delta=0.0,
            )
            count += 1
        return count

    def recompute_profile(self, user_id: int) -> StudentProfileSnapshot:
        """Recompute the student profile from all data sources."""
        now = self._now()
        profile = self._profiles.get_or_create(user_id)

        concepts = self._concepts.for_user(user_id)
        feedback = self._get_task_feedback(user_id)
        grades = self._get_grades(user_id)
        sessions = self._get_study_sessions(user_id)
        streak = self._get_streak(user_id)

        # Learning pace from session frequency
        week_ago = now - timedelta(days=7)
        event_count_7d = self._events.count_since(user_id, week_ago)
        if event_count_7d >= 20:
            pace = "fast"
        elif event_count_7d >= 8:
            pace = "moderate"
        elif event_count_7d >= 1:
            pace = "slow"
        else:
            pace = "unknown"

        # Subject strengths/weaknesses from concept mastery
        subject_scores: dict[str, list[float]] = defaultdict(list)
        for c in concepts:
            subject_scores[c.subject].append(c.mastery_score)

        subject_avgs = {
            subj: sum(scores) / len(scores)
            for subj, scores in subject_scores.items()
            if scores
        }
        sorted_subjects = sorted(subject_avgs.items(), key=lambda x: x[1], reverse=True)
        strongest = [s for s, _ in sorted_subjects[:5] if subject_avgs[s] >= 0.5]
        weakest = [s for s, _ in sorted_subjects[-5:] if subject_avgs[s] < 0.7]

        # Retention score from SM-2 data
        if concepts:
            review_intervals = []
            for c in concepts:
                if c.last_reviewed and c.times_seen >= 2:
                    days_ago = (now - c.last_reviewed).days
                    if days_ago <= 30:
                        review_intervals.append(c.mastery_score)
            retention = sum(review_intervals) / len(review_intervals) if review_intervals else 0.0
        else:
            retention = 0.0

        # Estimate ratio from task feedback
        ratios = []
        for fb in feedback:
            est = getattr(fb, "estimated_time", 0) or 0
            act = getattr(fb, "actual_time", None)
            if est > 0 and act and act > 0:
                ratios.append(act / est)
        avg_ratio = sum(ratios) / len(ratios) if ratios else 1.0

        # Engagement score
        session_count = len(sessions) if sessions else 0
        streak_days = getattr(streak, "current_streak", 0) if streak else 0
        engagement = min(1.0, (event_count_7d / 30.0) + (streak_days / 60.0) + (session_count / 50.0))

        # GPA snapshot
        gpa = None
        if grades:
            pcts = []
            for g in grades:
                pct = getattr(g, "percentage", None)
                if pct is not None:
                    pcts.append(float(pct))
            if pcts:
                avg_pct = sum(pcts) / len(pcts)
                gpa = _pct_to_gpa(avg_pct)

        # Preferred methods from tutor profile (passed via grades callback)
        methods: list[str] = []

        # Persist
        if profile is not None:
            profile.learning_pace = pace
            profile.strongest_subjects_json = json.dumps(strongest)
            profile.weakest_subjects_json = json.dumps(weakest)
            profile.preferred_methods_json = json.dumps(methods)
            profile.retention_score = round(retention, 4)
            profile.avg_estimate_ratio = round(avg_ratio, 4)
            profile.engagement_score = round(engagement, 4)
            profile.gpa_snapshot = round(gpa, 2) if gpa is not None else None
            profile.recomputed_at = now
            self._profiles.save(profile)

        return StudentProfileSnapshot(
            user_id=user_id,
            learning_pace=pace,
            strongest_subjects=tuple(strongest),
            weakest_subjects=tuple(weakest),
            preferred_methods=tuple(methods),
            retention_score=round(retention, 4),
            avg_estimate_ratio=round(avg_ratio, 4),
            engagement_score=round(engagement, 4),
            gpa_snapshot=round(gpa, 2) if gpa is not None else None,
        )

    # ── Dashboard ────────────────────────────────────────────────────

    def build_dashboard(self, user_id: int) -> LearningDashboardPayload:
        now = self._now()
        profile_snap = self.recompute_profile(user_id)

        strongest_rows = self._concepts.strongest(user_id, limit=8)
        weakest_rows = self._concepts.weakest(user_id, limit=8)
        forgetting_rows = self._concepts.forgetting_soon(user_id, now, threshold_days=7)

        strongest = tuple(_row_to_snapshot(r, now) for r in strongest_rows)
        weakest = tuple(_row_to_snapshot(r, now) for r in weakest_rows)
        forgetting = tuple(_row_to_snapshot(r, now) for r in forgetting_rows)

        # Mastery by subject
        all_concepts = self._concepts.for_user(user_id)
        subject_groups: dict[str, list[Any]] = defaultdict(list)
        for c in all_concepts:
            subject_groups[c.subject].append(c)

        mastery_by_subject = tuple(
            SubjectMastery(
                subject=subj,
                avg_mastery=round(sum(c.mastery_score for c in rows) / len(rows), 3),
                concept_count=len(rows),
                weakest_concept=min(rows, key=lambda c: c.mastery_score).concept if rows else "",
                strongest_concept=max(rows, key=lambda c: c.mastery_score).concept if rows else "",
            )
            for subj, rows in sorted(subject_groups.items())
            if rows
        )

        # Predictions
        predictions_list: list[PredictionResult] = []

        grades = self._get_grades(user_id)
        if grades:
            feedback = self._get_task_feedback(user_id)
            feedback_records = tuple(
                pred.FeedbackRecord(
                    course=getattr(fb, "course", ""),
                    estimated_time=getattr(fb, "estimated_time", 60),
                    actual_time=getattr(fb, "actual_time", None) or 0,
                    difficulty=getattr(fb, "difficulty", "Medium"),
                    completed_at_weekday=getattr(fb, "day_of_week", ""),
                    completed_at_hour=0,
                )
                for fb in (feedback or [])
            )
            grade_tuples = tuple(
                pred.CourseGradeInput(
                    course=getattr(g, "course", ""),
                    current_pct=float(getattr(g, "percentage", 0) or 0),
                )
                for g in grades
                if getattr(g, "percentage", None) is not None
            )
            if grade_tuples:
                gpa_pred = pred.predict_gpa_trajectory(pred.GradeTrajectoryInput(
                    current_grades=grade_tuples,
                    feedback_history=feedback_records,
                    days_remaining_in_term=60,
                ))
                predictions_list.append(gpa_pred)

        # Burnout risk
        sessions = self._get_study_sessions(user_id) or []
        streak = self._get_streak(user_id)
        health = self._get_health(user_id)

        recent_minutes = _session_minutes_by_day(sessions, now, 7)
        prior_minutes = _session_minutes_by_day(sessions, now - timedelta(days=7), 7)
        overdue_count = getattr(health, "overdue_count", 0) if health else 0

        burnout_pred = pred.predict_burnout_risk(pred.BurnoutRiskInput(
            study_minutes_7d=tuple(recent_minutes),
            study_minutes_prior_7d=tuple(prior_minutes),
            streak_days=getattr(streak, "current_streak", 0) if streak else 0,
            avg_daily_workload=sum(recent_minutes) / max(1, len(recent_minutes)),
            available_daily_minutes=180.0,
            overdue_count=overdue_count,
        ))
        predictions_list.append(burnout_pred)

        # Study trend (30 days)
        study_trend: dict[str, int] = {}
        for i in range(30):
            d = (now - timedelta(days=29 - i)).date()
            study_trend[d.isoformat()] = 0
        for s in sessions:
            created = getattr(s, "created_at", None)
            dur = getattr(s, "duration_seconds", 0) or 0
            if created:
                key = created.date().isoformat() if hasattr(created, "date") else str(created)[:10]
                if key in study_trend:
                    study_trend[key] += dur // 60

        # Retention trend (7 days)
        retention_trend: dict[str, float] = {}
        for i in range(7):
            d = (now - timedelta(days=6 - i)).date()
            retention_trend[d.isoformat()] = profile_snap.retention_score

        week_ago = now - timedelta(days=7)
        event_count = self._events.count_since(user_id, week_ago)

        return LearningDashboardPayload(
            profile=profile_snap,
            strongest_concepts=strongest,
            weakest_concepts=weakest,
            forgetting_soon=forgetting,
            mastery_by_subject=mastery_by_subject,
            predictions=tuple(predictions_list),
            study_trend=study_trend,
            retention_trend=retention_trend,
            event_count_7d=event_count,
        )


# ── Helpers ──────────────────────────────────────────────────────────


def _row_to_snapshot(row: Any, now: datetime) -> ConceptSnapshot:
    times_seen = getattr(row, "times_seen", 0) or 1
    times_correct = getattr(row, "times_correct", 0)
    last_reviewed = getattr(row, "last_reviewed", None)
    days_since = (now - last_reviewed).days if last_reviewed else None

    decay_rate = getattr(row, "decay_rate", 0.05)
    forgetting_risk = 0.0
    if days_since is not None and days_since > 0:
        forgetting_risk = min(1.0, 1.0 - (2.0 ** (-decay_rate * days_since)))

    return ConceptSnapshot(
        subject=row.subject,
        topic=row.topic,
        concept=row.concept,
        mastery_score=row.mastery_score,
        confidence_score=row.confidence_score,
        times_seen=times_seen,
        accuracy=times_correct / max(1, times_seen),
        days_since_review=days_since,
        forgetting_risk=round(forgetting_risk, 4),
    )


def _pct_to_gpa(pct: float) -> float:
    if pct >= 93:
        return 4.0
    if pct >= 90:
        return 3.7
    if pct >= 87:
        return 3.3
    if pct >= 83:
        return 3.0
    if pct >= 80:
        return 2.7
    if pct >= 77:
        return 2.3
    if pct >= 73:
        return 2.0
    if pct >= 70:
        return 1.7
    if pct >= 67:
        return 1.3
    if pct >= 60:
        return 1.0
    return 0.0


def _session_minutes_by_day(
    sessions: list[Any], ref: datetime, days: int,
) -> list[int]:
    by_day: dict[str, int] = {}
    for i in range(days):
        d = (ref - timedelta(days=days - 1 - i)).date()
        by_day[d.isoformat()] = 0
    for s in sessions:
        created = getattr(s, "created_at", None)
        dur = getattr(s, "duration_seconds", 0) or 0
        if created:
            key = created.date().isoformat() if hasattr(created, "date") else str(created)[:10]
            if key in by_day:
                by_day[key] += dur // 60
    return list(by_day.values())
