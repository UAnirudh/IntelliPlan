"""Tests for the deterministic priority engine."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from intelliplan.domain.assignment import (
    Assignment,
    AssignmentKind,
    AssignmentStatus,
)
from intelliplan.domain.plan import PriorityTier
from intelliplan.intelligence import priority
from intelliplan.intelligence.priority import (
    PriorityContext,
    compute,
    rank,
)


TODAY = date(2026, 6, 12)


def _a(
    *,
    title: str = "Practice packet",
    course: str = "AP Chemistry",
    due_offset: int | None = 2,
    kind: AssignmentKind = AssignmentKind.HOMEWORK,
    status: AssignmentStatus = AssignmentStatus.NOT_STARTED,
    points: float = 10.0,
    est_minutes: int = 45,
    course_max_points: float | None = None,
    aid: str = "a1",
) -> Assignment:
    due = None if due_offset is None else TODAY + timedelta(days=due_offset)
    return Assignment(
        id=aid,
        title=title,
        course=course,
        due_date=due,
        kind=kind,
        status=status,
        points_possible=points,
        est_minutes=est_minutes,
        source="manual",
        course_max_points=course_max_points,
    )


def _ctx(assignments: tuple[Assignment, ...] = (), course_max: dict[str, float] | None = None) -> PriorityContext:
    return PriorityContext.from_assignments(
        TODAY, assignments, course_max_points=course_max
    )


# ── Score bounds ────────────────────────────────────────────────────


class TestScoreBounds:
    def test_score_in_range(self) -> None:
        score = compute(_a(), _ctx())
        assert 0 <= score.score <= 100

    @pytest.mark.parametrize(
        "due_offset,kind,points",
        [
            (-5, AssignmentKind.EXAM, 100.0),     # extremely high
            (200, AssignmentKind.OTHER, 0.0),     # very low
            (None, AssignmentKind.OTHER, 0.0),    # no due date
        ],
    )
    def test_extremes_stay_clamped(self, due_offset: int | None, kind: AssignmentKind, points: float) -> None:
        score = compute(_a(due_offset=due_offset, kind=kind, points=points), _ctx())
        assert 0 <= score.score <= 100

    def test_deterministic_same_input_same_output(self) -> None:
        a = _a()
        ctx = _ctx()
        first = compute(a, ctx)
        second = compute(a, ctx)
        assert first == second


# ── Urgency monotonicity ────────────────────────────────────────────


class TestUrgencyMonotone:
    @pytest.mark.parametrize(
        "earlier,later",
        [(-1, 0), (0, 1), (1, 2), (2, 3), (3, 5), (5, 7), (7, 30)],
    )
    def test_earlier_due_means_higher_or_equal_urgency(self, earlier: int, later: int) -> None:
        # holding everything else constant, sooner-due should never score lower urgency
        e = compute(_a(due_offset=earlier), _ctx())
        l = compute(_a(due_offset=later), _ctx())
        e_urg = next(c.weight for c in e.rationale if c.key == "urgency")
        l_urg = next(c.weight for c in l.rationale if c.key == "urgency")
        assert e_urg >= l_urg


# ── Importance weighting ────────────────────────────────────────────


class TestImportance:
    @pytest.mark.parametrize(
        "kind,expected_weight",
        [
            (AssignmentKind.EXAM, 25),
            (AssignmentKind.PROJECT, 20),
            (AssignmentKind.TEST, 15),
            (AssignmentKind.LAB, 12),
            (AssignmentKind.HOMEWORK, 8),
            (AssignmentKind.CLASSWORK, 5),
            (AssignmentKind.OTHER, 4),
        ],
    )
    def test_importance_weights(self, kind: AssignmentKind, expected_weight: int) -> None:
        score = compute(_a(kind=kind), _ctx())
        chip = next(c for c in score.rationale if c.key == "importance")
        assert chip.weight == expected_weight


# ── Grade impact ────────────────────────────────────────────────────


class TestGradeImpact:
    def test_uses_course_max_when_present(self) -> None:
        # 50 of 500 → 10% → 2 points
        ctx = _ctx(course_max={"AP Chemistry": 500.0})
        s = compute(_a(points=50.0), ctx)
        chip = next(c for c in s.rationale if c.key == "grade_impact")
        assert chip.weight == 2

    def test_uses_assignment_course_max_first(self) -> None:
        # On-assignment course_max wins over the context's.
        ctx = _ctx(course_max={"AP Chemistry": 500.0})
        a = _a(points=100.0, course_max_points=200.0)
        chip = next(c for c in compute(a, ctx).rationale if c.key == "grade_impact")
        assert chip.weight == 10  # 100/200 = 50% → 10 of 20

    def test_falls_back_to_brackets(self) -> None:
        # No course max known anywhere
        s = compute(_a(points=100.0), _ctx())
        chip = next(c for c in s.rationale if c.key == "grade_impact")
        assert chip.weight == 18

    def test_unknown_points_get_baseline(self) -> None:
        chip = next(c for c in compute(_a(points=0.0), _ctx()).rationale if c.key == "grade_impact")
        assert chip.weight == 4

    def test_grade_impact_caps_at_20(self) -> None:
        # 2000 of 100 ≫ 100% — should clamp at 20
        ctx = _ctx(course_max={"AP Chemistry": 100.0})
        chip = next(c for c in compute(_a(points=2000.0), ctx).rationale if c.key == "grade_impact")
        assert chip.weight == 20


# ── Effort ──────────────────────────────────────────────────────────


class TestEffort:
    def test_short_and_imminent_quick_win(self) -> None:
        s = compute(_a(est_minutes=20, due_offset=1), _ctx())
        chip = next(c for c in s.rationale if c.key == "effort")
        assert chip.weight == 10

    def test_long_not_started_start_early(self) -> None:
        s = compute(_a(est_minutes=180, due_offset=5, status=AssignmentStatus.NOT_STARTED), _ctx())
        chip = next(c for c in s.rationale if c.key == "effort")
        assert chip.weight == 8

    def test_zero_estimate_baseline(self) -> None:
        chip = next(c for c in compute(_a(est_minutes=0), _ctx()).rationale if c.key == "effort")
        assert chip.weight == 3


# ── Dependency ──────────────────────────────────────────────────────


class TestDependency:
    def test_prep_for_upcoming_exam_gets_bonus(self) -> None:
        exam = _a(title="Unit 4 Test", kind=AssignmentKind.TEST, due_offset=3, aid="exam")
        prep = _a(title="Unit 4 review guide", kind=AssignmentKind.HOMEWORK, due_offset=1, aid="prep")
        ctx = _ctx(assignments=(exam, prep))
        chip = next((c for c in compute(prep, ctx).rationale if c.key == "dependency"), None)
        assert chip is not None and chip.weight == 5

    def test_no_exam_no_bonus(self) -> None:
        prep = _a(title="Some review", kind=AssignmentKind.HOMEWORK)
        score = compute(prep, _ctx(assignments=(prep,)))
        # dependency chip dropped when zero
        keys = {c.key for c in score.rationale}
        assert "dependency" not in keys

    def test_unrelated_homework_no_bonus(self) -> None:
        exam = _a(title="Unit 4 Test", kind=AssignmentKind.TEST, due_offset=3, aid="exam")
        unrelated = _a(title="Daily homework", kind=AssignmentKind.HOMEWORK, aid="hw")
        ctx = _ctx(assignments=(exam, unrelated))
        keys = {c.key for c in compute(unrelated, ctx).rationale}
        assert "dependency" not in keys

    def test_exam_itself_no_dependency_bonus(self) -> None:
        exam = _a(title="Unit 4 review test", kind=AssignmentKind.EXAM, due_offset=2, aid="e")
        keys = {c.key for c in compute(exam, _ctx(assignments=(exam,))).rationale}
        assert "dependency" not in keys


# ── Tier mapping & rank ─────────────────────────────────────────────


class TestRanking:
    def test_rank_orders_by_score_desc(self) -> None:
        big_exam = _a(title="Midterm", kind=AssignmentKind.EXAM, due_offset=1, points=100.0, aid="m")
        small_hw = _a(title="HW", kind=AssignmentKind.HOMEWORK, due_offset=6, points=5.0, aid="h")
        ctx = _ctx(assignments=(big_exam, small_hw))
        ordered = rank((small_hw, big_exam), ctx)
        assert ordered[0][0].id == "m"
        assert ordered[1][0].id == "h"

    def test_rank_tier_for_critical(self) -> None:
        # Overdue exam with big points should hit CRITICAL
        a = _a(kind=AssignmentKind.EXAM, due_offset=-1, points=100.0)
        s = compute(a, _ctx())
        assert s.tier is PriorityTier.CRITICAL

    def test_rank_tier_for_light(self) -> None:
        # Far-off untyped task with no points
        a = _a(kind=AssignmentKind.OTHER, due_offset=30, points=0.0, est_minutes=0)
        s = compute(a, _ctx())
        assert s.tier is PriorityTier.LIGHT


# ── Rationale completeness ──────────────────────────────────────────


class TestRationale:
    def test_always_has_core_components(self) -> None:
        chips = compute(_a(), _ctx()).rationale
        keys = {c.key for c in chips}
        # Dependency may legitimately drop when zero; the other four must always be present
        assert {"urgency", "importance", "grade_impact", "effort"} <= keys

    def test_rationale_sum_within_score(self) -> None:
        # The rendered score must match the sum of the chips that contributed.
        # (We drop only zero-weight dependency chips; everything else stays.)
        a = _a(due_offset=1, kind=AssignmentKind.EXAM, points=80.0, est_minutes=20)
        s = compute(a, _ctx())
        assert s.score == sum(c.weight for c in s.rationale)
