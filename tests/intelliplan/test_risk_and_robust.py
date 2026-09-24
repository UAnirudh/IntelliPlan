"""Deadline risk simulation and the risk-controlled planning loop."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

from intelliplan.intelligence.estimation import build_estimation_model

from intelliplan.intelligence.planner import (
    PlannerTask,
    build_plan,
    capacities_from_minutes,
)
from intelliplan.intelligence.risk import RiskReport, TaskRisk, group_key, simulate
from intelliplan.intelligence.robust import plan_with_risk_control

TODAY = date(2026, 3, 2)


def tasks(n, due_in, minutes=90, priority=60):
    return [
        PlannerTask(
            id=f"t{i}", title=f"Task {i}", course=f"c{i % 3}", kind="homework",
            due_date=TODAY + timedelta(days=due_in + i % 4), est_minutes=minutes,
            priority=priority,
        )
        for i in range(n)
    ]


def planned(task_list, per_day=120):
    return build_plan(task_list, capacities_from_minutes(TODAY, [per_day] * 14), today=TODAY)


# ── the simulator ────────────────────────────────────────────────────


def test_the_same_plan_always_reports_the_same_numbers():
    ts = tasks(6, 8)
    plan = planned(ts)
    assert simulate(plan, ts, today=TODAY).to_dict() == simulate(plan, ts, today=TODAY).to_dict()


def test_a_relaxed_week_is_on_track():
    ts = tasks(5, 9)
    report = simulate(planned(ts), ts, today=TODAY)
    assert report.weighted_on_time > 0.85
    assert not report.at_risk


def test_a_week_that_cannot_fit_says_so_and_says_why():
    ts = tasks(14, 2, minutes=120)
    report = simulate(planned(ts), ts, today=TODAY)
    assert report.at_risk
    assert report.expected_missed > 3
    assert any(t.main_risk == "not_planned" for t in report.at_risk)


def test_a_student_who_follows_through_less_is_at_more_risk():
    ts = tasks(8, 4)
    plan = planned(ts)
    steady = simulate(plan, ts, today=TODAY, follow_through=lambda s, load: 0.9, seed=1)
    shaky = simulate(plan, ts, today=TODAY, follow_through=lambda s, load: 0.35, seed=1)
    assert shaky.weighted_on_time < steady.weighted_on_time - 0.05


def test_work_we_cannot_size_well_is_riskier():
    ts = tasks(8, 3)
    plan = planned(ts)
    sure = simulate(plan, ts, today=TODAY, sigma_for=lambda t: 0.1, seed=5)
    unsure = simulate(plan, ts, today=TODAY, sigma_for=lambda t: 0.9, seed=5)
    assert unsure.expected_missed > sure.expected_missed


def test_stages_roll_up_to_the_assignment_the_student_knows():
    essay = [
        PlannerTask(id="essay::draft", title="Essay — Draft", parent_title="Essay",
                    due_date=TODAY + timedelta(days=6), est_minutes=90, priority=70),
        PlannerTask(id="essay::revise", title="Essay — Revise", parent_title="Essay",
                    due_date=TODAY + timedelta(days=6), est_minutes=45, priority=70,
                    depends_on=("essay::draft",)),
    ]
    report = simulate(planned(essay), essay, today=TODAY)
    assert [t.key for t in report.tasks] == ["essay"]
    assert report.tasks[0].title == "Essay"
    assert group_key("essay::draft") == "essay"


def test_undated_work_cannot_be_late():
    ts = [PlannerTask(id="x", title="Someday", est_minutes=60)]
    report = simulate(planned(ts), ts, today=TODAY)
    assert report.tasks == () and report.expected_missed == 0


# ── the risk-control loop ────────────────────────────────────────────


def report_for(plan, rewarded_buffer):
    """A stand-in simulator: on-time odds rise with finishing early."""
    rows = []
    for s in plan.sessions:
        slack = (s.due_date - s.day).days if s.due_date else 5
        p = min(0.99, 0.5 + 0.12 * slack) if rewarded_buffer else 0.7
        rows.append(TaskRisk(
            key=group_key(s.task_id), title=s.title, due_date=s.due_date,
            on_time_probability=p, expected_late_minutes=0, planned_minutes=s.minutes,
            unplanned_minutes=0, priority=s.priority,
            status="on_track" if p >= 0.8 else "at_risk", main_risk="follow_through",
        ))
    worst: dict[str, TaskRisk] = {}
    for r in rows:
        if r.key not in worst or r.on_time_probability < worst[r.key].on_time_probability:
            worst[r.key] = r
    tasks_ = tuple(worst.values())
    weighted = sum(t.on_time_probability for t in tasks_) / max(1, len(tasks_))
    return RiskReport(tasks=tasks_, expected_missed=sum(1 - t.on_time_probability for t in tasks_),
                      weighted_on_time=weighted, samples=1, seed=0)


def test_buffer_is_added_where_simulation_says_it_helps():
    # A confident estimate earns only a one-day buffer, and a student who
    # reliably works late pulls the sitting toward the deadline. Simulation
    # (here, a stand-in that rewards slack) says that is too tight.
    now = datetime(2026, 3, 2, 8)
    model = build_estimation_model(
        feedback_rows=[{"estimated": 60, "actual": 60, "at": now - timedelta(days=i)} for i in range(20)],
        now=now,
    )
    ts = [PlannerTask(id="lab", title="Lab", due_date=TODAY + timedelta(days=6),
                      est_minutes=60, priority=40)]
    caps = capacities_from_minutes(TODAY, [120] * 10)
    late_worker = lambda t, m, d, load: 0.2 if (d - TODAY).days < 4 else 0.95
    first = build_plan(ts, caps, model=model, today=TODAY, completion=late_worker)
    result = plan_with_risk_control(
        ts, caps, model=model, today=TODAY, completion=late_worker,
        assess=lambda p, t: report_for(p, True), target=0.8,
    )
    assert result.hardened == ("lab",)
    assert max(s.day for s in result.plan.sessions) < max(s.day for s in first.sessions)
    assert result.report.weighted_on_time > result.initial_report.weighted_on_time


def test_a_change_that_does_not_help_is_thrown_away():
    ts = tasks(4, 6)
    caps = capacities_from_minutes(TODAY, [120] * 14)
    result = plan_with_risk_control(ts, caps, today=TODAY,
                                    assess=lambda p, t: report_for(p, False), target=0.99)
    assert result.hardened == ()
    assert result.plan.sessions == build_plan(ts, caps, today=TODAY).sessions


def test_work_that_does_not_fit_is_not_hardened():
    # Buffer cannot create time; "not planned" risk is the student's call.
    ts = tasks(14, 2, minutes=120)
    caps = capacities_from_minutes(TODAY, [60] * 14)

    def assess(plan, task_list):
        r = simulate(plan, task_list, today=TODAY)
        return replace(r, tasks=tuple(replace(t, main_risk="not_planned") for t in r.tasks))

    result = plan_with_risk_control(ts, caps, today=TODAY, assess=assess)
    assert result.hardened == () and result.rounds == 0
