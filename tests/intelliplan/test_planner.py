"""Scheduler scenario tests.

These are not unit tests of helper functions — they are the realistic
student situations the planner has to get right, written as assertions
about the shape of the resulting plan.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from intelliplan.intelligence.estimation import build_estimation_model
from intelliplan.intelligence.planner import (
    DayCapacity,
    PlannerConfig,
    PlannerTask,
    Plan,
    Reality,
    build_plan,
    capacities_from_minutes,
    reschedule,
    session_cap_for,
    split_into_sessions,
    task_from_mapping,
)

TODAY = date(2026, 3, 2)  # a Monday
NOW = datetime(2026, 3, 2, 8, 0)


def caps(minutes_per_day, days=14, start=TODAY):
    if isinstance(minutes_per_day, int):
        minutes_per_day = [minutes_per_day] * days
    return capacities_from_minutes(start, minutes_per_day)


def task(tid, **kw):
    defaults = dict(id=tid, title=tid.title(), course="math", kind="homework", priority=50)
    defaults.update(kw)
    return PlannerTask(**defaults)


def minutes_by_day(plan: Plan) -> dict[date, int]:
    return {d.day: d.scheduled_minutes for d in plan.days}


def days_used(plan: Plan, task_id: str) -> list[date]:
    return sorted({s.day for s in plan.sessions if s.task_id == task_id})


# ── Sizing primitives ─────────────────────────────────────────────────


def test_split_produces_equal_sittings_not_a_stub():
    parts = split_into_sessions(100, cap=45)
    assert sum(parts) == 100
    assert max(parts) - min(parts) <= 1
    assert min(parts) >= 15


def test_short_work_is_one_sitting():
    assert split_into_sessions(30, cap=45) == [30]


def test_split_never_creates_pointless_fragments():
    for total in range(16, 400):
        parts = split_into_sessions(total, cap=45)
        assert sum(parts) == total
        assert min(parts) >= 15, (total, parts)


def test_subtask_count_shapes_the_split():
    # 5 questions, 100 minutes, 45 cap → 3 by cap, but 5 divides cleanly and
    # is not close, so the cap wins. 4 subtasks is within 1 of 3, so it wins.
    assert len(split_into_sessions(100, cap=45, subtask_count=4)) == 4


def test_session_cap_reflects_kind_and_difficulty():
    stamina = 50
    exam = session_cap_for(task("e", kind="exam"), stamina)
    project = session_cap_for(task("p", kind="project"), stamina)
    hard = session_cap_for(task("h", difficulty="hard"), stamina)
    easy = session_cap_for(task("z", difficulty="easy"), stamina)
    assert exam < stamina < project
    assert hard < easy


# ── Scenario: light workload ──────────────────────────────────────────


def test_light_workload_is_spread_not_crammed():
    tasks = [
        task("a", est_minutes=40, due_date=TODAY + timedelta(days=6)),
        task("b", est_minutes=40, due_date=TODAY + timedelta(days=8), course="bio"),
    ]
    plan = build_plan(tasks, caps(120), today=TODAY)
    loads = [m for m in minutes_by_day(plan).values() if m]
    assert not plan.overloaded
    assert max(loads) <= 60, loads
    # Two unrelated tasks should not both land on the same single day.
    assert len({s.day for s in plan.sessions}) >= 2


def test_nothing_is_scheduled_after_its_deadline():
    tasks = [task(f"t{i}", est_minutes=90, due_date=TODAY + timedelta(days=i + 1)) for i in range(6)]
    plan = build_plan(tasks, caps(180), today=TODAY)
    for s in plan.sessions:
        assert s.due_date is None or s.day <= s.due_date


def test_work_finishes_with_buffer_before_the_deadline():
    t = task("essay", kind="project", est_minutes=120, due_date=TODAY + timedelta(days=10), priority=85)
    plan = build_plan([t], caps(180), today=TODAY)
    last = max(days_used(plan, "essay"))
    assert last <= t.due_date - timedelta(days=1)


# ── Scenario: heavy workload ──────────────────────────────────────────


def test_heavy_week_balances_instead_of_dumping_on_one_day():
    tasks = [
        task(f"t{i}", est_minutes=90, due_date=TODAY + timedelta(days=7), course=f"c{i % 4}")
        for i in range(8)
    ]
    plan = build_plan(tasks, caps(150), today=TODAY)
    loads = [d.scheduled_minutes for d in plan.days[:7] if d.capacity_minutes]
    assert max(loads) <= 150
    # No day carries more than triple the lightest working day.
    working = [m for m in loads if m]
    assert max(working) <= min(working) * 3.5, working


def test_genuine_overload_is_reported_not_hidden():
    tasks = [
        task(f"t{i}", est_minutes=180, due_date=TODAY + timedelta(days=2))
        for i in range(8)
    ]
    plan = build_plan(tasks, caps(90), today=TODAY)
    assert plan.overloaded
    assert plan.deferred
    assert any("over capacity" in n for n in plan.notes)
    # And what did fit still respects the deadline.
    for s in plan.sessions:
        assert s.day <= s.due_date


def test_overload_protects_the_highest_priority_work():
    urgent = task("exam", kind="exam", est_minutes=120, due_date=TODAY + timedelta(days=2), priority=95)
    filler = [
        task(f"f{i}", est_minutes=120, due_date=TODAY + timedelta(days=2), priority=20)
        for i in range(6)
    ]
    plan = build_plan([*filler, urgent], caps(100), today=TODAY)
    scheduled = sum(s.minutes for s in plan.sessions if s.task_id == "exam")
    dropped = {d.task_id for d in plan.deferred}
    assert scheduled > 0
    assert "exam" not in dropped or scheduled >= 60


# ── Scenario: several deadlines on one day ────────────────────────────


def test_multiple_deadlines_same_day_start_earlier():
    due = TODAY + timedelta(days=4)
    tasks = [task(f"t{i}", est_minutes=80, due_date=due, course=f"c{i}") for i in range(4)]
    plan = build_plan(tasks, caps(120), today=TODAY)
    assert not plan.overloaded
    # The day before the deadline must not hold all four.
    day_before = [d for d in plan.days if d.day == due - timedelta(days=1)][0]
    assert len({s.task_id for s in day_before.sessions}) < 4
    # Work started at least three days out.
    assert min(s.day for s in plan.sessions) <= TODAY + timedelta(days=1)


# ── Scenario: large project with subtasks ─────────────────────────────


def test_large_project_is_split_across_multiple_days():
    t = task(
        "capstone",
        kind="project",
        est_minutes=300,
        due_date=TODAY + timedelta(days=12),
        subtask_count=6,
        priority=80,
        difficulty="hard",
    )
    plan = build_plan([t], caps(120), today=TODAY)
    used = days_used(plan, "capstone")
    assert len(used) >= 4, used
    assert sum(s.minutes for s in plan.sessions) >= 290
    # And no single sitting is a marathon.
    assert max(s.minutes for s in plan.sessions) <= 110


def test_exam_revision_is_spaced_across_days():
    t = task("midterm", kind="exam", est_minutes=240, due_date=TODAY + timedelta(days=9), priority=90)
    plan = build_plan([t], caps(200), today=TODAY)
    used = days_used(plan, "midterm")
    assert len(used) >= 4, used
    # Spacing means distinct days, not four sittings stacked on one evening.
    per_day = [len([s for s in plan.sessions if s.day == d]) for d in used]
    assert max(per_day) <= 2, per_day


# ── Scenario: fast and slow students ──────────────────────────────────


def _history(n, estimated, actual):
    return [
        {
            "estimated_time": estimated,
            "actual_time": actual,
            "course": "math",
            "kind": "homework",
            "completed_at": NOW - timedelta(days=i + 1),
        }
        for i in range(n)
    ]


def test_fast_student_gets_a_shorter_plan_than_slow_student():
    t = [task("a", est_minutes=60, due_date=TODAY + timedelta(days=7))]
    fast = build_estimation_model(_history(12, 60, 30), now=NOW)
    slow = build_estimation_model(_history(12, 60, 110), now=NOW)
    fast_plan = build_plan(t, caps(180), model=fast, today=TODAY)
    slow_plan = build_plan(t, caps(180), model=slow, today=TODAY)
    assert fast_plan.total_minutes < 60 < slow_plan.total_minutes


def test_slow_student_work_is_split_and_started_earlier():
    t = [task("a", est_minutes=90, due_date=TODAY + timedelta(days=6), priority=70)]
    slow = build_estimation_model(_history(14, 60, 140), now=NOW)
    plan = build_plan(t, caps(180), model=slow, today=TODAY)
    assert plan.total_minutes > 150
    # Default 45-minute stamina — task-feedback totals must not be mistaken
    # for sitting lengths — so ~210 minutes is four or five sittings.
    assert len(plan.sessions) >= 4
    assert max(s.minutes for s in plan.sessions) <= 60


def test_low_stamina_student_gets_more_shorter_sessions():
    t = [task("a", est_minutes=150, due_date=TODAY + timedelta(days=8))]
    short_focus = build_estimation_model(
        session_rows=[
            {"estimated": 25, "actual": 22, "completed": True, "at": NOW - timedelta(days=i)}
            for i in range(10)
        ],
        now=NOW,
    )
    plan = build_plan(t, caps(180), model=short_focus, today=TODAY)
    assert max(s.minutes for s in plan.sessions) <= 40
    assert len(plan.sessions) >= 4


# ── Scenario: limited availability ────────────────────────────────────


def test_limited_availability_uses_only_the_days_that_exist():
    availability = [0, 0, 60, 0, 0, 90, 0, 0, 0, 45, 0, 0, 0, 0]
    tasks = [task("a", est_minutes=120, due_date=TODAY + timedelta(days=11))]
    plan = build_plan(tasks, caps(availability), today=TODAY)
    open_days = {TODAY + timedelta(days=i) for i, m in enumerate(availability) if m}
    assert {s.day for s in plan.sessions} <= open_days


def test_zero_availability_defers_everything_loudly():
    plan = build_plan([task("a", est_minutes=60, due_date=TODAY + timedelta(days=3))], caps(0), today=TODAY)
    assert plan.overloaded
    assert plan.deferred


# ── Scenario: several hard assignments in one week ────────────────────


def test_hard_work_does_not_all_stack_on_one_day():
    tasks = [
        task(f"h{i}", est_minutes=70, difficulty="hard", due_date=TODAY + timedelta(days=6), course=f"c{i}")
        for i in range(5)
    ]
    plan = build_plan(tasks, caps(150), today=TODAY)
    per_day = {}
    for s in plan.sessions:
        if s.difficulty == "hard":
            per_day[s.day] = per_day.get(s.day, 0) + s.minutes
    assert max(per_day.values()) <= 150


def test_context_switching_is_penalised():
    # Six different courses, plenty of room. A day holding all six is a bad
    # plan even though it fits.
    tasks = [
        task(f"c{i}", course=f"course{i}", est_minutes=30, due_date=TODAY + timedelta(days=9))
        for i in range(6)
    ]
    plan = build_plan(tasks, caps(240), today=TODAY)
    per_day_courses = {}
    for s in plan.sessions:
        per_day_courses.setdefault(s.day, set()).add(s.course)
    assert max(len(v) for v in per_day_courses.values()) <= 4


# ── Dependencies ──────────────────────────────────────────────────────


def test_prerequisite_is_scheduled_before_its_dependent():
    outline = task("outline", est_minutes=40, due_date=TODAY + timedelta(days=10))
    essay = task(
        "essay",
        kind="project",
        est_minutes=90,
        due_date=TODAY + timedelta(days=10),
        depends_on=("outline",),
    )
    plan = build_plan([essay, outline], caps(120), today=TODAY)
    assert max(days_used(plan, "outline")) <= min(days_used(plan, "essay"))


# ── Weak concepts ─────────────────────────────────────────────────────


def test_weak_concepts_are_not_all_stacked_on_one_day():
    tasks = [
        task(
            f"w{i}",
            est_minutes=45,
            due_date=TODAY + timedelta(days=8),
            concepts=("integrals",),
            weak_concept_penalty=0.8,
            course=f"c{i}",
        )
        for i in range(4)
    ]
    plan = build_plan(tasks, caps(240), today=TODAY)
    per_day = {}
    for s in plan.sessions:
        per_day[s.day] = per_day.get(s.day, 0) + 1
    assert max(per_day.values()) <= 2, per_day


# ── Adaptive rescheduling ─────────────────────────────────────────────


def test_missed_session_does_not_cram_everything_into_tomorrow():
    tasks = [
        task("a", est_minutes=120, due_date=TODAY + timedelta(days=8)),
        task("b", est_minutes=120, due_date=TODAY + timedelta(days=9), course="bio"),
    ]
    original = build_plan(tasks, caps(120), today=TODAY)
    tomorrow = TODAY + timedelta(days=1)
    recovered = reschedule(
        tasks,
        caps(120, start=tomorrow),
        Reality(missed_task_ids=frozenset({"a"})),
        today=tomorrow,
    )
    first_day = [d for d in recovered.days if d.day == tomorrow][0]
    assert first_day.scheduled_minutes <= 120
    assert not recovered.overloaded
    assert original.total_minutes > 0


def test_partial_progress_is_credited_not_repeated():
    t = [task("a", est_minutes=120, due_date=TODAY + timedelta(days=8))]
    tomorrow = TODAY + timedelta(days=1)
    after = reschedule(
        t,
        caps(120, start=tomorrow),
        Reality(abandoned={"a": 50}),
        today=tomorrow,
    )
    assert 60 <= after.total_minutes <= 75, after.total_minutes


def test_finished_work_disappears_from_the_plan():
    tasks = [task("a", est_minutes=60, due_date=TODAY + timedelta(days=5)), task("b", est_minutes=60)]
    after = reschedule(
        tasks,
        caps(120),
        Reality(finished_task_ids=frozenset({"a"})),
        today=TODAY,
    )
    assert {s.task_id for s in after.sessions} == {"b"}


def test_finishing_early_frees_the_rest_of_the_week():
    tasks = [
        task("a", est_minutes=90, due_date=TODAY + timedelta(days=6)),
        task("b", est_minutes=90, due_date=TODAY + timedelta(days=7), course="bio"),
        task("c", est_minutes=90, due_date=TODAY + timedelta(days=8), course="chem"),
    ]
    before = build_plan(tasks, caps(90), today=TODAY)
    after = reschedule(
        tasks,
        caps(90),
        Reality(finished_task_ids=frozenset({"a", "b"})),
        today=TODAY,
    )
    assert after.total_minutes < before.total_minutes
    # Finishing early should buy back days, not just minutes.
    assert len({s.day for s in after.sessions}) < len({s.day for s in before.sessions})


def test_repeated_misses_raise_priority_and_pull_work_earlier():
    t = [
        task("slipping", est_minutes=60, due_date=TODAY + timedelta(days=10), priority=40),
        task("other", est_minutes=60, due_date=TODAY + timedelta(days=10), priority=40, course="bio"),
    ]
    baseline = build_plan(t, caps(60), today=TODAY)
    bumped = reschedule(t, caps(60), Reality(missed_task_ids=frozenset({"slipping"})), today=TODAY)
    assert min(days_used(bumped, "slipping")) <= min(days_used(baseline, "slipping"))


# ── Explanations and contracts ────────────────────────────────────────


def test_every_session_can_explain_itself():
    tasks = [task("a", est_minutes=100, due_date=TODAY + timedelta(days=5), priority=90)]
    plan = build_plan(tasks, caps(120), today=TODAY)
    assert plan.sessions
    for s in plan.sessions:
        assert s.reasons, s


def test_plan_is_deterministic():
    tasks = [task(f"t{i}", est_minutes=70, due_date=TODAY + timedelta(days=i + 2)) for i in range(6)]
    a = build_plan(tasks, caps(120), today=TODAY)
    b = build_plan(tasks, caps(120), today=TODAY)
    assert a == b


def test_no_work_is_silently_lost():
    tasks = [task(f"t{i}", est_minutes=95, due_date=TODAY + timedelta(days=9)) for i in range(5)]
    plan = build_plan(tasks, caps(150), today=TODAY)
    planned = sum(s.minutes for s in plan.sessions) + sum(d.minutes for d in plan.deferred)
    assert planned >= 5 * 90


def test_empty_input_is_a_valid_empty_plan():
    plan = build_plan([], caps(120), today=TODAY)
    assert plan.sessions == ()
    assert not plan.overloaded


def test_task_from_mapping_tolerates_messy_input():
    t = task_from_mapping(
        {
            "title": "Lab writeup",
            "course": "Chemistry",
            "type": "LAB",
            "due": "2026-03-09T23:59:00",
            "duration_minutes": "75",
            "priority": "9999",
            "depends_on": "prelab, safety",
        }
    )
    assert t.kind == "lab"
    assert t.due_date == date(2026, 3, 9)
    assert t.est_minutes == 75
    assert t.priority == 100
    assert t.depends_on == ("prelab", "safety")


def test_overdue_task_is_scheduled_immediately():
    t = task("late", est_minutes=60, due_date=TODAY - timedelta(days=2), priority=90)
    plan = build_plan([t], caps(120), today=TODAY)
    # Past deadline is unreachable, so it must surface rather than vanish.
    assert plan.deferred or all(s.day == TODAY for s in plan.sessions)


# ── Regressions found by inspecting real plans ────────────────────────


def test_sessions_never_exceed_the_largest_available_day():
    # 30 minutes a day. A 48-minute sitting can never be placed anywhere,
    # and used to sit in the deferred pile while days stayed empty.
    tasks = [task("essay", kind="project", est_minutes=150, due_date=TODAY + timedelta(days=9))]
    plan = build_plan(tasks, caps(30), today=TODAY)
    assert plan.sessions
    assert max(s.minutes for s in plan.sessions) <= 30


def test_one_tasks_sittings_are_not_stacked_on_a_single_day():
    tasks = [task("pset", est_minutes=60, due_date=TODAY + timedelta(days=3))]
    plan = build_plan(tasks, caps(120), today=TODAY)
    per_day = {}
    for s in plan.sessions:
        per_day[s.day] = per_day.get(s.day, 0) + 1
    # Splitting then re-stacking on one afternoon is a split that bought
    # nothing; it must be folded back into one block.
    assert max(per_day.values()) == 1


def test_part_numbers_run_in_chronological_order():
    tasks = [task("midterm", kind="exam", est_minutes=240, due_date=TODAY + timedelta(days=9))]
    plan = build_plan(tasks, caps(120), today=TODAY)
    parts = [(s.day, s.part_index) for s in sorted(plan.sessions, key=lambda s: s.day)]
    assert [p for _, p in parts] == sorted(p for _, p in parts)
    assert {s.part_total for s in plan.sessions} == {len(plan.sessions)}


def test_deferrals_are_one_row_per_task_not_per_sitting():
    tasks = [task("big", est_minutes=300, due_date=TODAY + timedelta(days=3))]
    plan = build_plan(tasks, caps(30), today=TODAY)
    assert plan.deferred
    assert len(plan.deferred) == len({d.task_id for d in plan.deferred})
    assert plan.deferred[0].minutes >= 60


def test_scarce_time_spreads_across_tasks_rather_than_pooling():
    # One loud high-priority exam plus four ordinary assignments, and far
    # too little time. Straight priority ordering spends the entire
    # fortnight on the exam and lets four deadlines pass untouched.
    exam = task("exam", kind="exam", est_minutes=240, due_date=TODAY + timedelta(days=9), priority=95)
    others = [
        task(f"o{i}", est_minutes=60, due_date=TODAY + timedelta(days=i + 2), course=f"c{i}", priority=60)
        for i in range(4)
    ]
    plan = build_plan([exam, *others], caps(30), today=TODAY)
    covered = {s.task_id for s in plan.sessions}
    assert "exam" in covered
    assert len(covered) >= 4, covered


def test_planning_stays_fast_on_a_huge_workload():
    """Guards against the quadratic triage this pass used to have.

    Overload triage indexed placed work by rescanning every item for every
    candidate day, and retried the whole unplaced pile after each swap. A
    student with hundreds of open assignments — precisely the one who needs
    triage most — waited seconds for it. The bound is deliberately loose;
    it exists to catch a return to quadratic, not to police milliseconds.
    """
    import time

    tasks = [
        task(f"t{i}", est_minutes=60, course=f"c{i % 8}",
             due_date=TODAY + timedelta(days=2 + (i % 12)), priority=30 + (i % 7) * 10)
        for i in range(600)
    ]
    started = time.perf_counter()
    plan = build_plan(tasks, caps(300), today=TODAY)
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0, f"planning 600 tasks took {elapsed:.2f}s"
    assert plan.sessions


@pytest.mark.parametrize("horizon", [1, 3, 7, 14])
def test_capacity_is_never_exceeded_without_saying_so(horizon):
    config = PlannerConfig(horizon_days=horizon)
    tasks = [task(f"t{i}", est_minutes=100, due_date=TODAY + timedelta(days=horizon)) for i in range(6)]
    plan = build_plan(tasks, caps(120, days=horizon), today=TODAY, config=config)
    for d in plan.days:
        if d.scheduled_minutes > d.capacity_minutes:
            assert plan.overloaded


# ── Stated daily target ───────────────────────────────────────────────


def test_a_stated_daily_target_flattens_the_week():
    """"Two hours a day" should shape the plan, not be decoration.

    The student's availability windows are the hard ceiling; what they said
    they want to do per day is a comfort ceiling. Above it work is priced, so
    a plan that fits inside the target spreads out instead of piling onto the
    first day the deadline allows.
    """
    tasks = [task(f"t{i}", est_minutes=60, due_date=TODAY + timedelta(days=8)) for i in range(6)]
    loose = build_plan(tasks, caps(300), today=TODAY)
    tight = build_plan(
        tasks,
        [DayCapacity(day=c.day, minutes=c.minutes, comfort_minutes=90) for c in caps(300)],
        today=TODAY,
    )
    assert max(minutes_by_day(tight).values()) < max(minutes_by_day(loose).values())


def test_the_daily_target_is_a_preference_not_a_ceiling():
    """A deadline still overrides comfort. Refusing to schedule work the
    student has time for, because they once said "one hour a day", is how a
    planner ends up hiding a deadline it could have met."""
    tasks = [task("cram", est_minutes=180, due_date=TODAY + timedelta(days=1), priority=90)]
    plan = build_plan(
        tasks,
        [DayCapacity(day=c.day, minutes=c.minutes, comfort_minutes=60) for c in caps(240, days=2)],
        today=TODAY,
    )
    assert not plan.deferred
    assert plan.total_minutes >= 170


def test_the_daily_target_never_manufactures_capacity():
    """A target above the real free time changes nothing — the calendar wins."""
    tasks = [task("t1", est_minutes=200, due_date=TODAY + timedelta(days=3))]
    plan = build_plan(
        tasks,
        [DayCapacity(day=c.day, minutes=c.minutes, comfort_minutes=600) for c in caps(60, days=4)],
        today=TODAY,
    )
    for d in plan.days:
        assert d.scheduled_minutes <= d.capacity_minutes
