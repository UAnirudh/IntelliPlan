"""A moved deadline moves the plan.

The FAQ promises: "When a due date changes in Canvas or StudentVue, your
IntelliPlan priority list and schedule update to reflect it." Autopilot used
to re-plan only for missed work, new work and risk, so an assignment already
in the plan kept its old dates.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from intelliplan.intelligence.autopilot import (
    AutopilotState,
    DueChange,
    Trigger,
    apply_due_changes,
    detect_due_changes,
    should_run,
    triggers_for,
)
from intelliplan.intelligence.planner import PlannerTask
from intelliplan.services.scheduling import (
    SchedulingService,
    StudentContext,
    plan_to_schedule_data,
)

TODAY = date(2026, 3, 2)
NOW = datetime(2026, 3, 2, 7, 0)
FULL_WEEK = {d: "afternoon,evening" for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")}


def t(id_, title, days, parent=""):
    return PlannerTask(id=id_, title=title, parent_title=parent, due_date=TODAY + timedelta(days=days))


def test_a_moved_deadline_is_detected_by_id_and_by_title():
    planned = [t("canvas-1", "Lab report", 6), t("sv-9", "Essay", 8)]
    live = [t("canvas-1", "Lab report", 3), t("other-id", "Essay", 10)]
    changes = {c.title: c.delta_days for c in detect_due_changes(planned, live)}
    assert changes == {"Lab report": -3, "Essay": 2}


def test_unchanged_or_unknown_work_is_not_a_change():
    planned = [t("a", "Quiz", 4)]
    assert detect_due_changes(planned, [t("a", "Quiz", 4)]) == ()
    assert detect_due_changes(planned, [t("zzz", "Something else", 1)]) == ()
    assert detect_due_changes(planned, [PlannerTask(id="a", title="Quiz")]) == ()


def test_stages_move_together_and_never_into_the_past():
    stages = [t("essay::outline", "Outline", 2, "Essay"), t("essay::draft", "Draft", 6, "Essay")]
    live = [t("essay::draft", "Draft", 3, "Essay")]  # assignment due pulled in 3 days
    changes = detect_due_changes(stages, live)
    assert len(changes) == 1 and changes[0].delta_days == -3
    moved = {x.id: x.due_date for x in apply_due_changes(stages, changes, TODAY)}
    assert moved["essay::draft"] == TODAY + timedelta(days=3)
    assert moved["essay::outline"] == TODAY  # would have been yesterday


def test_a_due_change_is_a_fact_that_always_runs():
    trig = triggers_for(missed_minutes=0, missed_titles=[], new_tasks=[], report=None,
                        due_changes=[DueChange("k", "Lab report", TODAY, TODAY + timedelta(days=2))])
    assert trig[0].key == "due_changed" and "Lab report is now due Wed Mar 4" in trig[0].text
    recent = AutopilotState(last_run=NOW - timedelta(minutes=5))
    assert should_run(recent, [Trigger("due_changed", "x")], TODAY, NOW)


def test_the_plan_follows_a_deadline_pulled_earlier():
    svc = SchedulingService(StudentContext(availability=FULL_WEEK))
    row = {"id": "t1", "title": "Problem set", "course": "Math", "kind": "homework",
           "due_date": (TODAY + timedelta(days=6)).isoformat(), "est_minutes": 240, "priority": 60}
    data = plan_to_schedule_data(svc.plan([row], today=TODAY, now=NOW))

    new_due = TODAY + timedelta(days=2)
    moved_row = dict(row, due_date=new_due.isoformat())
    run = svc.autopilot(data, {}, [moved_row], today=TODAY, now=NOW)

    assert run is not None and "due_changed" in run.triggers
    late = [
        (day["date"], b.get("assignment"))
        for day in run.data["schedule"] for b in day.get("blocks") or []
        if not b.get("is_break") and date.fromisoformat(day["date"]) > new_due
    ]
    assert late == []

