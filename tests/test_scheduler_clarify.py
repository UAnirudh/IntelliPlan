"""Tests for vague-request detection, clarifying questions, and presets."""

from datetime import date

import pytest

from scheduler_clarify import (
    MAX_QUESTIONS,
    answers_from_presets,
    apply_answers,
    assess_tasks,
    is_generic_course,
    is_generic_title,
    parse_deadline_answer,
    parse_duration_answer,
    preset_key,
    preset_payload,
)


def task(title="Essay on Hamlet", course="English", est=60, due="2026-08-03"):
    return {"title": title, "course": course, "estimated_time": est, "due_date": due}


def fields(qs):
    return [q.field for q in qs]


# ── generic detection ─────────────────────────────────────────────


@pytest.mark.parametrize("t", ["Study", "study", " STUDY ", "homework", "hw",
                               "work on stuff", "Review", "practice", "notes", ""])
def test_activity_words_are_generic(t):
    assert is_generic_title(t) is True


@pytest.mark.parametrize("t", ["Study ch. 7 vectors", "Hamlet essay draft",
                               "Lab report: titration", "Read Gatsby ch 3-5"])
def test_real_work_is_not_generic(t):
    assert is_generic_title(t) is False


def test_a_verb_with_an_object_is_specific_enough():
    # "study" alone is vague; "study biology" names the thing.
    assert is_generic_title("study") is True
    assert is_generic_title("study biology") is False


@pytest.mark.parametrize("c", ["", "General Academics", "school", "misc", "N/A", "-"])
def test_placeholder_courses_are_generic(c):
    assert is_generic_course(c) is True


def test_a_real_course_is_not_generic():
    assert is_generic_course("AP Calculus") is False


def test_preset_key_normalizes_case_and_spacing():
    assert preset_key("  Study  ") == preset_key("study") == "study"


def test_preset_key_strips_punctuation():
    assert preset_key("Study!!") == "study"


# ── assessment ────────────────────────────────────────────────────


def test_a_fully_specified_task_raises_no_questions():
    assert assess_tasks([task()]) == []


def test_no_tasks_raises_no_questions():
    assert assess_tasks([]) == []


def test_the_screenshot_case_is_caught():
    # "Study" / "General Academics" — exactly what shipped a useless block.
    qs = assess_tasks([{"title": "Study", "course": "General Academics",
                        "estimated_time": 20, "due_date": "2026-08-01"}])
    assert "deliverable" in fields(qs)
    assert "subject" in fields(qs)


def test_missing_duration_is_asked_about():
    qs = assess_tasks([task(est=0)])
    assert fields(qs) == ["duration"]


def test_missing_deadline_is_asked_about():
    qs = assess_tasks([task(due="")])
    assert fields(qs) == ["deadline"]


def test_questions_are_capped():
    qs = assess_tasks([{"title": "Study", "course": "", "estimated_time": 0, "due_date": ""}])
    assert len(qs) <= MAX_QUESTIONS


def test_questions_spread_across_tasks_before_stacking():
    # Three vague tasks: ask one thing about each, not three about the first.
    vague = [{"title": t, "course": "", "estimated_time": 0, "due_date": ""}
             for t in ("Study", "Homework", "Review")]
    qs = assess_tasks(vague)
    assert len({q.task for q in qs}) == 3


def test_known_courses_are_offered_as_options():
    tasks = [task(course="AP Biology"), {"title": "Study", "course": "",
                                         "estimated_time": 30, "due_date": "2026-08-01"}]
    qs = assess_tasks(tasks)
    subject_q = next(q for q in qs if q.field == "subject")
    assert "AP Biology" in subject_q.options


def test_a_generic_course_is_never_offered_as_an_option():
    tasks = [{"title": "A", "course": "General Academics", "estimated_time": 30, "due_date": "2026-08-01"},
             {"title": "Study", "course": "", "estimated_time": 30, "due_date": "2026-08-01"}]
    qs = assess_tasks(tasks)
    subject_q = next(q for q in qs if q.field == "subject")
    assert "General Academics" not in subject_q.options


def test_question_ids_are_stable_across_calls():
    a = assess_tasks([task(title="Study", course="", est=30, due="2026-08-01")])
    b = assess_tasks([task(title="study", course="", est=30, due="2026-08-01")])
    assert [q.id for q in a] == [q.id for q in b]


def test_question_to_dict_is_json_shaped():
    q = assess_tasks([task(est=0)])[0].to_dict()
    assert set(q) == {"id", "task", "field", "question", "why",
                      "options", "allow_free_text", "placeholder"}
    assert isinstance(q["options"], list)


# ── duration parsing ──────────────────────────────────────────────


@pytest.mark.parametrize("text,expected", [
    ("45 min", 45), ("45min", 45), ("30 minutes", 30),
    ("1 hour", 60), ("1.5 hours", 90), ("2h", 120), ("2 hrs", 120),
    ("90", 90),          # bare, >=10 -> minutes
    ("2", 120),          # bare, <10 -> hours
])
def test_duration_answers_parse(text, expected):
    assert parse_duration_answer(text) == expected


@pytest.mark.parametrize("bad", ["", None, "soon", "a while"])
def test_unparseable_duration_falls_back(bad):
    assert parse_duration_answer(bad, default=42) == 42


def test_duration_is_clamped_to_something_sane():
    assert parse_duration_answer("900 hours") == 600
    assert parse_duration_answer("1 min") == 5


# ── applying answers ──────────────────────────────────────────────


def test_answers_rewrite_the_task():
    tasks = [{"title": "Study", "course": "General Academics",
              "estimated_time": 20, "due_date": ""}]
    out = apply_answers(tasks, {
        "study::deliverable": "Finish ch.7 vector problems",
        "study::subject": "AP Physics",
        "study::duration": "1.5 hours",
    })
    assert out[0]["title"] == "Finish ch.7 vector problems"
    assert out[0]["original_title"] == "Study"
    assert out[0]["course"] == "AP Physics"
    assert out[0]["estimated_time"] == 90


def test_apply_answers_does_not_mutate_the_input():
    tasks = [{"title": "Study", "course": "", "estimated_time": 20}]
    apply_answers(tasks, {"study::subject": "Math"})
    assert tasks[0]["course"] == ""


def test_a_partial_answer_set_still_helps():
    tasks = [{"title": "Study", "course": "", "estimated_time": 20, "due_date": ""}]
    out = apply_answers(tasks, {"study::subject": "Math"})
    assert out[0]["course"] == "Math"
    assert out[0]["title"] == "Study"      # untouched


def test_blank_answers_are_ignored():
    tasks = [{"title": "Study", "course": "Bio", "estimated_time": 20}]
    out = apply_answers(tasks, {"study::subject": "   "})
    assert out[0]["course"] == "Bio"


def test_no_answers_returns_copies():
    tasks = [{"title": "A", "course": "B"}]
    out = apply_answers(tasks, {})
    assert out == tasks and out[0] is not tasks[0]


# ── deadlines ─────────────────────────────────────────────────────


MONDAY = date(2026, 7, 27)


@pytest.mark.parametrize("text,expected", [
    ("Today", "2026-07-27"),
    ("Tomorrow", "2026-07-28"),
    ("This week", "2026-07-31"),      # upcoming Friday
    ("Next week", "2026-08-07"),      # Friday of next week
    ("Friday", "2026-07-31"),
    ("Wednesday", "2026-07-29"),
    ("2026-08-03", "2026-08-03"),
    ("due 8/3/2026", ""),             # ambiguous order, not guessed at
])
def test_deadline_answers_resolve_to_dates(text, expected):
    assert parse_deadline_answer(text, today=MONDAY) == expected


@pytest.mark.parametrize("text", ["No deadline", "none", "", None, "whenever"])
def test_no_deadline_resolves_to_empty(text):
    assert parse_deadline_answer(text, today=MONDAY) == ""


def test_a_weekday_answer_never_resolves_to_today():
    # "Monday" said on a Monday means next Monday, not this morning.
    assert parse_deadline_answer("Monday", today=MONDAY) == "2026-08-03"


def test_answering_a_deadline_sets_a_real_due_date():
    out = apply_answers([{"title": "Study", "due_date": ""}],
                        {"study::deadline": "Tomorrow"}, today=MONDAY)
    assert out[0]["due_date"] == "2026-07-28"


# ── ask once, then move on ────────────────────────────────────────


def test_an_unusable_deadline_answer_is_not_asked_again():
    """"No deadline" is a real answer; re-asking it would loop forever."""
    tasks = [{"title": "Essay", "course": "English", "estimated_time": 60, "due_date": ""}]
    applied = apply_answers(tasks, {"essay::deadline": "No deadline"}, today=MONDAY)
    assert "deadline" not in fields(assess_tasks(applied))


def test_a_vague_deliverable_answer_is_not_asked_again():
    tasks = [{"title": "Study", "course": "Math", "estimated_time": 60, "due_date": "2026-08-01"}]
    applied = apply_answers(tasks, {"study::deliverable": "stuff"})
    assert "deliverable" not in fields(assess_tasks(applied))


def test_answering_everything_leaves_nothing_to_ask():
    tasks = [{"title": "Study", "course": "", "estimated_time": 0, "due_date": ""}]
    answers = {"study::deliverable": "Redo the failed quiz", "study::subject": "Chemistry",
               "study::duration": "45 min", "study::deadline": "Friday"}
    assert assess_tasks(apply_answers(tasks, answers, today=MONDAY)) == []


def test_question_ids_survive_the_deliverable_rename():
    """Renaming the task must not orphan answers already given against it."""
    tasks = [{"title": "Study", "course": "", "estimated_time": 0, "due_date": ""}]
    applied = apply_answers(tasks, {"study::deliverable": "Redo the failed quiz"})
    for q in assess_tasks(applied):
        assert q.id.startswith("study::"), q.id


# ── presets ───────────────────────────────────────────────────────


def test_preset_payload_groups_answers_by_task():
    qs = assess_tasks([{"title": "Study", "course": "", "estimated_time": 0, "due_date": "2026-08-01"}])
    answers = {q.id: "Math" if q.field == "subject" else "45 min" for q in qs}
    payload = preset_payload(qs, answers)
    assert "study" in payload
    assert payload["study"]["label"] == "Study"
    assert payload["study"]["answers"]


def test_preset_payload_skips_unanswered_questions():
    qs = assess_tasks([{"title": "Study", "course": "", "estimated_time": 0, "due_date": "2026-08-01"}])
    assert preset_payload(qs, {}) == {}


def test_saved_presets_expand_into_answers_for_matching_tasks():
    presets = {"study": {"label": "Study", "answers": {"subject": "Math", "duration": "45 min"}}}
    out = answers_from_presets([{"title": "Study"}], presets)
    assert out == {"study::subject": "Math", "study::duration": "45 min"}


def test_presets_for_tasks_not_in_this_request_are_ignored():
    presets = {"homework": {"label": "Homework", "answers": {"subject": "Math"}}}
    assert answers_from_presets([{"title": "Study"}], presets) == {}


def test_a_preset_round_trips_through_apply_answers():
    presets = {"study": {"label": "Study", "answers": {
        "deliverable": "Redo the failed quiz", "subject": "Chemistry", "duration": "1 hour"}}}
    tasks = [{"title": "Study", "course": "", "estimated_time": 0}]
    out = apply_answers(tasks, answers_from_presets(tasks, presets))
    assert out[0]["title"] == "Redo the failed quiz"
    assert out[0]["course"] == "Chemistry"
    assert out[0]["estimated_time"] == 60
