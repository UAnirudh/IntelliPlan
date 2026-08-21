"""Next-Best-Action — generation, scoring, and the order it produces."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from intelliplan.domain.student import ActionKind, MasteryEstimate
from intelliplan.intelligence.behavior import build_behavior_model
from intelliplan.intelligence.nba import (
    REASON_LABELS,
    NBAContext,
    decide,
    generate,
    score,
)

NOW = datetime(2026, 8, 21, 18, 30)
TODAY = NOW.date()


def block(task_id, title, *, course="Physics", minutes=45, due_in=3,
          priority=60, kind="homework"):
    return {
        "id": f"b-{task_id}",
        "task_id": task_id,
        "assignment": title,
        "course": course,
        "duration_minutes": minutes,
        "due_date": (TODAY + timedelta(days=due_in)).isoformat(),
        "priority": priority,
        "kind": kind,
        "difficulty": "Medium",
    }


def ctx(**kwargs):
    base = dict(now=NOW, available_minutes=60, behavior=None)
    base.update(kwargs)
    return NBAContext(**base)


# ── generation ───────────────────────────────────────────────────────


def test_nothing_to_do_produces_no_recommendation():
    assert decide(ctx()) == []


def test_a_finished_block_is_not_offered_again():
    done = {**block("t1", "Physics set"), "done": True}
    assert generate(ctx(), planned_today=[done]) == []


def test_breaks_in_the_plan_are_not_offered_as_work():
    brk = {"id": "b9", "assignment": "Long break", "is_break": True,
           "duration_minutes": 15}
    assert generate(ctx(), planned_today=[brk]) == []


def test_work_due_today_that_the_plan_missed_is_still_offered():
    """The plan and reality diverge, and when they do the plan is wrong."""
    rows = [{
        "id": "t9", "title": "Spanish quiz prep", "course": "Spanish",
        "due_date": TODAY.isoformat(), "priority": 55, "est_minutes": 25,
        "status": "not_started",
    }]
    titles = [c.title for c in generate(ctx(), assignments=rows)]
    assert "Spanish quiz prep" in titles


def test_completed_assignments_are_never_offered():
    rows = [{
        "id": "t9", "title": "Done already", "due_date": TODAY.isoformat(),
        "status": "completed",
    }]
    assert generate(ctx(), assignments=rows) == []


def test_a_task_on_the_plan_is_not_duplicated_from_the_assignment_list():
    rows = [{
        "id": "t1", "title": "Physics set", "course": "Physics",
        "due_date": TODAY.isoformat(), "status": "not_started",
    }]
    actions = decide(ctx(), planned_today=[block("t1", "Physics set", due_in=0)],
                     assignments=rows)
    assert len([a for a in actions if a.candidate.task_id == "t1"]) == 1


def test_a_started_task_is_offered_as_continue_not_start():
    actions = decide(
        ctx(in_progress_task_ids=frozenset({"t1"})),
        planned_today=[block("t1", "Physics set")],
    )
    assert actions[0].candidate.kind is ActionKind.CONTINUE_TASK
    assert "already_started" in actions[0].reason_codes


# ── ranking ──────────────────────────────────────────────────────────


def test_an_overdue_task_outranks_one_due_next_week():
    actions = decide(ctx(), planned_today=[
        block("far", "Essay", course="English", due_in=8, priority=60),
        block("late", "Lab report", course="Biology", due_in=-1, priority=60),
    ])
    assert actions[0].candidate.task_id == "late"
    assert "overdue" in actions[0].reason_codes


def test_a_weak_concept_before_an_assessment_can_outrank_routine_homework():
    """The headline claim of the whole feature: better than sorting by
    deadline. Homework due sooner loses to a quiz the student is not ready
    for."""
    mastery = [MasteryEstimate(
        subject="Physics", topic="Newton's Laws", concept="Newton's Laws",
        mastery=0.35, confidence=0.7, days_since_review=5,
        assessment_days_away=2, risk=0.9,
    )]
    actions = decide(
        ctx(available_minutes=45),
        planned_today=[block("hw", "Reading questions", course="English",
                             due_in=1, priority=40)],
        mastery=mastery,
    )
    assert actions[0].candidate.kind is ActionKind.EXAM_PREP
    assert "assessment_near" in actions[0].reason_codes
    assert "low_mastery" in actions[0].reason_codes


def test_strong_material_with_no_assessment_is_not_recommended_at_all():
    mastery = [MasteryEstimate(
        subject="Physics", topic="Kinematics", concept="Kinematics",
        mastery=0.95, confidence=0.8, days_since_review=2,
        assessment_days_away=None, risk=0.05,
    )]
    assert generate(ctx(), mastery=mastery) == []


def test_a_long_stretch_of_work_makes_a_break_the_top_answer():
    actions = decide(
        ctx(continuous_minutes=200, worked_today_minutes=220),
        planned_today=[block("t1", "Physics set", due_in=1)],
    )
    assert actions[0].candidate.kind is ActionKind.BREAK
    assert "long_stretch" in actions[0].reason_codes


def test_a_short_stretch_does_not():
    actions = decide(
        ctx(continuous_minutes=40),
        planned_today=[block("t1", "Physics set", due_in=1)],
    )
    assert actions[0].candidate.kind is not ActionKind.BREAK


def test_work_that_fits_the_gap_beats_work_that_does_not():
    short_gap = ctx(available_minutes=30)
    fits = score(generate(short_gap, planned_today=[block("a", "A", minutes=25)])[0], short_gap)
    spills = score(generate(short_gap, planned_today=[block("b", "B", minutes=120)])[0], short_gap)
    assert fits.score > spills.score
    assert "fits_the_gap" in fits.reason_codes


def test_staying_in_the_same_subject_is_worth_something():
    same = ctx(current_course="Physics")
    other = ctx(current_course="History")
    candidate = generate(same, planned_today=[block("t1", "Physics set")])[0]
    assert score(candidate, same).score > score(candidate, other).score
    assert "same_subject" in score(candidate, same).reason_codes


# ── behaviour coupling ───────────────────────────────────────────────


def test_a_subject_the_student_abandons_scores_lower_than_one_they_finish():
    rows = (
        [{"actual": 45, "course": "Physics", "completed": True,
          "started_at": NOW - timedelta(days=i)} for i in range(15)]
        + [{"actual": 45, "course": "Chemistry", "completed": False,
            "started_at": NOW - timedelta(days=i)} for i in range(15)]
    )
    behavior = build_behavior_model(rows, now=NOW)
    c = ctx(behavior=behavior)

    physics = score(generate(c, planned_today=[block("p", "P", course="Physics")])[0], c)
    chemistry = score(generate(c, planned_today=[block("k", "K", course="Chemistry")])[0], c)

    assert physics.completion_probability > chemistry.completion_probability
    assert physics.score > chemistry.score
    assert "low_completion_odds" in chemistry.reason_codes


def test_an_already_loaded_day_is_flagged_as_over_capacity():
    behavior = build_behavior_model(
        [{"actual": 45, "completed": True, "started_at": NOW - timedelta(days=i)}
         for i in range(10)],
        daily_minutes={f"d{i}": 60 for i in range(10)},
        now=NOW,
    )
    action = decide(
        ctx(behavior=behavior, worked_today_minutes=400),
        planned_today=[block("t1", "Physics set")],
    )[0]
    assert "over_capacity" in action.reason_codes


# ── explanation contract ─────────────────────────────────────────────


def test_every_reason_code_has_a_sentence_a_student_can_read():
    actions = decide(ctx(), planned_today=[block("t1", "Physics set", due_in=0)])
    codes = actions[0].reason_codes
    assert codes
    for code in codes:
        assert code in REASON_LABELS, f"reason code {code!r} has no label"
    assert len(actions[0].explanation) == len(codes)


def test_the_components_sum_to_something_the_score_can_be_checked_against():
    """The explanation is the arithmetic, so the arithmetic has to be there."""
    action = decide(ctx(), planned_today=[block("t1", "Physics set")])[0]
    keys = {k for k, _ in action.components}
    assert {"deadline", "academic_value", "completion", "schedule_fit"} <= keys
    assert 0.0 <= action.score <= 1.0


def test_confidence_is_lower_for_a_student_we_know_nothing_about():
    known = build_behavior_model(
        [{"actual": 45, "course": "Physics", "completed": True,
          "started_at": NOW - timedelta(days=i)} for i in range(20)],
        now=NOW,
    )
    blind = decide(ctx(), planned_today=[block("t1", "P")])[0]
    informed = decide(ctx(behavior=known), planned_today=[block("t1", "P")])[0]
    assert informed.confidence > blind.confidence


def test_a_method_is_recommended_for_every_study_action():
    action = decide(ctx(), planned_today=[block("t1", "Physics set")])[0]
    assert action.method


# ── edge cases ───────────────────────────────────────────────────────


def test_no_time_left_today_does_not_crash_and_still_ranks():
    actions = decide(ctx(available_minutes=0),
                     planned_today=[block("t1", "Physics set", due_in=0)])
    assert actions
    assert "little_time_left" in actions[0].reason_codes


def test_malformed_blocks_are_skipped_rather_than_crashing():
    junk = ["not a dict", {}, {"assignment": ""}, None]
    actions = decide(ctx(), planned_today=junk)
    assert actions == []


def test_a_block_with_a_nonsense_duration_falls_back_to_a_sane_one():
    bad = {**block("t1", "Physics set"), "duration_minutes": "banana"}
    assert generate(ctx(), planned_today=[bad])[0].minutes == 30


def test_a_block_with_an_unparseable_due_date_is_treated_as_undated():
    bad = {**block("t1", "Physics set"), "due_date": "next tuesday"}
    candidate = generate(ctx(), planned_today=[bad])[0]
    assert candidate.due_date is None
