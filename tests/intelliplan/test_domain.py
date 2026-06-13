"""Tests for the domain dataclasses."""

from __future__ import annotations

from datetime import date

import pytest

from intelliplan.domain.assignment import (
    Assignment,
    AssignmentKind,
    AssignmentStatus,
)
from intelliplan.domain.plan import PriorityScore, PriorityTier, ReasonChip


def _make(due: date | None = None, status: AssignmentStatus = AssignmentStatus.NOT_STARTED) -> Assignment:
    return Assignment(
        id="a1",
        title="Practice packet",
        course="AP Chemistry",
        due_date=due,
        kind=AssignmentKind.HOMEWORK,
        status=status,
        points_possible=10.0,
        est_minutes=45,
        source="manual",
    )


class TestAssignment:
    def test_frozen(self) -> None:
        a = _make()
        with pytest.raises(Exception):
            a.title = "changed"  # type: ignore[misc]

    def test_days_until_due_future(self) -> None:
        a = _make(due=date(2026, 6, 15))
        assert a.days_until_due(date(2026, 6, 12)) == 3

    def test_days_until_due_overdue(self) -> None:
        a = _make(due=date(2026, 6, 10))
        assert a.days_until_due(date(2026, 6, 12)) == -2

    def test_days_until_due_no_date(self) -> None:
        assert _make(due=None).days_until_due(date(2026, 6, 12)) is None

    def test_is_overdue_false_for_completed(self) -> None:
        a = _make(due=date(2020, 1, 1), status=AssignmentStatus.COMPLETED)
        assert a.is_overdue is False

    def test_is_overdue_false_when_no_date(self) -> None:
        assert _make(due=None).is_overdue is False


class TestPriorityScore:
    @pytest.mark.parametrize(
        "score,expected",
        [
            (100, PriorityTier.CRITICAL),
            (85, PriorityTier.CRITICAL),
            (84, PriorityTier.FOCUS),
            (65, PriorityTier.FOCUS),
            (64, PriorityTier.STEADY),
            (40, PriorityTier.STEADY),
            (39, PriorityTier.LIGHT),
            (0, PriorityTier.LIGHT),
        ],
    )
    def test_tier_thresholds(self, score: int, expected: PriorityTier) -> None:
        assert PriorityScore.tier_for(score) is expected

    def test_rationale_is_immutable(self) -> None:
        chip = ReasonChip(key="urgency", weight=30, reason="Due tomorrow")
        ps = PriorityScore(score=92, tier=PriorityTier.CRITICAL, rationale=(chip,))
        with pytest.raises(Exception):
            ps.score = 50  # type: ignore[misc]
        assert ps.rationale[0].key == "urgency"
