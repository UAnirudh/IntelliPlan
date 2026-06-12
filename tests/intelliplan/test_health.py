"""Tests for the Academic Health Score engine."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from intelliplan.domain.assignment import (
    Assignment,
    AssignmentKind,
    AssignmentStatus,
)
from intelliplan.intelligence import health
from intelliplan.intelligence.health import CourseGrade, HealthSnapshot, compute


TODAY = date(2026, 6, 12)


def _a(
    *,
    due_offset: int | None = 2,
    kind: AssignmentKind = AssignmentKind.HOMEWORK,
    status: AssignmentStatus = AssignmentStatus.NOT_STARTED,
    aid: str = "a1",
    title: str = "task",
    course: str = "X",
) -> Assignment:
    due = None if due_offset is None else TODAY + timedelta(days=due_offset)
    return Assignment(
        id=aid,
        title=title,
        course=course,
        due_date=due,
        kind=kind,
        status=status,
        points_possible=0.0,
        est_minutes=30,
        source="manual",
    )


# ── Baseline & bounds ───────────────────────────────────────────────


class TestBounds:
    def test_perfect_picture_returns_high_score(self) -> None:
        snap = HealthSnapshot(
            assignments=(),
            grades=(CourseGrade("Math", 95.0, (95.0, 94.0, 93.0)),),
            completion_rate_7d=0.9,
            schedule_balance_index=0.7,
            today=TODAY,
        )
        score = compute(snap)
        assert score.score == 100
        assert score.tier == "thriving"

    def test_score_clamped_and_caps_enforced(self) -> None:
        # Caps prevent any single dimension from tanking the score on its
        # own. With every dimension maxed out, total penalty is
        # 32 + 16 + 24 + 12 = 84, so score floors at 16, not 0. That's
        # the engine contract — caps work, clamping holds.
        overdue = tuple(
            _a(due_offset=-1, aid=f"o{i}", title=f"o{i}") for i in range(5)
        )
        high_stakes = tuple(
            _a(due_offset=1, kind=AssignmentKind.EXAM, aid=f"h{i}", title=f"h{i}")
            for i in range(4)
        )
        grades = tuple(
            CourseGrade(f"C{i}", 50.0, (50.0, 60.0, 70.0)) for i in range(4)
        )
        snap = HealthSnapshot(
            assignments=overdue + high_stakes,
            grades=grades,
            completion_rate_7d=0.0,
            schedule_balance_index=0.0,
            today=TODAY,
        )
        score = compute(snap)
        assert 0 <= score.score <= 100
        assert score.score == 16
        assert score.tier == "at_risk"
        # Each penalty component should be at its cap.
        by_key = {c.key: c.impact for c in score.components}
        assert by_key["overdue"] == -32
        assert by_key["high_stakes_soon"] == -16
        assert by_key["failing_courses"] == -24
        assert by_key["declining_courses"] == -12

    def test_overdue_penalty_capped(self) -> None:
        # 100 overdue items shouldn't subtract 800 — cap at 32.
        many = tuple(
            _a(due_offset=-1, aid=f"o{i}", title=f"o{i}") for i in range(100)
        )
        snap = HealthSnapshot(assignments=many, grades=(), today=TODAY)
        score = compute(snap)
        overdue_chip = next(c for c in score.components if c.key == "overdue")
        assert overdue_chip.impact == -32


# ── Component detection ────────────────────────────────────────────


class TestComponents:
    def test_overdue_detected(self) -> None:
        snap = HealthSnapshot(
            assignments=(_a(due_offset=-2),), grades=(), today=TODAY,
        )
        chip = next(c for c in compute(snap).components if c.key == "overdue")
        assert chip.value == 1.0
        assert chip.impact == -8

    def test_completed_not_counted_as_overdue(self) -> None:
        snap = HealthSnapshot(
            assignments=(_a(due_offset=-2, status=AssignmentStatus.COMPLETED),),
            grades=(), today=TODAY,
        )
        keys = {c.key for c in compute(snap).components}
        assert "overdue" not in keys

    def test_high_stakes_soon(self) -> None:
        exam = _a(due_offset=2, kind=AssignmentKind.EXAM)
        snap = HealthSnapshot(assignments=(exam,), grades=(), today=TODAY)
        chip = next(c for c in compute(snap).components if c.key == "high_stakes_soon")
        assert chip.impact == -4

    def test_high_stakes_only_when_not_started(self) -> None:
        exam = _a(due_offset=2, kind=AssignmentKind.EXAM, status=AssignmentStatus.IN_PROGRESS)
        snap = HealthSnapshot(assignments=(exam,), grades=(), today=TODAY)
        keys = {c.key for c in compute(snap).components}
        assert "high_stakes_soon" not in keys

    def test_failing_course_detected(self) -> None:
        snap = HealthSnapshot(
            grades=(CourseGrade("Bio", 65.0, ()),), today=TODAY,
        )
        chip = next(c for c in compute(snap).components if c.key == "failing_courses")
        assert chip.impact == -6
        assert "Bio" in chip.reason

    def test_declining_course_requires_three_points(self) -> None:
        snap = HealthSnapshot(
            grades=(CourseGrade("Bio", 75.0, (75.0, 85.0)),), today=TODAY,
        )
        keys = {c.key for c in compute(snap).components}
        assert "declining_courses" not in keys

    def test_declining_course_when_trend_down(self) -> None:
        snap = HealthSnapshot(
            grades=(CourseGrade("Bio", 75.0, (75.0, 80.0, 85.0)),), today=TODAY,
        )
        chip = next(c for c in compute(snap).components if c.key == "declining_courses")
        assert chip.impact == -4
        assert "Bio" in chip.reason

    def test_completion_bonus_at_threshold(self) -> None:
        snap = HealthSnapshot(
            completion_rate_7d=0.80, today=TODAY,
        )
        chip = next(c for c in compute(snap).components if c.key == "completion_7d")
        assert chip.impact == 5

    def test_completion_under_threshold_no_bonus(self) -> None:
        snap = HealthSnapshot(completion_rate_7d=0.5, today=TODAY)
        chip = next(c for c in compute(snap).components if c.key == "completion_7d")
        assert chip.impact == 0

    def test_balance_bonus_at_threshold(self) -> None:
        snap = HealthSnapshot(schedule_balance_index=0.6, today=TODAY)
        chip = next(c for c in compute(snap).components if c.key == "schedule_balance")
        assert chip.impact == 5


# ── Delta tracking ─────────────────────────────────────────────────


class TestDelta:
    def test_delta_zero_when_no_previous(self) -> None:
        snap = HealthSnapshot(today=TODAY)
        score = compute(snap)
        assert score.delta_vs_yesterday == 0

    def test_delta_vs_previous(self) -> None:
        snap = HealthSnapshot(
            assignments=(_a(due_offset=-1),), grades=(), today=TODAY,
        )
        first = compute(snap)
        # Add another overdue
        snap2 = HealthSnapshot(
            assignments=(_a(due_offset=-1, aid="o1"), _a(due_offset=-1, aid="o2")),
            grades=(), today=TODAY,
        )
        second = compute(snap2, previous=first)
        assert second.delta_vs_yesterday == second.score - first.score
        assert second.delta_vs_yesterday < 0


# ── Tier mapping ───────────────────────────────────────────────────


class TestTier:
    @pytest.mark.parametrize(
        "score,expected",
        [
            (100, "thriving"),
            (85, "thriving"),
            (84, "steady"),
            (70, "steady"),
            (69, "watch"),
            (50, "watch"),
            (49, "at_risk"),
            (0, "at_risk"),
        ],
    )
    def test_tier_thresholds(self, score: int, expected: str) -> None:
        # Construct a snapshot that lands on the target score via overdue
        # items + course penalties — used here to drive the public API.
        # Simpler: call _tier_for directly.
        assert health._tier_for(score) == expected


# ── Summary text ───────────────────────────────────────────────────


class TestSummary:
    def test_clean_picture_summary_positive(self) -> None:
        snap = HealthSnapshot(today=TODAY)
        score = compute(snap)
        assert "solid" in score.summary.lower() or "steady" in score.summary.lower()

    def test_summary_mentions_biggest_negative(self) -> None:
        snap = HealthSnapshot(
            assignments=(
                _a(due_offset=-1, aid="o1"),
                _a(due_offset=-1, aid="o2"),
                _a(due_offset=2, kind=AssignmentKind.EXAM),
            ),
            today=TODAY,
        )
        score = compute(snap)
        # overdue (-16) is bigger than high_stakes (-4) — summary should reflect overdue
        assert "overdue" in score.summary.lower()
