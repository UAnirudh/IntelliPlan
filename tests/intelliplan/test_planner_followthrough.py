"""The planner's new inputs: follow-through, anchors, pins, pushes, hints.

Every input is optional, and with none of them the planner must behave
exactly as it always has — that is tested too.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

from intelliplan.intelligence.estimation import build_estimation_model
from intelliplan.intelligence.planner import (
    DayCapacity,
    PlannerTask,
    buffer_days_for,
    build_plan,
    capacities_from_minutes,
    PlannerConfig,
)

TODAY = date(2026, 3, 2)  # Monday
DUE = TODAY + timedelta(days=10)


def six():
    return [
        PlannerTask(id=f"t{i}", title=f"T{i}", course="c", due_date=DUE, est_minutes=60, priority=60)
        for i in range(6)
    ]


CAPS = capacities_from_minutes(TODAY, [120] * 14)


def days_of(plan, task_id):
    return sorted(s.day for s in plan.sessions if s.task_id == task_id)


def test_with_no_new_inputs_nothing_changes():
    a = build_plan(six(), CAPS, today=TODAY)
    b = build_plan(six(), CAPS, today=TODAY, completion=None, anchors=None)
    assert a.sessions == b.sessions


def test_work_drifts_off_the_day_the_student_never_follows_through_on():
    fridays_fail = lambda task, minutes, day, load: 0.15 if day.weekday() == 4 else 0.85
    plan = build_plan(six(), CAPS, today=TODAY, completion=fridays_fail)
    assert not [s for s in plan.sessions if s.day.weekday() == 4]


def test_a_full_day_is_less_attractive_when_fatigue_is_learned():
    tired = lambda task, minutes, day, load: max(0.1, 0.9 - load / 200)
    plan = build_plan(six(), CAPS, today=TODAY, completion=tired)
    assert max(d.scheduled_minutes for d in plan.days) <= 120


def test_anchors_keep_untouched_work_where_it_was():
    base = build_plan(six(), CAPS, today=TODAY)
    anchors = {}
    for s in base.sessions:
        anchors.setdefault(s.task_id, []).append(s.day)
    lost = base.sessions[0].day
    caps = [c if c.day != lost else replace(c, minutes=0) for c in CAPS]
    repaired = build_plan(six(), caps, today=TODAY, anchors=anchors)
    rebuilt = build_plan(six(), caps, today=TODAY)

    def moved(plan):
        old = {(s.task_id, s.day) for s in base.sessions}
        return sum(1 for s in plan.sessions if (s.task_id, s.day) not in old)

    assert moved(repaired) <= moved(rebuilt)
    assert moved(repaired) <= sum(1 for s in base.sessions if s.day == lost) + 1


def test_an_exact_sitting_hint_reproduces_the_plan():
    base = build_plan(six(), CAPS, today=TODAY)
    hinted = [
        replace(t, sittings=tuple((s.day, s.minutes) for s in base.sessions if s.task_id == t.id),
                est_minutes=sum(s.minutes for s in base.sessions if s.task_id == t.id),
                calibrated=True)
        for t in six()
    ]
    again = build_plan(hinted, CAPS, today=TODAY)
    assert {(s.task_id, s.day, s.minutes) for s in again.sessions} == {
        (s.task_id, s.day, s.minutes) for s in base.sessions
    }


def test_a_displaced_sitting_keeps_its_size():
    task = PlannerTask(id="a", title="A", due_date=DUE, est_minutes=70, calibrated=True,
                       sittings=((None, 70),))
    plan = build_plan([task], CAPS, today=TODAY)
    assert [s.minutes for s in plan.sessions] == [70]


def test_a_pinned_sitting_never_moves_even_past_a_days_capacity():
    pin_day = TODAY + timedelta(days=3)
    tasks = six()
    tasks[0] = replace(tasks[0], pinned=((pin_day, 60),))
    caps = [c if c.day != pin_day else replace(c, minutes=30) for c in CAPS]
    plan = build_plan(tasks, caps, today=TODAY)
    pinned = [s for s in plan.sessions if s.task_id == "t0"]
    assert [s.day for s in pinned] == [pin_day]
    assert "You placed this here" in pinned[0].reasons[0]


def test_triage_never_evicts_a_pinned_sitting():
    pin_day = TODAY + timedelta(days=1)
    low = PlannerTask(id="low", title="Low", due_date=TODAY + timedelta(days=2), est_minutes=60,
                      priority=5, pinned=((pin_day, 60),))
    urgent = [
        PlannerTask(id=f"u{i}", title="U", due_date=TODAY + timedelta(days=2), est_minutes=100, priority=95)
        for i in range(3)
    ]
    caps = capacities_from_minutes(TODAY, [100] * 5)
    plan = build_plan([low, *urgent], caps, today=TODAY)
    assert days_of(plan, "low") == [pin_day]


def test_not_today_means_not_before_the_day_given():
    tasks = six()
    tasks[0] = replace(tasks[0], not_before=TODAY + timedelta(days=4))
    plan = build_plan(tasks, CAPS, today=TODAY)
    assert all(d >= TODAY + timedelta(days=4) for d in days_of(plan, "t0"))


def test_pushing_past_the_deadline_is_reported_not_hidden():
    task = PlannerTask(id="x", title="X", due_date=TODAY + timedelta(days=2), est_minutes=60,
                       not_before=TODAY + timedelta(days=5))
    plan = build_plan([task], CAPS, today=TODAY)
    assert not plan.sessions
    assert [d.task_id for d in plan.deferred] == ["x"]


def test_calibrated_minutes_are_not_corrected_twice():
    now = datetime(2026, 3, 2, 8)
    slow = build_estimation_model(
        feedback_rows=[{"estimated": 60, "actual": 120, "at": now - timedelta(days=i)} for i in range(20)],
        now=now,
    )
    raw = PlannerTask(id="r", title="R", due_date=DUE, est_minutes=60)
    fresh = build_plan([raw], CAPS, model=slow, today=TODAY)
    again = build_plan([replace(raw, calibrated=True)], CAPS, model=slow, today=TODAY)
    assert sum(s.minutes for s in fresh.sessions) > 100
    assert sum(s.minutes for s in again.sessions) == 60


def test_extra_buffer_days_finish_work_earlier():
    task = PlannerTask(id="e", title="E", due_date=DUE, est_minutes=60, priority=50)
    from intelliplan.intelligence.estimation import EstimationModel

    est = EstimationModel().predict(60)
    base = buffer_days_for(task, est, PlannerConfig())
    assert buffer_days_for(replace(task, extra_buffer_days=2), est, PlannerConfig()) == base + 2
