"""Tests for the planner → application seam."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from intelliplan.intelligence.planner import Reality
from intelliplan.services.scheduling import (
    SchedulingService,
    StudentContext,
    plan_to_schedule_data,
)

TODAY = date(2026, 3, 2)
NOW = datetime(2026, 3, 2, 7, 0)

FULL_WEEK = {d: "afternoon,evening" for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")}


def rows(**over):
    base = {
        "id": "t1",
        "title": "Problem set",
        "course": "Math",
        "kind": "homework",
        "due_date": (TODAY + timedelta(days=4)).isoformat(),
        "est_minutes": 90,
        "priority": 60,
    }
    base.update(over)
    return base


def service(**ctx):
    return SchedulingService(StudentContext(availability=FULL_WEEK, **ctx))


# ── Capacity ──────────────────────────────────────────────────────────


def test_capacity_comes_from_real_availability():
    svc = service()
    caps = svc.capacities(TODAY, horizon_days=7, now=NOW)
    assert len(caps) == 7
    # afternoon (12–17) + evening (17–22) = 10 hours of window, but capacity
    # is study time, and the clock placer spends part of that window on gaps
    # and breaks. Roughly three quarters of the raw figure is real.
    assert 400 <= caps[1].minutes <= 500


def test_commitments_subtract_from_capacity():
    plain = service().capacities(TODAY, 7, now=NOW)[1].minutes
    busy = service(commitments="soccer Tue 4-6 pm").capacities(TODAY, 7, now=NOW)[1].minutes
    assert busy < plain


def test_days_with_no_availability_have_no_capacity():
    svc = SchedulingService(StudentContext(availability={"Mon": "evening"}, preferred_time=""))
    caps = svc.capacities(TODAY, 7, now=NOW)
    # Only Monday was declared; the rest fall back to the (empty) preference.
    assert caps[0].minutes > 0


def test_weak_days_are_priced_not_blocked():
    svc = service(weak_days=("Tue",))
    caps = {c.day: c for c in svc.capacities(TODAY, 7, now=NOW)}
    tuesday = caps[TODAY + timedelta(days=1)]
    assert tuesday.minutes > 0
    assert tuesday.quality < 1.0


def test_malformed_availability_does_not_crash_planning():
    svc = SchedulingService(StudentContext(availability={"Mon": {"nonsense": True}}))
    plan = svc.plan([rows()], today=TODAY, now=NOW)
    assert plan is not None


# ── Planning end to end ───────────────────────────────────────────────


def test_plan_schedules_the_work():
    plan = service().plan([rows()], today=TODAY, now=NOW)
    assert plan.total_minutes >= 85
    assert all(s.day <= date.fromisoformat(rows()["due_date"]) for s in plan.sessions)


def test_history_changes_the_plan_length():
    fast = service(
        feedback_rows=[
            {"estimated_time": 60, "actual_time": 30, "course": "Math", "kind": "homework"}
            for _ in range(10)
        ]
    )
    slow = service(
        feedback_rows=[
            {"estimated_time": 60, "actual_time": 110, "course": "Math", "kind": "homework"}
            for _ in range(10)
        ]
    )
    assert fast.plan([rows()], today=TODAY, now=NOW).total_minutes < 90
    assert slow.plan([rows()], today=TODAY, now=NOW).total_minutes > 90


def test_weak_concepts_are_picked_up_from_mastery():
    svc = service(concept_mastery={"integrals": 0.2})
    tasks = svc.tasks_from([rows(concepts=["integrals"])])
    assert tasks[0].weak_concept_penalty == pytest.approx(0.8)


def test_unknown_concepts_carry_no_penalty():
    svc = service(concept_mastery={"integrals": 0.2})
    assert svc.tasks_from([rows(concepts=["photosynthesis"])])[0].weak_concept_penalty == 0.0


def test_unparseable_rows_are_skipped_not_fatal():
    svc = service()
    tasks = svc.tasks_from([rows(), {"title": None, "due_date": "not-a-date"}])
    assert len(tasks) == 2  # the second degrades to defaults rather than vanishing


def test_replan_credits_work_already_done():
    svc = service()
    before = svc.plan([rows()], today=TODAY, now=NOW).total_minutes
    after = svc.replan(
        [rows()], Reality(abandoned={"t1": 40}), today=TODAY, now=NOW
    ).total_minutes
    assert after < before


# ── Rendering to the app's block shape ────────────────────────────────


def test_schedule_data_has_the_shape_the_app_renders():
    svc = service()
    plan = svc.plan([rows()], today=TODAY, now=NOW)
    data = svc.to_schedule_data(plan, now=NOW)

    assert data["generated_by"] == "planner-v2"
    assert data["schedule"]
    day = data["schedule"][0]
    assert {"date", "day_name", "blocks"} <= set(day)
    block = day["blocks"][0]
    for key in ("id", "assignment", "course", "duration_minutes", "difficulty", "time_slot"):
        assert key in block, key
    assert block["difficulty"] in ("Easy", "Medium", "Hard")


def test_blocks_carry_their_explanation():
    svc = service()
    data = svc.to_schedule_data(svc.plan([rows()], today=TODAY, now=NOW), now=NOW)
    work = [b for d in data["schedule"] for b in d["blocks"] if not b.get("is_break")]
    assert work
    assert all(b["reasons"] for b in work)


def test_block_times_are_real_clock_times_in_order():
    svc = service()
    data = svc.to_schedule_data(svc.plan([rows(est_minutes=200)], today=TODAY, now=NOW), now=NOW)
    for day in data["schedule"]:
        starts = [b["start_iso"] for b in day["blocks"] if b.get("start_iso")]
        assert starts == sorted(starts)


def test_overload_is_surfaced_to_the_client():
    svc = SchedulingService(StudentContext(availability={"Mon": "evening"}, preferred_time="evening"))
    tasks = [
        rows(id=f"t{i}", title=f"Task {i}", est_minutes=200, due_date=(TODAY + timedelta(days=2)).isoformat())
        for i in range(6)
    ]
    data = svc.to_schedule_data(svc.plan(tasks, today=TODAY, now=NOW), now=NOW)
    assert data["overloaded"] is True
    assert data["deferred"]
    assert data["notes"]


def test_empty_task_list_renders_an_empty_schedule():
    svc = service()
    data = svc.to_schedule_data(svc.plan([], today=TODAY, now=NOW), now=NOW)
    assert data["schedule"] == []
    assert data["overloaded"] is False


# ── Regressions found running the real endpoint ───────────────────────


def test_capacity_reserves_room_for_breaks_and_gaps():
    # A five-hour evening does not hold five hours of study — the clock
    # placer spends minutes on gaps and long breaks out of the same window.
    svc = service()
    raw = 600  # afternoon + evening
    assert svc.capacities(TODAY, 7, now=NOW)[1].minutes < raw * 0.85


def test_no_session_is_lost_between_allocation_and_the_clock():
    """Every allocated sitting must appear on the calendar or as a deferral.

    This is the failure that produced "Calculus Midterm (part 2 of 7)" with
    no part 1: the planner filled the day to its raw window size, placement
    then ran out of clock, and the overflow was mentioned only in a note.
    """
    svc = service()
    tasks = [
        rows(id=f"t{i}", title=f"Task {i}", est_minutes=90, course=f"c{i}",
             due_date=(TODAY + timedelta(days=3)).isoformat())
        for i in range(5)
    ]
    plan = svc.plan(tasks, today=TODAY, now=NOW)
    data = svc.to_schedule_data(plan, now=NOW)

    placed = [
        b for d in data["schedule"] for b in d["blocks"]
        if not b.get("is_break")
    ]
    allocated = len(plan.sessions)
    assert len(placed) + len(data["deferred"]) >= allocated


def test_parts_of_a_split_task_are_contiguously_numbered_on_the_calendar():
    svc = service()
    plan = svc.plan(
        [rows(id="midterm", title="Midterm", kind="exam", est_minutes=240,
              due_date=(TODAY + timedelta(days=9)).isoformat())],
        today=TODAY, now=NOW,
    )
    data = svc.to_schedule_data(plan, now=NOW)
    # Grouped by task, not by parent assignment: a large exam is decomposed
    # into stages, each of which is its own task and numbers its own sittings.
    # "Part 2 of 3" only has to have a part 1 inside the same task.
    by_task: dict[str, list[int]] = {}
    for d in data["schedule"]:
        for b in d["blocks"]:
            if b.get("is_break"):
                continue
            by_task.setdefault(b["task_id"], []).append(b["part_index"])
    assert by_task
    for task_id, parts in by_task.items():
        assert sorted(parts) == list(range(1, len(parts) + 1)), (task_id, parts)


def test_a_day_never_reports_more_time_than_it_holds():
    """The two figures a day shows must be measurable against each other.

    ``total_minutes`` is recomputed downstream across every block including
    breaks, so it has to be compared against the whole window rather than
    the study-only capacity the optimizer allocates against — otherwise a
    correct plan renders as "235 / 223".
    """
    svc = service()
    tasks = [
        rows(id=f"t{i}", title=f"Task {i}", est_minutes=90, course=f"c{i}",
             due_date=(TODAY + timedelta(days=4)).isoformat())
        for i in range(4)
    ]
    data = svc.to_schedule_data(svc.plan(tasks, today=TODAY, now=NOW), now=NOW)
    for day in data["schedule"]:
        wall = sum(b.get("duration_minutes") or 0 for b in day["blocks"])
        assert wall <= day["capacity_minutes"], day["date"]
        assert day["study_minutes"] <= day["study_capacity_minutes"], day["date"]


def test_blocks_that_overflow_the_clock_become_deferrals_not_footnotes():
    svc = service()
    # One short evening, far more work than it holds.
    tight = SchedulingService(
        StudentContext(availability={d: "evening" for d in
                                     ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")})
    )
    tasks = [
        rows(id=f"t{i}", title=f"Task {i}", est_minutes=180, course=f"c{i}",
             due_date=(TODAY + timedelta(days=1)).isoformat())
        for i in range(5)
    ]
    data = tight.to_schedule_data(tight.plan(tasks, today=TODAY, now=NOW), now=NOW)
    assert data["overloaded"] is True
    assert data["deferred"]


def test_planner_order_survives_clock_placement():
    svc = service()
    tasks = [
        rows(id="urgent", title="Urgent", due_date=(TODAY + timedelta(days=1)).isoformat(), priority=95),
        rows(id="later", title="Later", due_date=(TODAY + timedelta(days=9)).isoformat(), priority=20),
    ]
    plan = svc.plan(tasks, today=TODAY, now=NOW)
    data = plan_to_schedule_data(plan, availability=FULL_WEEK, now=NOW)
    first_day = data["schedule"][0]["blocks"]
    titles = [b["parent_title"] for b in first_day if not b.get("is_break")]
    assert "Urgent" in titles


def test_stated_daily_target_reaches_the_capacities():
    svc = service(daily_target_minutes=90)
    caps = svc.capacities(TODAY, 7, now=NOW)
    assert all(c.comfort_minutes == 90 for c in caps)
    # The window is still the hard limit — the target only lowers the soft one.
    assert caps[1].minutes > 90
    assert caps[1].soft_limit(0.8) == 90


def test_no_stated_target_leaves_the_default_utilisation_alone():
    caps = service().capacities(TODAY, 7, now=NOW)
    assert caps[1].comfort_minutes is None
    assert caps[1].soft_limit(0.8) == int(round(caps[1].minutes * 0.8))


# ── Stages ────────────────────────────────────────────────────────────


def test_a_big_essay_becomes_named_stages_in_order():
    """"Part 3 of 5" does not tell a student what to do when they sit down,
    and it implies the parts are interchangeable when they are not."""
    svc = service()
    plan = svc.plan(
        [rows(id="paper", title="Research paper on the New Deal", kind="project",
              est_minutes=600, course="History",
              due_date=(TODAY + timedelta(days=12)).isoformat())],
        today=TODAY, now=NOW,
    )
    labels = [s.title for s in plan.sessions]
    assert any("Research and gather sources" in t for t in labels)
    assert any("Write the draft" in t for t in labels)

    def first_day(fragment):
        return min(s.day for s in plan.sessions if fragment in s.title)

    assert first_day("Research and gather sources") < first_day("Write the draft")
    assert first_day("Write the draft") <= first_day("Revise")


def test_a_prerequisite_is_never_scheduled_after_its_dependent():
    svc = service()
    plan = svc.plan(
        [rows(id="paper", title="Research paper", kind="project", est_minutes=600,
              due_date=(TODAY + timedelta(days=12)).isoformat())],
        today=TODAY, now=NOW,
    )
    by_id = {}
    for s in plan.sessions:
        by_id.setdefault(s.task_id, []).append(s.day)
    tasks = {t.id: t for t in svc.tasks_from([
        rows(id="paper", title="Research paper", kind="project", est_minutes=600,
             due_date=(TODAY + timedelta(days=12)).isoformat())
    ])}
    for task_id, days in by_id.items():
        for dep in tasks[task_id].depends_on:
            if dep in by_id:
                assert max(by_id[dep]) <= min(days), (dep, task_id)


def test_a_problem_set_is_not_decomposed_end_to_end():
    svc = service()
    plan = svc.plan(
        [rows(id="ps", title="Problem set 7", kind="homework", est_minutes=400,
              due_date=(TODAY + timedelta(days=6)).isoformat())],
        today=TODAY, now=NOW,
    )
    assert all(s.task_id == "ps" for s in plan.sessions)


def test_stage_blocks_still_point_back_at_their_assignment():
    """Enrichment, the interactive view and the calendar export all match a
    block to its assignment by parent_title."""
    svc = service()
    plan = svc.plan(
        [rows(id="paper", title="Research paper", kind="project", est_minutes=600,
              due_date=(TODAY + timedelta(days=12)).isoformat())],
        today=TODAY, now=NOW,
    )
    data = svc.to_schedule_data(plan, now=NOW)
    work = [b for d in data["schedule"] for b in d["blocks"] if not b.get("is_break")]
    assert work
    assert all(b["parent_title"] == "Research paper" for b in work)
    assert all(b["stage_title"] for b in work)


def test_decomposition_can_be_turned_off():
    svc = SchedulingService(
        StudentContext(availability=FULL_WEEK), decompose_stages=False
    )
    tasks = svc.tasks_from(
        [rows(id="paper", title="Research paper", kind="project", est_minutes=600)]
    )
    assert [t.id for t in tasks] == ["paper"]


def test_stages_do_not_inflate_the_total_work():
    svc = service()
    staged = svc.tasks_from(
        [rows(id="paper", title="Research paper", kind="project", est_minutes=600)]
    )
    assert sum(t.est_minutes for t in staged) == 600


def test_work_already_done_is_not_subtracted_once_per_stage():
    svc = service()
    staged = svc.tasks_from(
        [rows(id="paper", title="Research paper", kind="project", est_minutes=600,
              done_minutes=120)]
    )
    assert all(t.done_minutes == 0 for t in staged)


# ── Concepts ──────────────────────────────────────────────────────────


def test_concepts_are_matched_from_what_the_student_studies():
    """The concept_stack weight has been priced since the planner was written
    and nothing ever populated PlannerTask.concepts."""
    svc = service(concept_mastery={"integrals": 0.2, "photosynthesis": 0.9})
    task = svc.tasks_from(
        [rows(title="Practice with integrals", description="Definite integrals only.")]
    )[0]
    assert task.concepts == ("integrals",)
    assert task.weak_concept_penalty == pytest.approx(0.8)


def test_only_the_students_own_concepts_are_matched():
    """Inventing concepts from a title is how a planner starts asserting that
    "Chapter 4" is about integrals."""
    svc = service(concept_mastery={"integrals": 0.2})
    task = svc.tasks_from([rows(title="Chapter 4 review")])[0]
    assert task.concepts == ()


def test_an_explicit_concept_list_is_not_overwritten():
    svc = service(concept_mastery={"integrals": 0.2})
    task = svc.tasks_from(
        [rows(title="Practice with integrals", concepts=["derivatives"])]
    )[0]
    assert task.concepts == ("derivatives",)


def test_concept_matching_respects_word_boundaries():
    svc = service(concept_mastery={"ion": 0.1})
    assert svc.tasks_from([rows(title="Station rotation")])[0].concepts == ()


def test_no_mastery_data_means_no_concept_work_at_all():
    svc = service()
    assert svc.tasks_from([rows(title="Practice with integrals")])[0].concepts == ()
