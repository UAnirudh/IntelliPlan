"""Autopilot's decision rules: when it may act, and what counts as new."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from intelliplan.intelligence.autopilot import (
    AutopilotState,
    Trigger,
    detect_new_work,
    explain,
    should_run,
)
from intelliplan.intelligence.planner import PlannerTask

TODAY = date(2026, 3, 2)
NOW = datetime(2026, 3, 2, 9)

PLAN = {"schedule": [{"date": TODAY.isoformat(), "blocks": [
    {"task_id": "canvas-17", "assignment": "Chem lab (part 1 of 2)", "parent_title": "Chem lab",
     "duration_minutes": 40},
]}]}


def task(id_, title, days=4):
    return PlannerTask(id=id_, title=title, due_date=TODAY + timedelta(days=days))


def test_work_the_plan_already_has_is_not_new_by_id_or_by_title():
    found = detect_new_work(PLAN, [task("canvas-17", "Chem lab"), task("other-id", "Chem lab")], TODAY)
    assert found == ()


def test_new_work_is_found_and_stages_travel_together():
    found = detect_new_work(PLAN, [
        task("essay::draft", "Essay — Draft"), task("essay::revise", "Essay — Revise"),
        task("quiz", "Quiz"),
    ], TODAY)
    assert {t.id for t in found} == {"essay::draft", "essay::revise", "quiz"}


def test_overdue_undated_and_far_off_work_is_left_alone():
    found = detect_new_work(PLAN, [
        task("late", "Late", days=-1), PlannerTask(id="undated", title="Someday"),
        task("far", "Far", days=40),
    ], TODAY)
    assert found == ()


def test_finished_titles_are_never_absorbed():
    assert detect_new_work(PLAN, [task("q", "Quiz 3")], TODAY, finished_titles=["Quiz 3"]) == ()


def test_facts_always_run_but_risk_alone_waits_out_the_cooldown():
    recent = AutopilotState(last_run=NOW - timedelta(hours=1))
    assert should_run(recent, [Trigger("missed", "x")], TODAY, NOW)
    assert not should_run(recent, [Trigger("at_risk", "x")], TODAY, NOW)
    assert should_run(AutopilotState(last_run=NOW - timedelta(hours=7)), [Trigger("at_risk", "x")], TODAY, NOW)


def test_off_means_off_and_an_undo_is_respected_for_the_day():
    assert not should_run(AutopilotState(enabled=False), [Trigger("missed", "x")], TODAY, NOW)
    undone = AutopilotState(suppressed_until=TODAY)
    assert not should_run(undone, [Trigger("missed", "x")], TODAY, NOW)
    assert should_run(undone, [Trigger("missed", "x")], TODAY + timedelta(days=1), NOW)


def test_state_round_trips_and_the_log_is_bounded():
    state = AutopilotState()
    for i in range(30):
        state = state.with_entry({"i": i}, now=NOW)
    again = AutopilotState.from_json(state.to_json())
    assert len(again.log) == 12 and again.log[-1]["i"] == 29
    assert AutopilotState.from_json("garbage") == AutopilotState()


def test_the_explanation_is_plain():
    headline, reasons = explain([Trigger("missed", "90 min you didn't get to (Chem) got new days")], 2)
    assert headline == "Autopilot updated your plan — 2 sessions moved."
    assert reasons == ["90 min you didn't get to (Chem) got new days."]
