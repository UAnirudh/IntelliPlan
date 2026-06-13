"""Academic Health Score — 0..100 with explainable components.

``compute(snapshot, previous=None) → HealthScore``

The score starts at 100 and is adjusted by a set of named, capped
components. Every component is a :class:`HealthComponent` carrying the
raw measurement, its impact on the score, and a human-readable reason
chip. ``delta_vs_yesterday`` is derived from an optional ``previous``
snapshot — when absent, the delta is zero (we don't invent yesterday).

Pure, deterministic. ``today`` is passed via the snapshot input — the
function itself never reads the clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from intelliplan.domain.assignment import (
    Assignment,
    AssignmentKind,
    AssignmentStatus,
)
from intelliplan.domain.plan import HealthComponent, HealthScore


# ── Component impact caps ────────────────────────────────────────────
#
# Each *unit* of bad signal subtracts up to this many points, capped so
# a single dimension never tanks the score entirely.

_OVERDUE_UNIT_PENALTY        = 8
_OVERDUE_CAP                 = 32   # 4+ overdue items max out at -32
_HIGH_STAKES_UNIT_PENALTY    = 4
_HIGH_STAKES_CAP             = 16
_FAILING_COURSE_PENALTY      = 6
_FAILING_COURSE_CAP          = 24
_DECLINING_COURSE_PENALTY    = 4
_DECLINING_COURSE_CAP        = 12

# Positive signals are smaller, deliberately — health is hard to bank,
# easy to lose. This shapes the score so an A+ student with 1 overdue
# item still drops visibly, instead of being "saved" by their average.

_COMPLETION_BONUS_THRESHOLD = 0.80
_COMPLETION_BONUS_POINTS    = 5
_BALANCE_BONUS_THRESHOLD    = 0.60
_BALANCE_BONUS_POINTS       = 5


# ── Input snapshot ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CourseGrade:
    """One course's current grade picture."""

    course: str
    current_pct: float           # 0..100
    last_three_pct: tuple[float, ...] = ()  # most recent first


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    """Everything ``compute`` needs to produce a :class:`HealthScore`.

    All fields are derived from the existing data sources by the
    repository layer — the engine never touches the DB.
    """

    assignments: tuple[Assignment, ...] = ()
    grades: tuple[CourseGrade, ...] = ()
    completion_rate_7d: float = 0.0   # 0..1
    schedule_balance_index: float = 0.0  # 0..1; 1.0 = perfectly even week
    today: object = None              # ``date`` or ``None``; opaque to engine
    extras: dict[str, str] = field(default_factory=dict)


# ── Helpers ──────────────────────────────────────────────────────────


def _overdue(snapshot: HealthSnapshot) -> int:
    today = snapshot.today
    if today is None:
        return 0
    count = 0
    for a in snapshot.assignments:
        if a.status in (AssignmentStatus.COMPLETED, AssignmentStatus.DISMISSED):
            continue
        if a.due_date is None:
            continue
        if a.due_date < today:  # type: ignore[operator]
            count += 1
    return count


def _high_stakes_soon(snapshot: HealthSnapshot) -> int:
    today = snapshot.today
    if today is None:
        return 0
    count = 0
    for a in snapshot.assignments:
        if a.status is not AssignmentStatus.NOT_STARTED:
            continue
        if a.kind not in (AssignmentKind.EXAM, AssignmentKind.TEST, AssignmentKind.PROJECT):
            continue
        if a.due_date is None:
            continue
        delta = (a.due_date - today).days  # type: ignore[operator]
        if 0 <= delta <= 3:
            count += 1
    return count


def _failing_courses(grades: Iterable[CourseGrade]) -> tuple[str, ...]:
    return tuple(g.course for g in grades if g.current_pct < 70.0)


def _declining_courses(grades: Iterable[CourseGrade]) -> tuple[str, ...]:
    declining: list[str] = []
    for g in grades:
        if len(g.last_three_pct) < 3:
            continue
        # last_three_pct most-recent-first. Trend is recent - oldest.
        recent, _mid, oldest = g.last_three_pct[0], g.last_three_pct[1], g.last_three_pct[2]
        if (oldest - recent) >= 5.0:
            declining.append(g.course)
    return tuple(declining)


def _tier_for(score: int) -> str:
    if score >= 85:
        return "thriving"
    if score >= 70:
        return "steady"
    if score >= 50:
        return "watch"
    return "at_risk"


def _summary(score: int, delta: int, components: tuple[HealthComponent, ...]) -> str:
    if not components:
        return "Health score steady."
    negatives = [c for c in components if c.impact < 0]
    if not negatives:
        if delta > 0:
            return f"Health up {delta} points — strong week."
        return "Health is solid — no risks detected."
    biggest = min(negatives, key=lambda c: c.impact)
    if delta < 0:
        return f"Health dropped {abs(delta)} points — {biggest.reason.lower()}."
    if delta > 0:
        return f"Health up {delta} points — watch: {biggest.reason.lower()}."
    return f"Health steady — watch: {biggest.reason.lower()}."


# ── Public API ───────────────────────────────────────────────────────


def compute(
    snapshot: HealthSnapshot,
    previous: HealthScore | None = None,
) -> HealthScore:
    """Compute the 0..100 Academic Health Score."""

    components: list[HealthComponent] = []
    score = 100

    # ── Penalties ───────────────────────────────────────────────────
    overdue_n = _overdue(snapshot)
    if overdue_n > 0:
        impact = -min(_OVERDUE_CAP, overdue_n * _OVERDUE_UNIT_PENALTY)
        components.append(HealthComponent(
            key="overdue",
            value=float(overdue_n),
            impact=impact,
            reason=f"{overdue_n} overdue item{'s' if overdue_n != 1 else ''}",
        ))
        score += impact

    high_stakes_n = _high_stakes_soon(snapshot)
    if high_stakes_n > 0:
        impact = -min(_HIGH_STAKES_CAP, high_stakes_n * _HIGH_STAKES_UNIT_PENALTY)
        components.append(HealthComponent(
            key="high_stakes_soon",
            value=float(high_stakes_n),
            impact=impact,
            reason=f"{high_stakes_n} high-stakes item{'s' if high_stakes_n != 1 else ''} in next 3 days",
        ))
        score += impact

    failing = _failing_courses(snapshot.grades)
    if failing:
        impact = -min(_FAILING_COURSE_CAP, len(failing) * _FAILING_COURSE_PENALTY)
        components.append(HealthComponent(
            key="failing_courses",
            value=float(len(failing)),
            impact=impact,
            reason=f"Below 70% in {', '.join(failing)}",
        ))
        score += impact

    declining = _declining_courses(snapshot.grades)
    if declining:
        impact = -min(_DECLINING_COURSE_CAP, len(declining) * _DECLINING_COURSE_PENALTY)
        components.append(HealthComponent(
            key="declining_courses",
            value=float(len(declining)),
            impact=impact,
            reason=f"Grade trending down in {', '.join(declining)}",
        ))
        score += impact

    # ── Bonuses ─────────────────────────────────────────────────────
    if snapshot.completion_rate_7d >= _COMPLETION_BONUS_THRESHOLD:
        components.append(HealthComponent(
            key="completion_7d",
            value=snapshot.completion_rate_7d,
            impact=_COMPLETION_BONUS_POINTS,
            reason=f"{round(snapshot.completion_rate_7d * 100)}% completion this week",
        ))
        score += _COMPLETION_BONUS_POINTS
    elif snapshot.completion_rate_7d > 0:
        # Informational only — no impact on score, but shown for context.
        components.append(HealthComponent(
            key="completion_7d",
            value=snapshot.completion_rate_7d,
            impact=0,
            reason=f"{round(snapshot.completion_rate_7d * 100)}% completion this week",
        ))

    if snapshot.schedule_balance_index >= _BALANCE_BONUS_THRESHOLD:
        components.append(HealthComponent(
            key="schedule_balance",
            value=snapshot.schedule_balance_index,
            impact=_BALANCE_BONUS_POINTS,
            reason="Workload evenly distributed",
        ))
        score += _BALANCE_BONUS_POINTS

    # ── Clamp + finalise ────────────────────────────────────────────
    score = max(0, min(100, score))
    delta = score - previous.score if previous else 0
    components_tuple = tuple(components)
    return HealthScore(
        score=score,
        delta_vs_yesterday=delta,
        tier=_tier_for(score),
        components=components_tuple,
        summary=_summary(score, delta, components_tuple),
    )
