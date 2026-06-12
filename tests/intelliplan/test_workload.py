"""Tests for the deterministic workload forecaster."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from intelliplan.domain.assignment import (
    Assignment,
    AssignmentKind,
    AssignmentStatus,
)
from intelliplan.intelligence import workload
from intelliplan.intelligence.workload import (
    DEFAULT_DAILY_AVAILABLE_MIN,
    FORECAST_DAYS,
    forecast,
)


TODAY = date(2026, 6, 12)  # a Friday


def _a(
    *,
    due_offset: int | None = 2,
    est_minutes: int = 60,
    status: AssignmentStatus = AssignmentStatus.NOT_STARTED,
    aid: str = "a1",
) -> Assignment:
    due = None if due_offset is None else TODAY + timedelta(days=due_offset)
    return Assignment(
        id=aid,
        title="Task",
        course="X",
        due_date=due,
        kind=AssignmentKind.HOMEWORK,
        status=status,
        points_possible=0.0,
        est_minutes=est_minutes,
        source="manual",
    )


class TestShape:
    def test_forecast_has_seven_days(self) -> None:
        f = forecast([], None, TODAY)
        assert len(f.days) == FORECAST_DAYS

    def test_first_day_is_today(self) -> None:
        f = forecast([], None, TODAY)
        assert f.days[0].day == TODAY

    def test_default_availability_applies_when_none(self) -> None:
        f = forecast([], None, TODAY)
        for d in f.days:
            assert d.available_minutes == DEFAULT_DAILY_AVAILABLE_MIN


class TestCommitted:
    def test_committed_lands_on_due_date(self) -> None:
        f = forecast([_a(due_offset=2, est_minutes=90)], None, TODAY)
        assert f.days[2].committed_minutes == 90

    def test_completed_excluded(self) -> None:
        a = _a(due_offset=2, est_minutes=90, status=AssignmentStatus.COMPLETED)
        f = forecast([a], None, TODAY)
        assert f.days[2].committed_minutes == 0

    def test_no_due_date_no_committed_load(self) -> None:
        f = forecast([_a(due_offset=None, est_minutes=999)], None, TODAY)
        for d in f.days:
            assert d.committed_minutes == 0

    def test_out_of_horizon_ignored(self) -> None:
        f = forecast([_a(due_offset=30, est_minutes=999)], None, TODAY)
        for d in f.days:
            assert d.committed_minutes == 0
            assert d.prework_minutes == 0


class TestPrework:
    def test_prework_distributes_backwards(self) -> None:
        # 90-min task due in 3 days, default 120/day available.
        # Should land in slot day=2 (one day before due).
        f = forecast([_a(due_offset=3, est_minutes=90)], None, TODAY)
        assert f.days[2].prework_minutes == 90
        # And not on the due day itself — that's the committed pool.
        assert f.days[3].committed_minutes == 90
        assert f.days[3].prework_minutes == 0

    def test_prework_overflows_to_earlier_days(self) -> None:
        # 250-min task due in 3 days, 120/day available.
        # Day 2 takes 120, day 1 takes 120, day 0 takes 10.
        f = forecast([_a(due_offset=3, est_minutes=250)], None, TODAY)
        assert f.days[2].prework_minutes == 120
        assert f.days[1].prework_minutes == 120
        assert f.days[0].prework_minutes == 10

    def test_due_today_no_prework(self) -> None:
        f = forecast([_a(due_offset=0, est_minutes=60)], None, TODAY)
        assert f.days[0].committed_minutes == 60
        for d in f.days:
            assert d.prework_minutes == 0


class TestAvailability:
    def test_per_weekday_availability_honored(self) -> None:
        # TODAY is Friday — give Monday 0 min available, everything else default.
        avail = {"monday": 0}
        f = forecast([], avail, TODAY)
        monday_day = next((d for d in f.days if d.day.weekday() == 0), None)
        assert monday_day is not None
        assert monday_day.available_minutes == 0

    def test_invalid_availability_uses_default(self) -> None:
        f = forecast([], {"not-a-day": 999, "monday": "bad"}, TODAY)
        for d in f.days:
            assert d.available_minutes == DEFAULT_DAILY_AVAILABLE_MIN

    def test_zero_availability_means_stress_only_when_load(self) -> None:
        f = forecast([], {day: 0 for day in (
            "monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday",
        )}, TODAY)
        for d in f.days:
            assert d.stress == 0.0


class TestStress:
    def test_zero_when_empty(self) -> None:
        f = forecast([], None, TODAY)
        for d in f.days:
            assert d.stress == 0.0

    def test_in_zero_to_one(self) -> None:
        f = forecast(
            [_a(due_offset=i, est_minutes=180) for i in range(FORECAST_DAYS)],
            None,
            TODAY,
        )
        for d in f.days:
            assert 0.0 <= d.stress <= 1.0

    def test_stress_one_when_overbooked(self) -> None:
        # 600 min on day 0 with 120 min available
        f = forecast([_a(due_offset=0, est_minutes=600)], None, TODAY)
        assert f.days[0].stress == 1.0

    def test_partial_when_underloaded(self) -> None:
        # 60 min of 120 available → 0.5
        f = forecast([_a(due_offset=0, est_minutes=60)], None, TODAY)
        assert f.days[0].stress == pytest.approx(0.5)


class TestSummary:
    def test_empty_week_summary(self) -> None:
        f = forecast([], None, TODAY)
        assert "clear" in f.summary.lower()
        assert f.heaviest_day is None

    def test_heaviest_day_named(self) -> None:
        a = _a(due_offset=3, est_minutes=400)
        f = forecast([a], None, TODAY)
        assert f.heaviest_day == TODAY + timedelta(days=3)
        assert (TODAY + timedelta(days=3)).strftime("%A") in f.summary
