"""Sizing tests.

The point of this module is that two assignments worth the same points are
not the same amount of work. Most of these tests assert *relative* size —
that is the property the product depends on, and the absolute rates are
priors the estimation model corrects per student.
"""

from __future__ import annotations

import pytest

from intelliplan.intelligence.sizing import (
    MAX_SIZED_MINUTES,
    MIN_SIZED_MINUTES,
    size_assignment,
    size_from_metadata,
    strip_markup,
)


# ── The failure this module exists to fix ─────────────────────────────


def test_an_essay_and_a_problem_set_worth_the_same_points_differ():
    essay = size_from_metadata(
        title="Research paper",
        kind="project",
        description="Write a 2,000-word researched essay with citations.",
        points_possible=50,
    )
    homework = size_from_metadata(
        title="Section 3.4",
        kind="homework",
        description="Complete problems 12-18.",
        points_possible=50,
    )
    assert essay.minutes > homework.minutes * 4
    assert essay.is_measured and homework.is_measured


def test_no_metadata_falls_back_to_the_old_behaviour():
    sized = size_from_metadata(title="Worksheet", kind="homework", points_possible=40)
    assert sized.basis == "points"
    assert not sized.signals
    assert sized.minutes > 0


def test_nothing_at_all_still_returns_a_usable_number():
    sized = size_from_metadata()
    assert sized.basis == "kind"
    assert MIN_SIZED_MINUTES <= sized.minutes <= MAX_SIZED_MINUTES


# ── Word counts ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "Write a 1500 word essay.",
        "Write a 1,500-word essay.",
        "Your essay should be 1500 words or more.",
    ],
)
def test_word_counts_are_read_in_the_spellings_teachers_use(text):
    sized = size_from_metadata(title="Essay", kind="project", description=text)
    assert sized.minutes >= 400
    assert "1,500 words" in sized.explanation


def test_a_word_range_takes_the_midpoint():
    low = size_from_metadata(kind="project", description="Write 500-700 words.")
    assert "600 words" in low.explanation


def test_page_counts_convert_to_words():
    pages = size_from_metadata(title="Essay", kind="project", description="Write a 4-page paper.")
    words = size_from_metadata(title="Essay", kind="project", description="Write 1100 words.")
    assert abs(pages.minutes - words.minutes) <= 10


def test_short_answers_cost_less_per_word_than_essays():
    essay = size_from_metadata(title="Essay", kind="project", description="Write 400 words.")
    short = size_from_metadata(
        title="Bell ringer", kind="classwork", description="Answer in 400 words."
    )
    assert short.minutes < essay.minutes


# ── Reading ───────────────────────────────────────────────────────────


def test_a_page_range_is_read_as_pages_not_as_a_length():
    sized = size_from_metadata(
        title="Chapter reading", kind="homework", description="Read pages 120-145."
    )
    assert 80 <= sized.minutes <= 100
    assert "26 pages" in sized.explanation


def test_note_taking_costs_more_than_reading_alone():
    plain = size_from_metadata(description="Read pages 120-145.")
    noted = size_from_metadata(description="Read pages 120-145 and take notes.")
    assert noted.minutes > plain.minutes


def test_reading_and_answering_questions_add_up():
    both = size_from_metadata(
        description="Read pages 120-145 and answer questions 1-10."
    )
    reading = size_from_metadata(description="Read pages 120-145.")
    assert both.minutes > reading.minutes


def test_a_chapter_with_no_page_range_is_still_sized():
    sized = size_from_metadata(description="Read chapter 7 before class.")
    assert sized.is_measured
    assert sized.minutes >= 60


# ── Problems ──────────────────────────────────────────────────────────


def test_a_problem_range_is_inclusive():
    """"Problems 12-18" is seven problems. The off-by-one is the difference
    between a plan and an argument."""
    sized = size_from_metadata(description="Do problems 12-18.")
    assert "7 problems" in sized.explanation


@pytest.mark.parametrize(
    "text",
    ["Complete #1-15.", "Answer questions 1 through 15.", "Do 15 problems."],
)
def test_problem_counts_are_read_in_several_spellings(text):
    assert "15 problems" in size_from_metadata(description=text).explanation


def test_more_problems_means_more_time():
    few = size_from_metadata(description="Do problems 1-5.")
    many = size_from_metadata(description="Do problems 1-40.")
    assert many.minutes > few.minutes * 3


def test_problems_become_split_boundaries():
    """The planner prefers to break at question boundaries over breaking at
    minute 34, but only if it knows the boundaries exist."""
    sized = size_from_metadata(description="Do problems 1-20.")
    assert sized.subtask_count == 20


def test_a_quiz_question_is_cheaper_than_a_homework_problem():
    quiz = size_from_metadata(
        description="20 questions.", submission_types=["online_quiz"]
    )
    homework = size_from_metadata(description="20 questions.")
    assert quiz.minutes < homework.minutes


# ── LMS-supplied facts ────────────────────────────────────────────────


def test_a_stated_time_limit_wins_over_everything():
    """A timed quiz is not an estimate. It is a fact, and guessing over the
    top of a fact is how a planner loses trust."""
    sized = size_from_metadata(
        title="Unit 4 quiz",
        description="Read pages 1-300 and write 5000 words.",
        time_limit_minutes=25,
    )
    assert sized.minutes == 25


def test_a_rubric_implies_a_multi_part_deliverable():
    plain = size_from_metadata(title="Presentation", kind="project", points_possible=30)
    rubbed = size_from_metadata(
        title="Presentation", kind="project", points_possible=30, rubric_rows=6
    )
    assert rubbed.minutes > plain.minutes
    assert rubbed.subtask_count == 6


def test_a_rubric_and_a_word_count_are_not_added_together():
    """They describe the same essay. Summing them double-counts the work."""
    sized = size_from_metadata(
        title="Essay",
        kind="project",
        description="Write a 2000-word essay.",
        rubric_rows=5,
    )
    words_only = size_from_metadata(
        title="Essay", kind="project", description="Write a 2000-word essay."
    )
    assert sized.minutes == words_only.minutes


def test_long_instructions_raise_a_floor_but_never_override():
    long_text = "You will need to " + "complete each step carefully. " * 30
    floored = size_from_metadata(title="Lab", kind="lab", description=long_text)
    assert floored.is_measured
    measured = size_from_metadata(
        title="Lab", kind="lab", description=long_text + " Write 3000 words."
    )
    assert measured.minutes > floored.minutes


# ── Robustness ────────────────────────────────────────────────────────


def test_html_descriptions_are_read_not_choked_on():
    sized = size_from_metadata(
        title="Essay",
        kind="project",
        description="<p>Write a <strong>1,200-word</strong> essay.</p>",
    )
    assert "1,200 words" in sized.explanation


def test_entities_survive_stripping():
    assert strip_markup("<p>Read pages 1&ndash;5</p>").startswith("Read pages 1")


def test_scripts_are_removed_with_their_contents():
    text = strip_markup("<script>var words = 9000;</script><p>Do problems 1-4.</p>")
    assert "9000" not in text


def test_absurd_numbers_are_clamped_not_trusted():
    sized = size_from_metadata(kind="project", description="Write 900000 words.")
    assert sized.minutes == MAX_SIZED_MINUTES


def test_a_page_reference_is_not_a_page_count():
    """"See page 7" is a location. Treating it as "write 7 pages" turned a
    two-minute instruction into a two-hour essay."""
    sized = size_from_metadata(description="Do problems 1-4. See page 7 for the table.")
    assert sized.minutes < 40


def test_the_dict_adapter_reads_every_key_spelling():
    sized = size_assignment(
        {
            "name": "Essay",
            "type": "project",
            "description": "Write 1000 words.",
            "points_possible": "30",
            "rubric": [{}, {}, {}],
        }
    )
    assert sized.is_measured
    assert sized.minutes >= 250


def test_the_adapter_survives_garbage():
    sized = size_assignment(
        {"title": None, "points_possible": "lots", "rubric": "nope", "time_limit": "x"}
    )
    assert MIN_SIZED_MINUTES <= sized.minutes <= MAX_SIZED_MINUTES
