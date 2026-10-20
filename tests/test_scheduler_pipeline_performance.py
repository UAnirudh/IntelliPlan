"""Deterministic work-count regressions for scheduler hot paths."""
from datetime import date, datetime
from types import SimpleNamespace

import scheduler_engine
from intelliplan.intelligence.calendar_sittings import calendar_sittings
from intelliplan.intelligence.planner import DayPlan, Plan, Session, _ready_work_order
from intelliplan.services.scheduling import SchedulingService, StudentContext

DAY = date(2026, 10, 19)


def test_already_fitting_day_uses_one_clock_replay(monkeypatch):
    sessions = tuple(Session(task_id=str(i), title=str(i), course='Math', kind='homework',
                             day=DAY, minutes=15, part_index=1, part_total=1,
                             difficulty='medium', priority=50, due_date=DAY)
                     for i in range(20))
    plan = Plan(days=(DayPlan(DAY, sessions, 600, 300),), total_minutes=300)
    calls = []
    original = scheduler_engine.place_day_blocks

    def counted(*args, **kwargs):
        calls.append(len(args[0]))
        return original(*args, **kwargs)

    monkeypatch.setattr(scheduler_engine, 'place_day_blocks', counted)
    result = calendar_sittings(plan, {DAY: [scheduler_engine.Window(
        datetime(2026, 10, 19, 8), datetime(2026, 10, 19, 18))]}, scheduler_engine.StudyDNA())
    assert result[DAY] == list(sessions)
    assert calls == [20]


def test_deadline_window_queries_are_once_per_day_and_never_cross_calls(monkeypatch):
    from intelliplan.intelligence.planner import PlannerTask

    scheduler = SchedulingService(StudentContext())
    calls = []

    def windows(day, now):
        calls.append(day)
        return [scheduler_engine.Window(datetime.combine(day, datetime.min.time()).replace(hour=8),
                                        datetime.combine(day, datetime.min.time()).replace(hour=20))]

    monkeypatch.setattr(scheduler, 'windows_for', windows)
    tasks = [PlannerTask(id=str(i), title=str(i), est_minutes=15,
                         deadline_at=datetime(2026, 10, 19 + i % 2, 18)) for i in range(60)]
    first = scheduler._deadline_capacities(tasks, datetime(2026, 10, 19, 7))
    assert len(calls) == 2
    assert all(task.deadline_day_capacity == 600 for task in first)
    scheduler._deadline_capacities(tasks, datetime(2026, 10, 19, 7))
    assert len(calls) == 4


def test_dependency_order_waits_for_every_parent_sitting_and_ignores_absent_parents():
    rows = [SimpleNamespace(task_id='child', depends_on=('parent', 'absent'), order=0),
            SimpleNamespace(task_id='parent', depends_on=(), order=2),
            SimpleNamespace(task_id='parent', depends_on=(), order=3),
            SimpleNamespace(task_id='other', depends_on=(), order=1)]
    assert [row.task_id for row in _ready_work_order(rows, lambda row: row.order)] == [
        'other', 'parent', 'parent', 'child']


def test_time_labels_cover_midnight_noon_and_single_digit_hours():
    assert [scheduler_engine._fmt12(datetime(2026, 10, 19, hour, minute))
            for hour, minute in ((0, 0), (9, 5), (12, 0), (23, 59))] == [
        '12:00 AM', '9:05 AM', '12:00 PM', '11:59 PM']
