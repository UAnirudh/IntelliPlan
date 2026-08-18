"""Priority adapter tests.

The scheduler and the Command Center now answer "how important is this?" the
same way. These tests pin the conversion from loose ingest dicts, and the one
piece of judgement in it: the student's own label counts, but does not win
outright.
"""

from __future__ import annotations

from datetime import date, timedelta

from intelliplan.domain.assignment import AssignmentKind, AssignmentStatus
from intelliplan.services.prioritisation import (
    assignment_from_row,
    score_rows,
)

TODAY = date(2026, 3, 2)


def row(**over):
    base = {
        "id": "a1",
        "title": "Problem set 4",
        "course": "Math",
        "due_date": (TODAY + timedelta(days=5)).isoformat(),
        "estimated_time": 60,
        "points_possible": 20,
        "priority": "Medium",
    }
    base.update(over)
    return base


# ── Typing the dict ───────────────────────────────────────────────────


def test_a_declared_kind_beats_a_guess_from_the_title():
    """A Canvas assignment group or a StudentVue category is a teacher's own
    classification. Guessing from the title is the fallback, not the rule."""
    typed = assignment_from_row(row(title="Chapter 4", category="Test"), TODAY)
    assert typed.kind is AssignmentKind.TEST


def test_the_title_is_used_when_nothing_was_declared():
    assert assignment_from_row(row(title="Unit 2 Final Exam"), TODAY).kind is AssignmentKind.EXAM


def test_work_in_progress_is_not_reported_as_untouched():
    typed = assignment_from_row(row(done_minutes=30), TODAY)
    assert typed.status is AssignmentStatus.IN_PROGRESS


def test_a_bad_due_date_becomes_no_due_date_rather_than_an_error():
    assert assignment_from_row(row(due_date="soon-ish"), TODAY).due_date is None


def test_garbage_numbers_do_not_raise():
    typed = assignment_from_row(row(points_possible="lots", estimated_time="ages"), TODAY)
    assert typed.points_possible == 0.0
    assert typed.est_minutes == 0


# ── Scoring ───────────────────────────────────────────────────────────


def test_an_overdue_exam_outranks_a_distant_worksheet():
    scores = score_rows(
        [
            row(id="exam", title="Midterm exam", category="Exam", points_possible=100,
                due_date=(TODAY - timedelta(days=1)).isoformat()),
            row(id="ws", title="Worksheet", points_possible=5,
                due_date=(TODAY + timedelta(days=13)).isoformat()),
        ],
        TODAY,
    )
    assert scores["exam"].score > scores["ws"].score


def test_the_score_carries_its_reasons():
    scores = score_rows([row(due_date=TODAY.isoformat())], TODAY)
    assert any("Due today" in r for r in scores["a1"].reasons)


def test_the_students_label_moves_the_score():
    high = score_rows([row(id="h", priority="High")], TODAY)["h"].score
    mid = score_rows([row(id="m", priority="Medium")], TODAY)["m"].score
    low = score_rows([row(id="l", priority="Low")], TODAY)["l"].score
    assert high > mid > low


def test_the_students_label_is_reported_as_a_reason():
    scores = score_rows([row(priority="High")], TODAY)
    assert any("high priority" in r for r in scores["a1"].reasons)


def test_a_low_label_does_not_bury_an_overdue_exam():
    """The label is a nudge, not a veto. Marking a midterm low priority should
    not push it behind a worksheet."""
    scores = score_rows(
        [
            row(id="exam", title="Midterm exam", category="Exam", priority="Low",
                points_possible=100, due_date=(TODAY - timedelta(days=2)).isoformat()),
            row(id="ws", title="Worksheet", priority="High", points_possible=5,
                due_date=(TODAY + timedelta(days=12)).isoformat()),
        ],
        TODAY,
    )
    assert scores["exam"].score > scores["ws"].score


def test_exam_prep_is_recognised_from_the_rest_of_the_list():
    """The dependency component is a property of the whole set, not of one
    row — it needs to know an exam is coming in the same course."""
    rows = [
        row(id="exam", title="Biology test", category="Test", course="Biology",
            due_date=(TODAY + timedelta(days=4)).isoformat()),
        row(id="prep", title="Biology review guide", course="Biology",
            due_date=(TODAY + timedelta(days=3)).isoformat()),
    ]
    with_exam = score_rows(rows, TODAY)["prep"].score
    without_exam = score_rows([rows[1]], TODAY)["prep"].score
    assert with_exam > without_exam


def test_scores_stay_in_range():
    scores = score_rows(
        [row(id="x", title="Final exam", category="Exam", priority="High",
             points_possible=500, due_date=(TODAY - timedelta(days=30)).isoformat())],
        TODAY,
    )
    assert 0 <= scores["x"].score <= 100


def test_an_untypeable_row_does_not_cost_the_others_their_scores():
    scores = score_rows([row(id="good"), {"id": "bad", "due_date": object()}], TODAY)
    assert "good" in scores


def test_no_rows_is_not_an_error():
    assert score_rows([], TODAY) == {}


def test_rows_are_never_given_each_others_labels():
    """A skipped row used to shift every later pairing by one."""
    rows = [
        {"id": "skipped"},  # no title, no key material beyond the id
        row(id="high", priority="High"),
        row(id="low", priority="Low"),
    ]
    scores = score_rows(rows, TODAY)
    assert scores["high"].score > scores["low"].score
