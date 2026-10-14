"""Feasibility verification over a finished plan."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from intelliplan.intelligence.constraints import Severity, check_schedule

DAY = date(2026, 8, 21)


def at(hour: int, minute: int = 0) -> str:
    return datetime(2026, 8, 21, hour, minute).isoformat()


def blk(block_id, start_h, end_h, *, due=None, is_break=False, title="Physics"):
    return {
        "id": block_id,
        "assignment": title,
        "start_iso": at(start_h),
        "end_iso": at(end_h),
        "duration_minutes": int((end_h - start_h) * 60),
        "is_break": is_break,
        "due_date": due,
    }


def sched(blocks, *, capacity=600, day=DAY):
    return {
        "schedule": [{
            "date": day.isoformat(),
            "blocks": blocks,
            "capacity_minutes": capacity,
        }]
    }


@dataclass(frozen=True)
class FakeWindow:
    start: datetime
    end: datetime


def keys(report):
    return {v.key for v in report.violations}


# ── the happy path ───────────────────────────────────────────────────


def test_a_clean_plan_is_feasible():
    report = check_schedule(sched([blk("a", 9, 10), blk("b", 11, 12)]))
    assert report.feasible
    assert report.violations == ()
    assert report.blocks_checked == 2


def test_an_empty_plan_is_feasible_rather_than_an_error():
    assert check_schedule({}).feasible
    assert check_schedule(None).feasible


# ── hard violations ──────────────────────────────────────────────────


def test_overlapping_blocks_are_infeasible():
    report = check_schedule(sched([blk("a", 9, 11), blk("b", 10, 12)]))
    assert not report.feasible
    assert "overlap" in keys(report)
    assert report.hard[0].block_ids == ("a", "b")


def test_touching_blocks_are_not_an_overlap():
    """9-10 followed by 10-11 is back-to-back, not a conflict."""
    assert check_schedule(sched([blk("a", 9, 10), blk("b", 10, 11)])).feasible


def test_work_scheduled_after_its_own_deadline_is_infeasible():
    report = check_schedule(sched([
        blk("a", 9, 10, due=(DAY - timedelta(days=1)).isoformat())
    ]))
    assert not report.feasible
    assert "after_deadline" in keys(report)


def test_work_on_the_due_date_itself_is_allowed():
    assert check_schedule(sched([blk("a", 9, 10, due=DAY.isoformat())])).feasible


def test_explicit_catchup_is_feasible_but_keeps_original_late_deadline():
    data = sched([blk('late', 9, 10, due=(DAY - timedelta(days=1)).isoformat())])
    report = check_schedule(data, now=datetime.combine(DAY, datetime.min.time()),
                            allow_overdue_recovery=True)
    assert report.feasible
    assert keys(report) == {'overdue_recovery'}
    assert report.soft[0].block_ids == ('late',)


def test_catchup_permission_never_excuses_missing_a_future_deadline():
    report = check_schedule(
        sched([blk('future', 9, 10, due=(DAY - timedelta(days=1)).isoformat())]),
        now=datetime.combine(DAY - timedelta(days=2), datetime.min.time()),
        allow_overdue_recovery=True,
    )
    assert not report.feasible
    assert 'after_deadline' in keys(report)


def test_study_booked_over_a_calendar_commitment_is_infeasible():
    report = check_schedule(
        sched([blk("a", 9, 11)]),
        busy_by_date={DAY: [(600, 660)]},  # 10:00–11:00
    )
    assert not report.feasible
    assert "calendar_conflict" in keys(report)


def test_a_commitment_that_does_not_overlap_is_not_a_conflict():
    report = check_schedule(
        sched([blk("a", 9, 10)]), busy_by_date={DAY: [(660, 720)]}
    )
    assert report.feasible


def test_zero_length_calendar_period_cannot_create_a_false_conflict():
    assert check_schedule(sched([blk('a', 9, 11)]),
                          busy_by_date={DAY: [(600, 600), (660, 600)]}).feasible


def test_work_outside_stated_availability_is_infeasible():
    report = check_schedule(
        sched([blk("a", 5, 6)]),
        windows_for=lambda d: [FakeWindow(datetime(2026, 8, 21, 17),
                                          datetime(2026, 8, 21, 21))],
    )
    assert not report.feasible
    assert "outside_availability" in keys(report)


def test_a_block_filed_under_the_wrong_date_is_caught():
    data = {"schedule": [{
        "date": (DAY + timedelta(days=1)).isoformat(),
        "blocks": [blk("a", 9, 10)],
        "capacity_minutes": 600,
    }]}
    report = check_schedule(data)
    assert not report.feasible
    assert "date_mismatch" in keys(report)


def test_a_zero_length_block_is_infeasible():
    report = check_schedule(sched([blk("a", 9, 9)]))
    assert not report.feasible
    assert "zero_duration" in keys(report)


# ── soft violations ──────────────────────────────────────────────────


def test_an_overloaded_day_is_reported_but_still_executable():
    report = check_schedule(sched([blk("a", 9, 14)], capacity=120))
    assert report.feasible
    assert "over_capacity" in keys(report)
    assert all(v.severity == Severity.SOFT for v in report.violations)


def test_hours_of_unbroken_work_is_reported():
    report = check_schedule(sched([blk("a", 9, 13), blk("b", 13, 14)]))
    assert report.feasible
    assert "no_break" in keys(report)


def test_a_scheduled_break_resets_the_run():
    report = check_schedule(sched([
        blk("a", 9, 12), blk("b", 12, 13, is_break=True), blk("c", 13, 15),
    ]))
    assert "no_break" not in keys(report)


def test_a_block_in_the_past_is_noted_without_condemning_the_plan():
    report = check_schedule(
        sched([blk("a", 9, 10)]), now=datetime(2026, 8, 21, 20, 0)
    )
    assert report.feasible
    assert "in_the_past" in keys(report)


def test_a_two_minute_block_is_soft_not_hard():
    tiny = {**blk("a", 9, 9), "end_iso": at(9, 2), "duration_minutes": 2}
    report = check_schedule(sched([tiny]))
    assert report.feasible
    assert "too_short" in keys(report)


# ── robustness ───────────────────────────────────────────────────────


def test_unplaced_blocks_are_skipped_rather_than_invented_into_conflicts():
    unplaced = {"id": "x", "assignment": "Essay", "duration_minutes": 60}
    report = check_schedule(sched([blk("a", 9, 10), unplaced]))
    assert report.feasible
    assert report.blocks_checked == 1


def test_a_failing_availability_provider_cannot_certify_a_plan():
    def boom(day):
        raise RuntimeError("availability service is down")

    report = check_schedule(sched([blk('a', 9, 10)]), windows_for=boom)
    assert not report.feasible
    assert 'availability_unverified' in keys(report)


def test_no_free_windows_rejects_placed_work():
    assert not check_schedule(sched([blk('a', 9, 10)]), windows_for=lambda day: []).feasible


def test_the_report_serialises_for_logging():
    payload = check_schedule(sched([blk("a", 9, 11), blk("b", 10, 12)])).to_dict()
    assert payload["feasible"] is False
    assert payload["violations"][0]["key"] == "overlap"
    assert payload["violations"][0]["day"] == DAY.isoformat()
