"""TodayService — builds the canonical Command Center payload.

Composition root for the read path::

    assignments → priority.rank ─┐
    availability → workload      ├─→ facts ─→ BriefingService ─→ TodayPayload
    grades → health              ─┘

All dependencies are injected. The service itself owns no SQL beyond
what its injected providers do, and it never calls the AI directly —
that's the BriefingService's job (which itself delegates to the
Narrator).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable

from intelliplan.domain.assignment import Assignment
from intelliplan.domain.plan import (
    HealthScore,
    PlannedTask,
    PriorityScore,
    TodayPayload,
    WorkloadForecast,
)
from intelliplan.intelligence import health as health_engine
from intelliplan.intelligence import priority as priority_engine
from intelliplan.intelligence import workload as workload_engine
from intelliplan.intelligence.health import CourseGrade, HealthSnapshot
from intelliplan.services.briefing import BriefingService

logger = logging.getLogger(__name__)

MAX_PLAN_ITEMS = 7


@dataclass(frozen=True, slots=True)
class StudentInfo:
    """What the service needs to know about the student."""

    user_id: int
    name: str = ""
    grade_level: str = ""
    availability: dict[str, int] | None = None  # weekday → minutes
    personalization_enabled: bool = False


class TodayService:
    def __init__(
        self,
        *,
        assignments_repo: Any,                      # .for_user(user_id, today) → tuple[Assignment, ...]
        briefing_service: BriefingService,
        get_student: Callable[[int], StudentInfo],
        get_grades: Callable[[int], tuple[CourseGrade, ...]] = lambda _uid: (),
        get_completion_rate_7d: Callable[[int], float] = lambda _uid: 0.0,
        health_snapshot_model: type | None = None,
        session: Any = None,
        now: Callable[[], datetime] = datetime.utcnow,
    ) -> None:
        self._assignments = assignments_repo
        self._briefing = briefing_service
        self._get_student = get_student
        self._get_grades = get_grades
        self._get_completion = get_completion_rate_7d
        self._health_model = health_snapshot_model
        self._session = session
        self._now = now

    # ── public API ───────────────────────────────────────────────────

    def build(self, user_id: int, *, force_brief: bool = False) -> TodayPayload:
        now = self._now()
        today = now.date()
        student = self._get_student(user_id)

        assignments = tuple(self._assignments.for_user(user_id, today))
        grades = tuple(self._get_grades(user_id))
        completion = float(self._get_completion(user_id))

        # ── deterministic engines ────────────────────────────────────
        ctx = priority_engine.PriorityContext.from_assignments(today, assignments)
        ranked = priority_engine.rank(assignments, ctx)

        forecast = workload_engine.forecast(assignments, student.availability, today)
        balance = _balance_index(forecast)

        snapshot = HealthSnapshot(
            assignments=assignments,
            grades=grades,
            completion_rate_7d=completion,
            schedule_balance_index=balance,
            today=today,
        )
        previous = self._yesterday_health(user_id, today)
        health = health_engine.compute(snapshot, previous=previous)
        self._persist_health(user_id, today, health, snapshot)

        plan = tuple(
            PlannedTask(
                assignment=a,
                priority=score,
                why_now=_why_now(a, score, forecast),
                deep_link="/priority",
            )
            for a, score in ranked[:MAX_PLAN_ITEMS]
        )

        # ── narration (cached) ───────────────────────────────────────
        facts = self._facts(student, plan, forecast, health)
        briefing = self._briefing.get_or_refresh(
            user_id, facts, student_name=student.name, force=force_brief,
        )

        return TodayPayload(
            generated_at=now,
            student_name=student.name,
            student_grade_level=student.grade_level,
            personalization_enabled=student.personalization_enabled,
            briefing=briefing,
            plan=plan,
            forecast=forecast,
            health=health,
        )

    # ── facts for the Narrator ───────────────────────────────────────

    @staticmethod
    def _facts(
        student: StudentInfo,
        plan: tuple[PlannedTask, ...],
        forecast: WorkloadForecast,
        health: HealthScore,
    ) -> dict[str, Any]:
        """JSON-safe, deterministic dict. This is what gets hashed for
        cache invalidation — keep keys stable and values canonical."""
        top_tasks = [
            {
                "title": t.assignment.title,
                "course": t.assignment.course,
                "due_date": t.assignment.due_date.isoformat() if t.assignment.due_date else None,
                "est_minutes": t.assignment.est_minutes,
                "priority_tier": t.priority.tier.value,
                "top_reason": t.priority.rationale[0].reason if t.priority.rationale else "",
            }
            for t in plan[:3]
        ]
        total_remaining_min = sum(t.assignment.est_minutes for t in plan)
        return {
            "grade_level": student.grade_level,
            "top_tasks": top_tasks,
            "task_count": len(plan),
            "total_remaining_minutes": total_remaining_min,
            "heaviest_day_name": forecast.heaviest_day.strftime("%A") if forecast.heaviest_day else "",
            "workload_summary": forecast.summary,
            "health_score": health.score,
            "health_delta": health.delta_vs_yesterday,
            "health_summary": health.summary,
        }

    # ── health persistence (delta source) ────────────────────────────

    def _yesterday_health(self, user_id: int, today: date) -> HealthScore | None:
        if self._health_model is None or self._session is None:
            return None
        try:
            row = (
                self._session.query(self._health_model)
                .filter(self._health_model.user_id == user_id)
                .filter(self._health_model.snapshot_date < today)
                .order_by(self._health_model.snapshot_date.desc())
                .first()
            )
        except Exception as exc:
            logger.warning("yesterday-health lookup failed: %s", exc)
            return None
        if row is None:
            return None
        return HealthScore(
            score=int(row.score or 0),
            delta_vs_yesterday=0,
            tier="steady",
            components=(),
            summary="",
        )

    def _persist_health(
        self, user_id: int, today: date, health: HealthScore, snapshot: HealthSnapshot
    ) -> None:
        if self._health_model is None or self._session is None:
            return
        try:
            row = (
                self._session.query(self._health_model)
                .filter_by(user_id=user_id, snapshot_date=today)
                .first()
            )
            if row is None:
                row = self._health_model(user_id=user_id, snapshot_date=today)
                self._session.add(row)
            row.score = health.score
            row.overdue_count = int(next(
                (c.value for c in health.components if c.key == "overdue"), 0))
            row.high_stakes_soon_count = int(next(
                (c.value for c in health.components if c.key == "high_stakes_soon"), 0))
            row.failing_courses_count = int(next(
                (c.value for c in health.components if c.key == "failing_courses"), 0))
            row.declining_courses_count = int(next(
                (c.value for c in health.components if c.key == "declining_courses"), 0))
            row.completion_rate_7d = snapshot.completion_rate_7d
            row.schedule_balance_index = snapshot.schedule_balance_index
            row.components_json = json.dumps(
                [
                    {"key": c.key, "value": c.value, "impact": c.impact, "reason": c.reason}
                    for c in health.components
                ],
                ensure_ascii=False,
            )
            self._session.commit()
        except Exception as exc:
            logger.warning("health snapshot persist failed: %s", exc)
            try:
                self._session.rollback()
            except Exception:
                pass


# ── helpers ──────────────────────────────────────────────────────────


def _balance_index(forecast: WorkloadForecast) -> float:
    """0..1 — how evenly the week's load is spread.

    1.0 = perfectly even. Uses 1 - (normalised mean absolute deviation).
    A week with zero load counts as balanced.
    """
    loads = [d.committed_minutes + d.prework_minutes for d in forecast.days]
    total = sum(loads)
    if total == 0:
        return 1.0
    mean = total / len(loads)
    mad = sum(abs(x - mean) for x in loads) / len(loads)
    # MAD can at most approach mean * 2 * (n-1)/n; normalising by mean
    # keeps the index scale-free across light and heavy weeks.
    return max(0.0, min(1.0, 1.0 - (mad / (mean * 2))))


def _why_now(a: Assignment, score: PriorityScore, forecast: WorkloadForecast) -> str:
    """Deterministic one-liner per task. The Narrator personalises the
    briefing; per-row text stays template-built so the plan renders
    instantly and identically with AI down."""
    chips = {c.key: c for c in score.rationale}
    urgency = chips.get("urgency")
    dependency = chips.get("dependency")
    if dependency and dependency.weight > 0:
        return dependency.reason
    if urgency and urgency.weight >= 34:
        reason = urgency.reason
        if forecast.heaviest_day and a.due_date == forecast.heaviest_day:
            return f"{reason} — and it lands on your heaviest day"
        return reason
    impact = chips.get("grade_impact")
    if impact and impact.weight >= 12:
        return f"{impact.reason} — high impact on your grade"
    if urgency:
        return urgency.reason
    return "Keeps you ahead of schedule"
