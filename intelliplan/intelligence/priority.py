"""Deterministic priority engine.

``compute(assignment, context) → PriorityScore``

The score is the sum of five components, each capped, each producing a
:class:`~intelliplan.domain.plan.ReasonChip` so the UI can render an
explanation next to the number.

Component caps:

==============  ===  Why
Urgency         40   Strongest driver — soonest deadlines move first.
Importance      25   What kind of work it is.
Grade impact    20   Weight in the course grade.
Effort          10   Small bonus for momentum-friendly fits.
Dependency       5   Tiebreaker — prereq for an upcoming exam.
==============  ===

Total cap: 100. ``PriorityScore.score`` is always clamped to
``[0, 100]`` and is integer for stable hashing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from intelliplan.domain.assignment import (
    Assignment,
    AssignmentKind,
    AssignmentStatus,
)
from intelliplan.domain.plan import PriorityScore, ReasonChip


# ── Component weights ────────────────────────────────────────────────

_IMPORTANCE_WEIGHTS: dict[AssignmentKind, int] = {
    AssignmentKind.EXAM:      25,
    AssignmentKind.PROJECT:   20,
    AssignmentKind.TEST:      15,
    AssignmentKind.LAB:       12,
    AssignmentKind.HOMEWORK:   8,
    AssignmentKind.CLASSWORK:  5,
    AssignmentKind.OTHER:      4,
}

_IMPORTANCE_LABEL: dict[AssignmentKind, str] = {
    AssignmentKind.EXAM:      "Exam",
    AssignmentKind.PROJECT:   "Project / essay",
    AssignmentKind.TEST:      "Test / quiz",
    AssignmentKind.LAB:       "Lab",
    AssignmentKind.HOMEWORK:  "Homework",
    AssignmentKind.CLASSWORK: "Classwork",
    AssignmentKind.OTHER:     "General assignment",
}


# ── Context ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PriorityContext:
    """Everything the engine needs that isn't on the assignment itself.

    ``today`` is passed explicitly — the engine never reads the clock.
    ``course_max_points`` maps course → total points, used by the grade-
    impact component. ``upcoming_assessments_by_course`` lists upcoming
    high-stakes items per course; used by the dependency component to
    flag "review guide" → "test" prereqs.
    """

    today: date
    course_max_points: dict[str, float] = field(default_factory=dict)
    upcoming_assessments_by_course: dict[str, tuple[Assignment, ...]] = field(
        default_factory=dict
    )

    @classmethod
    def from_assignments(
        cls,
        today: date,
        assignments: Iterable[Assignment],
        course_max_points: dict[str, float] | None = None,
    ) -> "PriorityContext":
        """Convenience builder — derives the per-course assessment index."""
        by_course: dict[str, list[Assignment]] = {}
        for a in assignments:
            if a.kind not in (AssignmentKind.EXAM, AssignmentKind.TEST):
                continue
            if a.due_date is None or a.due_date < today:
                continue
            if (a.due_date - today).days > 14:
                continue
            by_course.setdefault(a.course, []).append(a)
        return cls(
            today=today,
            course_max_points=dict(course_max_points or {}),
            upcoming_assessments_by_course={
                k: tuple(sorted(v, key=lambda x: x.due_date or today))
                for k, v in by_course.items()
            },
        )


# ── Component functions ──────────────────────────────────────────────


def _urgency(assignment: Assignment, ctx: PriorityContext) -> ReasonChip:
    """Urgency: 0..40, piecewise on ``days_until_due``."""
    d = assignment.days_until_due(ctx.today)
    if d is None:
        return ReasonChip(key="urgency", weight=2, reason="No due date set")
    if d < 0:
        return ReasonChip(
            key="urgency",
            weight=40,
            reason=f"Overdue by {abs(d)} day{'s' if abs(d) != 1 else ''}",
        )
    if d == 0:
        return ReasonChip(key="urgency", weight=38, reason="Due today")
    if d == 1:
        return ReasonChip(key="urgency", weight=34, reason="Due tomorrow")
    if d == 2:
        return ReasonChip(key="urgency", weight=28, reason="Due in 2 days")
    if d == 3:
        return ReasonChip(key="urgency", weight=22, reason="Due in 3 days")
    if d <= 5:
        return ReasonChip(key="urgency", weight=14, reason=f"Due in {d} days")
    if d <= 7:
        return ReasonChip(key="urgency", weight=8, reason="Due this week")
    if assignment.status is AssignmentStatus.NOT_STARTED:
        return ReasonChip(key="urgency", weight=4, reason=f"Due in {d} days — not started")
    return ReasonChip(key="urgency", weight=2, reason=f"Due in {d} days")


def _importance(assignment: Assignment, ctx: PriorityContext) -> ReasonChip:
    """Importance: 0..25, based on assignment kind."""
    weight = _IMPORTANCE_WEIGHTS[assignment.kind]
    label = _IMPORTANCE_LABEL[assignment.kind]
    return ReasonChip(key="importance", weight=weight, reason=label)


def _grade_impact(assignment: Assignment, ctx: PriorityContext) -> ReasonChip:
    """Grade impact: 0..20.

    Preferred path: ``points_possible / course_max_points * 20`` when the
    course total is known. Falls back to point-bracket heuristic when not.
    """
    pts = max(0.0, float(assignment.points_possible or 0.0))
    course_max = (
        assignment.course_max_points
        or ctx.course_max_points.get(assignment.course)
        or 0.0
    )
    if course_max and course_max > 0:
        pct = pts / course_max
        weight = max(0, min(20, round(pct * 20)))
        return ReasonChip(
            key="grade_impact",
            weight=weight,
            reason=f"{round(pct * 100)}% of {assignment.course} points",
        )
    if pts >= 80:
        return ReasonChip(key="grade_impact", weight=18, reason="Large point value")
    if pts >= 30:
        return ReasonChip(key="grade_impact", weight=12, reason="Moderate point value")
    if pts > 0:
        return ReasonChip(key="grade_impact", weight=6, reason="Small point value")
    return ReasonChip(key="grade_impact", weight=4, reason="Point value unknown")


def _effort(assignment: Assignment, ctx: PriorityContext) -> ReasonChip:
    """Effort: 0..10. Momentum-friendly fits get a small bonus."""
    d = assignment.days_until_due(ctx.today)
    est = max(0, int(assignment.est_minutes or 0))
    if est == 0:
        return ReasonChip(key="effort", weight=3, reason="No time estimate")
    if est <= 30 and d is not None and 0 <= d <= 3:
        return ReasonChip(key="effort", weight=10, reason="Quick win — fits in one sitting")
    if est >= 120 and assignment.status is AssignmentStatus.NOT_STARTED:
        return ReasonChip(key="effort", weight=8, reason="Large task — start early")
    if est <= 60:
        return ReasonChip(key="effort", weight=6, reason="Manageable in one session")
    return ReasonChip(key="effort", weight=4, reason=f"~{est // 60}h of work")


_PREREQ_HINT_RE = (
    "review", "study guide", "practice", "prep", "outline",
    "notes", "flashcards", "rough draft",
)


def _dependency(assignment: Assignment, ctx: PriorityContext) -> ReasonChip:
    """Dependency: 0..5. Bonus when this looks like prep for an exam."""
    if assignment.kind in (AssignmentKind.EXAM, AssignmentKind.TEST):
        return ReasonChip(key="dependency", weight=0, reason="")
    upcoming = ctx.upcoming_assessments_by_course.get(assignment.course)
    if not upcoming:
        return ReasonChip(key="dependency", weight=0, reason="")
    title_lower = assignment.title.lower()
    looks_like_prep = any(h in title_lower for h in _PREREQ_HINT_RE)
    if not looks_like_prep:
        return ReasonChip(key="dependency", weight=0, reason="")
    next_exam = upcoming[0]
    days_to_exam = (next_exam.due_date - ctx.today).days if next_exam.due_date else None
    if days_to_exam is None or days_to_exam < 0 or days_to_exam > 7:
        return ReasonChip(key="dependency", weight=0, reason="")
    return ReasonChip(
        key="dependency",
        weight=5,
        reason=f"Prep for {next_exam.title} in {days_to_exam}d",
    )


# ── Public API ───────────────────────────────────────────────────────


def compute(assignment: Assignment, context: PriorityContext) -> PriorityScore:
    """Compute the 0..100 priority score for one assignment.

    Deterministic. Pure. Same input → same output.
    """
    chips = (
        _urgency(assignment, context),
        _importance(assignment, context),
        _grade_impact(assignment, context),
        _effort(assignment, context),
        _dependency(assignment, context),
    )
    # Drop the dependency chip when it contributed nothing — the UI
    # doesn't need an empty row, but we keep it during scoring so the
    # rationale ordering stays stable for tests.
    rationale = tuple(c for c in chips if c.weight > 0 or c.key != "dependency")
    raw = sum(c.weight for c in chips)
    score = max(0, min(100, int(raw)))
    return PriorityScore(
        score=score,
        tier=PriorityScore.tier_for(score),
        rationale=rationale,
    )


def rank(
    assignments: Iterable[Assignment], context: PriorityContext
) -> tuple[tuple[Assignment, PriorityScore], ...]:
    """Score and rank a list of assignments, highest score first.

    Ties are broken by due-date-ascending then by title for stability.
    """
    scored = [(a, compute(a, context)) for a in assignments]
    scored.sort(
        key=lambda pair: (
            -pair[1].score,
            pair[0].due_date or context.today.replace(year=context.today.year + 100),
            pair[0].title,
        )
    )
    return tuple(scored)
