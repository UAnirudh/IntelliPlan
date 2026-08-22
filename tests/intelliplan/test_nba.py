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


# ── the same work must never appear twice ────────────────────────────


def test_the_same_work_from_two_ingest_paths_is_shown_once():
    """The defect that reached a browser.

    The plan block and the assignment row for one Physics set carry ids
    minted by different ingest paths, so an id-only dedupe showed it as the
    headline *and* as the first thing after it.
    """
    plan_block = block("t-physics", "Physics problem set", course="Physics", due_in=1)
    same_work = {
        "id": "manual:412",              # different id, same real assignment
        "title": "Physics problem set",
        "course": "Physics",
        "due_date": (TODAY + timedelta(days=1)).isoformat(),
        "priority": 80, "est_minutes": 45, "status": "not_started",
    }
    actions = decide(ctx(), planned_today=[plan_block], assignments=[same_work])
    titles = [a.candidate.title for a in actions]
    assert titles.count("Physics problem set") == 1


def test_matching_titles_in_different_courses_are_kept_apart():
    """"Chapter 3 questions" exists in half a student's subjects. Course is
    what tells them apart, so the dedupe must not collapse them."""
    actions = decide(ctx(available_minutes=180), planned_today=[
        block("a", "Chapter 3 questions", course="Physics", due_in=1),
        block("b", "Chapter 3 questions", course="History", due_in=1),
    ])
    assert len(actions) == 2


def test_a_break_is_never_deduped_against_work():
    actions = decide(
        ctx(continuous_minutes=200),
        planned_today=[block("t1", "Take a break", course="", due_in=1)],
    )
    kinds = {a.candidate.kind for a in actions}
    assert ActionKind.BREAK in kinds


# ── reason ordering ──────────────────────────────────────────────────


def test_the_deadline_leads_ahead_of_schedule_mechanics():
    """"There is not much of today's study time left" outranked "this is due
    tomorrow" purely because the scorer computed it first. Ordering by how
    the arithmetic runs is not ordering."""
    action = decide(
        ctx(available_minutes=0),
        planned_today=[block("t1", "Physics set", due_in=1, priority=90)],
    )[0]
    codes = list(action.reason_codes)
    assert "due_tomorrow" in codes
    assert "little_time_left" in codes
    assert codes.index("due_tomorrow") < codes.index("little_time_left")


def test_overdue_beats_every_other_reason():
    action = decide(
        ctx(),
        planned_today=[block("t1", "Late lab", due_in=-2, priority=90)],
    )[0]
    assert action.reason_codes[0] == "overdue"


def test_caveats_never_lead():
    action = decide(
        ctx(worked_today_minutes=900),
        planned_today=[block("t1", "Physics set", due_in=0)],
    )[0]
    caveats = {"over_capacity", "low_completion_odds", "little_time_left"}
    assert action.reason_codes[0] not in caveats


def test_the_explanation_follows_the_same_order_as_the_codes():
    action = decide(
        ctx(available_minutes=0),
        planned_today=[block("t1", "Physics set", due_in=1)],
    )[0]
    expected = [REASON_LABELS[c] for c in action.reason_codes if c in REASON_LABELS]
    assert list(action.explanation) == expected


def test_every_reason_code_has_an_explicit_rank():
    """An unranked code sorts to a default and is easy to miss. Keep the two
    tables in step."""
    from intelliplan.intelligence.nba import _REASON_RANK

    assert set(REASON_LABELS) == set(_REASON_RANK)


# ── "not now" has to mean not now ────────────────────────────────────


def test_declined_work_is_not_re_offered_the_same_day():
    """Re-offering what the student just pushed away is the app arguing
    with them, which is exactly what the override flow exists to avoid."""
    plan = [block("t1", "Physics set", due_in=2), block("t2", "Essay",
                                                        course="English", due_in=3)]
    kept = decide(ctx(dismissed_task_ids=frozenset({"t1"})), planned_today=plan)
    assert [a.candidate.task_id for a in kept] == ["t2"]


def test_overdue_work_comes_back_even_after_being_declined():
    """Declining something does not make its deadline go away. Silently
    dropping overdue work would be the scheduler helping them miss it."""
    plan = [block("t1", "Late lab", due_in=-1)]
    kept = decide(ctx(dismissed_task_ids=frozenset({"t1"})), planned_today=plan)
    assert [a.candidate.task_id for a in kept] == ["t1"]
    assert "overdue" in kept[0].reason_codes


def test_work_due_today_can_still_be_declined():
    """Due today is urgent, not yet overdue. The student is allowed to say
    they will do it later this evening."""
    plan = [block("t1", "Physics set", due_in=0)]
    assert decide(ctx(dismissed_task_ids=frozenset({"t1"})), planned_today=plan) == []


def test_no_dismissals_changes_nothing():
    plan = [block("t1", "Physics set", due_in=2)]
    assert len(decide(ctx(), planned_today=plan)) == 1


def test_declining_work_sticks_even_when_two_sources_describe_it():
    """The bug that survived the first fix.

    The plan block and the assignment row are the same Physics set with
    different ids. Filtering dismissals before deduping removed the plan
    candidate and left the assignment one, so the declined work came back
    wearing a different id.
    """
    plan_block = block("t-physics", "Physics problem set", course="Physics", due_in=2)
    same_work = {
        "id": "manual:412", "title": "Physics problem set", "course": "Physics",
        "due_date": (TODAY + timedelta(days=2)).isoformat(),
        "priority": 80, "est_minutes": 45, "status": "not_started",
    }
    # The student declined whatever the card showed them, which is the
    # top-ranked candidate after deduping.
    shown = decide(ctx(), planned_today=[plan_block], assignments=[same_work])
    dismissed = frozenset({shown[0].candidate.task_id})

    after = decide(
        ctx(dismissed_task_ids=dismissed),
        planned_today=[plan_block], assignments=[same_work],
    )
    assert [a.candidate.title for a in after] == []
