"""Exam revision, flashcard load, and telling a student why a week broke.

Each of these covers something the day allocator could not see. Exams were
arriving as a single task due on the exam date, so a unit test got one
sitting the night before. Flashcard reviews were invisible to the capacity
model even though FSRS knows exactly when every card falls due. And a week
that did not fit came back as a count of dropped blocks, which is not
something anybody can act on.
"""

from datetime import date, datetime, timedelta

import pytest

import scheduler_depth as depth


# ── Exam detection ────────────────────────────────────────────────

@pytest.mark.parametrize("title", [
    "Unit 4 exam", "AP Bio midterm", "Chapter 3 quiz Friday",
    "Final exam - Calculus", "Spanish test",
])
def test_exam_titles_are_recognised(title):
    assert depth.looks_like_exam({"title": title})


@pytest.mark.parametrize("title", [
    "Review chapter 4", "Essay draft", "Lab report", "Read pages 40-60",
])
def test_ordinary_homework_is_not_treated_as_an_exam(title):
    """'Review chapter 4' is homework. Treating it as an exam would triple it."""
    assert not depth.looks_like_exam({"title": title})


def test_an_explicit_type_beats_the_title():
    assert depth.looks_like_exam({"title": "Chapter 9", "type": "exam"})


# ── Revision ladder ───────────────────────────────────────────────

def test_a_distant_exam_gets_several_spaced_passes():
    today = date(2026, 3, 1)
    sittings = depth.revision_plan({"title": "Unit 4 exam"}, date(2026, 3, 15), today)
    assert len(sittings) == 4
    days = [date.fromisoformat(s["preferred_date"]) for s in sittings]
    assert days == sorted(days)
    assert all(d < date(2026, 3, 15) for d in days)
    # Spread out, not stacked: every pass lands on a different day.
    assert len(set(days)) == len(days)


def test_a_near_exam_gets_only_the_passes_that_fit():
    today = date(2026, 3, 1)
    sittings = depth.revision_plan({"title": "quiz"}, date(2026, 3, 4), today)
    assert 1 <= len(sittings) <= 2
    assert all(date.fromisoformat(s["preferred_date"]) >= today for s in sittings)


def test_an_exam_today_or_in_the_past_generates_nothing():
    today = date(2026, 3, 10)
    assert depth.revision_plan({"title": "final"}, date(2026, 3, 10), today) == []
    assert depth.revision_plan({"title": "final"}, date(2026, 3, 1), today) == []


def test_the_last_pass_is_shorter_and_says_what_to_do():
    sittings = depth.revision_plan({"title": "Unit 4 exam"}, date(2026, 3, 15), date(2026, 3, 1))
    assert sittings[-1]["est_minutes"] < sittings[0]["est_minutes"]
    # A revision block with no instruction becomes an hour of rereading.
    assert "recall" in sittings[-1]["what_to_do"].lower()
    assert sittings[0]["what_to_do"]


def test_expanding_exams_keeps_the_exam_and_adds_its_preparation():
    tasks = [
        {"title": "Unit 4 exam", "due_date": "2026-03-15", "est_minutes": 60},
        {"title": "History essay", "due_date": "2026-03-10", "est_minutes": 90},
    ]
    out, plans = depth.expand_exams(tasks, date(2026, 3, 1))
    assert len(plans) == 1
    assert any(t["title"] == "Unit 4 exam" for t in out)
    assert any(t.get("is_revision") for t in out)
    assert len(out) > len(tasks)


def test_an_exam_with_no_date_is_left_alone():
    out, plans = depth.expand_exams([{"title": "final exam"}], date(2026, 3, 1))
    assert plans == [] and len(out) == 1


def test_a_term_full_of_exams_does_not_flood_the_week():
    tasks = [{"title": f"exam {i}", "due_date": "2026-04-01"} for i in range(20)]
    out, plans = depth.expand_exams(tasks, date(2026, 3, 1))
    assert len(plans) <= 6


# ── Flashcard load ────────────────────────────────────────────────

def test_due_cards_become_minutes():
    assert depth.review_minutes_by_day({"2026-03-02": 120})["2026-03-02"] == 20


def test_review_time_comes_out_of_the_day_it_falls_on():
    capacity = {"2026-03-02": 180, "2026-03-03": 180}
    remaining, reserved = depth.reserve_review_time(capacity, {"2026-03-02": 120})
    assert reserved["2026-03-02"] == 20
    assert remaining["2026-03-02"] == 160
    assert remaining["2026-03-03"] == 180


def test_reviews_can_never_eat_the_whole_day():
    """A 900-card backlog must not leave a student with no time for the essay
    that is actually due."""
    remaining, reserved = depth.reserve_review_time({"2026-03-02": 120}, {"2026-03-02": 900})
    assert reserved["2026-03-02"] <= 120 * depth.MAX_REVIEW_SHARE
    assert remaining["2026-03-02"] > 0


def test_a_day_with_no_cards_keeps_all_its_time():
    remaining, reserved = depth.reserve_review_time({"2026-03-02": 90}, {})
    assert remaining["2026-03-02"] == 90 and reserved["2026-03-02"] == 0


# ── Shortfall ─────────────────────────────────────────────────────

def test_a_week_that_fits_says_so():
    tasks = [{"title": "Essay", "est_minutes": 60}]
    placed = {"2026-03-02": [{"duration_minutes": 60}]}
    report = depth.diagnose(tasks, {"2026-03-02": 120}, placed, [])
    assert report.fits and report.missing_minutes == 0


def test_a_short_week_is_quantified_not_counted():
    tasks = [{"title": "Essay", "est_minutes": 180}, {"title": "Lab", "est_minutes": 120}]
    placed = {"2026-03-02": [{"duration_minutes": 120}]}
    unplaced = [{"title": "Essay", "duration_minutes": 130}]
    report = depth.diagnose(tasks, {"2026-03-02": 120, "2026-03-03": 60}, placed, unplaced)
    assert not report.fits
    assert report.missing_minutes == 130
    assert "2h 10m" in report.message
    assert report.unplaced_titles == ["Essay"]


def test_the_diagnosis_points_at_the_day_with_room():
    tasks = [{"title": "Essay", "est_minutes": 200}]
    placed = {"2026-03-02": [{"duration_minutes": 60}], "2026-03-07": []}
    unplaced = [{"title": "Essay", "duration_minutes": 140}]
    report = depth.diagnose(
        tasks, {"2026-03-02": 60, "2026-03-07": 240}, placed, unplaced)
    assert report.roomiest_days and report.roomiest_days[0][0] == "2026-03-07"
    assert any("Saturday" in s for s in report.suggestions)


def test_a_wildly_overloaded_week_says_to_cut_work_not_shuffle_it():
    tasks = [{"title": f"Task {i}", "est_minutes": 120} for i in range(10)]
    unplaced = [{"title": f"Task {i}", "duration_minutes": 120} for i in range(8)]
    report = depth.diagnose(tasks, {"2026-03-02": 120}, {"2026-03-02": []}, unplaced)
    assert any("next week or be cut" in s for s in report.suggestions)


def test_the_report_serialises_for_the_page():
    report = depth.diagnose([{"est_minutes": 30}], {"2026-03-02": 60},
                            {"2026-03-02": [{"duration_minutes": 30}]}, [])
    body = report.as_dict()
    assert body["fits"] is True
    assert set(body) >= {"demand_minutes", "capacity_minutes", "message", "suggestions"}
