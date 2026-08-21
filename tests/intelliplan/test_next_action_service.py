"""NextActionService — composition, degradation, and the plan adapter."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from intelliplan.domain.student import MasteryEstimate
from intelliplan.services.next_action import (
    NextActionService,
    decision_to_dict,
    plan_from_schedule_data,
    preview_override,
    tasks_from_plan,
)

NOW = datetime(2026, 8, 21, 18, 0)
TODAY = NOW.date()


def a_block(task_id="t1", *, minutes=45, day=None, course="Physics",
            hour=19, due_in=2, is_break=False):
    day = day or TODAY
    start = datetime.combine(day, datetime.min.time()).replace(hour=hour)
    return {
        "id": f"b-{task_id}-{hour}",
        "task_id": task_id,
        "assignment": f"{course} work",
        "parent_title": f"{course} work",
        "course": course,
        "kind": "homework",
        "duration_minutes": minutes,
        "difficulty": "Medium",
        "priority": 70,
        "due_date": (day + timedelta(days=due_in)).isoformat(),
        "start_iso": start.isoformat(),
        "end_iso": (start + timedelta(minutes=minutes)).isoformat(),
        "is_break": is_break,
    }


def a_schedule(*days):
    return {"schedule": list(days)}


def a_day(day, blocks, capacity=180):
    return {
        "date": day.isoformat(),
        "day_name": day.strftime("%A"),
        "blocks": blocks,
        "capacity_minutes": capacity,
        "total_minutes": sum(b["duration_minutes"] for b in blocks),
    }


def service(**overrides):
    kwargs = dict(
        get_active_schedule=lambda uid: a_schedule(a_day(TODAY, [a_block()])),
        get_remaining_minutes=lambda uid, now: 90,
    )
    kwargs.update(overrides)
    return NextActionService(**kwargs)


# ── the decision ─────────────────────────────────────────────────────


def test_a_student_with_a_plan_gets_a_recommendation():
    decision = service().decide(1, NOW)
    assert decision.has_recommendation
    assert decision.headline.candidate.course == "Physics"
    assert decision.minutes_remaining_today == 90
    assert decision.minutes_planned_today == 45


def test_a_student_with_nothing_outstanding_is_told_so_plainly():
    decision = service(get_active_schedule=lambda uid: None).decide(1, NOW)
    assert not decision.has_recommendation
    assert "caught up" in decision.reason or "Nothing is outstanding" in decision.reason


def test_a_closed_study_window_is_named_rather_than_ignored():
    decision = service(get_remaining_minutes=lambda uid, now: 0).decide(1, NOW)
    assert decision.reason
    assert "closed" in decision.reason


def test_breaks_in_the_plan_do_not_count_as_planned_work():
    schedule = a_schedule(a_day(TODAY, [
        a_block("t1", minutes=45),
        a_block("brk", minutes=15, hour=20, is_break=True),
    ]))
    decision = service(get_active_schedule=lambda uid: schedule).decide(1, NOW)
    assert decision.minutes_planned_today == 45


def test_the_recommendation_is_fast_enough_to_render_synchronously():
    """The budget in the brief is under a second; the engines themselves must
    be nowhere near it, because the providers are the slow part."""
    assert service().decide(1, NOW).compute_ms < 500


# ── degradation ──────────────────────────────────────────────────────


def test_a_failing_provider_degrades_that_input_and_nothing_else():
    def boom(uid):
        raise RuntimeError("LMS is down")

    decision = service(get_assignments=boom).decide(1, NOW)
    assert decision.has_recommendation


def test_an_unreadable_history_falls_back_to_the_population_prior():
    """A history source that is down should look like a student with no
    history, not like a crash — and certainly not like a student we know
    things about."""
    from intelliplan.domain.student import EvidenceSource

    def boom(uid):
        raise RuntimeError("session table unavailable")

    decision = service(get_session_rows=boom).decide(1, NOW)
    assert decision.has_recommendation
    assert decision.behavior.completion_evidence.source is EvidenceSource.POPULATION
    assert decision.behavior.observations == 0


def test_a_provider_returning_none_is_treated_as_empty_not_as_a_crash():
    decision = service(get_assignments=lambda uid: None).decide(1, NOW)
    assert decision.has_recommendation


# ── risk ─────────────────────────────────────────────────────────────


def test_work_due_within_two_days_is_surfaced_as_at_risk():
    rows = [{
        "id": "x", "title": "Lab report", "course": "Biology",
        "due_date": TODAY.isoformat(), "status": "not_started",
    }]
    decision = service(get_assignments=lambda uid: rows).decide(1, NOW)
    assert any(r["kind"] == "deadline" and r["title"] == "Lab report"
               for r in decision.at_risk)


def test_a_weak_concept_before_an_assessment_is_surfaced_as_at_risk():
    mastery = [MasteryEstimate(
        subject="Physics", topic="Newton", concept="Newton's Laws",
        mastery=0.4, confidence=0.7, days_since_review=3,
        assessment_days_away=2, risk=0.85,
    )]
    decision = service(get_mastery=lambda uid, today: mastery).decide(1, NOW)
    assert any(r["kind"] == "mastery" for r in decision.at_risk)


def test_strong_material_is_not_called_at_risk():
    mastery = [MasteryEstimate(
        subject="Physics", topic="Newton", concept="Newton's Laws",
        mastery=0.95, confidence=0.9, days_since_review=1,
        assessment_days_away=2, risk=0.1,
    )]
    decision = service(get_mastery=lambda uid, today: mastery).decide(1, NOW)
    assert not any(r["kind"] == "mastery" for r in decision.at_risk)


# ── serialisation ────────────────────────────────────────────────────


def test_the_payload_carries_the_reasons_and_the_arithmetic():
    payload = decision_to_dict(service().decide(1, NOW))
    assert payload["has_recommendation"] is True
    assert payload["headline"]["why"]
    assert payload["headline"]["reason_codes"]
    assert payload["headline"]["components"]
    assert payload["headline"]["block_id"]


def test_the_payload_is_json_safe():
    import json

    json.dumps(decision_to_dict(service().decide(1, NOW)))


# ── feasibility ──────────────────────────────────────────────────────


def test_the_service_can_answer_whether_the_current_plan_is_possible():
    clash = a_schedule(a_day(TODAY, [
        a_block("t1", hour=19, minutes=120),
        a_block("t2", hour=20, minutes=60),
    ]))
    report = service(get_active_schedule=lambda uid: clash).check(1)
    assert not report.feasible
    assert any(v.key == "overlap" for v in report.violations)


# ── plan adapter ─────────────────────────────────────────────────────


def test_a_saved_schedule_round_trips_into_a_plan():
    schedule = a_schedule(
        a_day(TODAY, [a_block("t1", minutes=45)]),
        a_day(TODAY + timedelta(days=1), [a_block("t2", minutes=60,
                                                  day=TODAY + timedelta(days=1))]),
    )
    plan = plan_from_schedule_data(schedule)
    assert [d.day for d in plan.days] == [TODAY, TODAY + timedelta(days=1)]
    assert plan.total_minutes == 105


def test_auto_breaks_do_not_survive_into_the_plan():
    schedule = a_schedule(a_day(TODAY, [
        a_block("t1", minutes=45),
        a_block("brk", minutes=15, hour=20, is_break=True),
    ]))
    plan = plan_from_schedule_data(schedule)
    assert plan.total_minutes == 45


def test_an_empty_schedule_becomes_an_empty_plan_not_an_error():
    assert plan_from_schedule_data(None).days == ()
    assert plan_from_schedule_data({}).days == ()


def test_a_multi_part_task_collapses_to_one_task_when_scored():
    schedule = a_schedule(a_day(TODAY, [
        a_block("t1", minutes=45, hour=17),
        a_block("t1", minutes=45, hour=19),
    ]))
    tasks = tasks_from_plan(plan_from_schedule_data(schedule))
    assert len(tasks) == 1
    assert tasks[0].est_minutes == 90


# ── override preview ─────────────────────────────────────────────────


def test_moving_work_off_today_reports_the_minutes_both_ways():
    schedule = a_schedule(
        a_day(TODAY, [a_block("t1", minutes=45)]),
        a_day(TODAY + timedelta(days=1), []),
    )
    outcome = preview_override(schedule, action="move", task_id="t1",
                               day=TODAY, today=TODAY)
    assert outcome is not None
    assert outcome.moved_minutes == 45
    assert outcome.target_day == TODAY + timedelta(days=1)
    assert outcome.report.summary


def test_moving_a_task_that_is_not_there_is_a_no_op_not_an_error():
    schedule = a_schedule(a_day(TODAY, [a_block("t1")]))
    assert preview_override(schedule, action="move", task_id="ghost",
                            day=TODAY, today=TODAY) is None


def test_an_unknown_override_action_is_refused():
    schedule = a_schedule(a_day(TODAY, [a_block("t1")]))
    assert preview_override(schedule, action="detonate", task_id="t1",
                            day=TODAY, today=TODAY) is None


def test_an_override_against_an_empty_plan_is_a_no_op():
    assert preview_override(None, action="move", task_id="t1",
                            day=TODAY, today=TODAY) is None
