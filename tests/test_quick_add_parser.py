"""The quick-add parser: "bio lab due fri 2h" → a task, with no model.

Table-driven, because the parser *is* a table of decisions: what "fri" means
on a Friday, whether "10-14" is a date or a problem range, which course "bio"
is when there are two. Each row pins one decision so a later "improvement"
that quietly changes it fails here instead of in a student's planner.

The clock is always pinned. ``NOW`` is Thursday 1 October 2026, 15:00 in the
student's own timezone; the timezone rows at the bottom build that local
clock from a UTC instant through ``time_utils.local_now``, which is what the
endpoints do.
"""

from datetime import date, datetime, time

import pytest

from intelliplan.intelligence.quick_add import (
    course_aliases, infer_kind, parse_quick_add, planner_kind,
)
from time_utils import local_now, valid_timezone

NOW = datetime(2026, 10, 1, 15, 0)  # a Thursday
COURSES = [
    "AP Biology",
    "Chemistry Honors - Period 3",
    "AP US History",
    "English 10",
    "Algebra II",
    "Spanish III",
    "Physics",
]


def parse(text, now=NOW, courses=COURSES):
    return parse_quick_add(text, now=now, courses=courses)


# ── Dates ─────────────────────────────────────────────────────────────

DATE_CASES = [
    # relative words and their spellings
    ("read ch 4 today", date(2026, 10, 1)),
    ("vocab tonight", date(2026, 10, 1)),
    ("quiz tomorrow", date(2026, 10, 2)),
    ("quiz tmrw", date(2026, 10, 2)),
    ("quiz tmr", date(2026, 10, 2)),
    ("quiz tmw", date(2026, 10, 2)),
    ("quiz tomo", date(2026, 10, 2)),
    ("quiz 2moro", date(2026, 10, 2)),
    ("quiz day after tomorrow", date(2026, 10, 3)),
    # bare weekdays are the next one, never today
    ("quiz fri", date(2026, 10, 2)),
    ("quiz friday", date(2026, 10, 2)),
    ("quiz sat", date(2026, 10, 3)),
    ("quiz mon", date(2026, 10, 5)),
    ("quiz tues", date(2026, 10, 6)),
    ("quiz weds", date(2026, 10, 7)),
    ("quiz thu", date(2026, 10, 8)),        # Thursday said on a Thursday
    ("quiz thurs", date(2026, 10, 8)),
    ("quiz Thursday", date(2026, 10, 8)),
    ("quiz FRI", date(2026, 10, 2)),         # case-insensitive
    ("quiz fri.", date(2026, 10, 2)),
    # this / next
    ("quiz this thu", date(2026, 10, 1)),
    ("quiz this fri", date(2026, 10, 2)),
    ("quiz next tue", date(2026, 10, 6)),    # Tuesday of next calendar week
    ("quiz next fri", date(2026, 10, 9)),
    ("quiz next mon", date(2026, 10, 5)),
    ("essay next week", date(2026, 10, 9)),  # Friday of next week
    ("essay this weekend", date(2026, 10, 3)),
    # in N
    ("project in 3 days", date(2026, 10, 4)),
    ("project in a week", date(2026, 10, 8)),
    ("project in 2 weeks", date(2026, 10, 15)),
    ("project due in two days", date(2026, 10, 3)),
    # numeric
    ("essay 10/14", date(2026, 10, 14)),
    ("essay due 10/14", date(2026, 10, 14)),
    ("essay 10/14/26", date(2026, 10, 14)),
    ("essay 10/14/2027", date(2027, 10, 14)),
    ("essay 2026-11-03", date(2026, 11, 3)),
    ("lab 9/28", date(2026, 9, 28)),         # 3 days ago: stays (overdue capture)
    ("lab 1/5", date(2027, 1, 5)),           # long gone this year: next year
    ("lab 9/1", date(2027, 9, 1)),
    # month names
    ("quiz oct 14", date(2026, 10, 14)),
    ("quiz October 14th", date(2026, 10, 14)),
    ("quiz 14 oct", date(2026, 10, 14)),
    ("quiz 3rd of november", date(2026, 11, 3)),
    ("quiz dec 1, 2027", date(2027, 12, 1)),
    ("quiz sept 30", date(2026, 9, 30)),
    ("quiz jan 10", date(2027, 1, 10)),
    # ordinal day of month
    ("review the 14th", date(2026, 10, 14)),
    ("review the 1st", date(2026, 10, 1)),   # today is the 1st
    ("review the 31st", date(2026, 10, 31)),
    # lead-in words are consumed with the date
    ("essay by fri", date(2026, 10, 2)),
    ("essay due on fri", date(2026, 10, 2)),
    ("essay before mon", date(2026, 10, 5)),
]


@pytest.mark.parametrize("text,expected", DATE_CASES)
def test_dates(text, expected):
    assert parse(text).due_date == expected


NO_DATE_CASES = [
    "problems 10-14",          # a range, not a date
    "test 2/30",               # not a real day
    "read chapter 4",
    "monday blues essay prompt",  # still "monday"? no: words around it
]


@pytest.mark.parametrize("text", NO_DATE_CASES[:3])
def test_things_that_are_not_dates(text):
    assert parse(text).due_date is None


def test_impossible_date_stays_in_the_title():
    q = parse("test 2/30")
    assert q.due_date is None
    assert "2/30" in q.title


def test_the_leftmost_date_wins_and_the_rest_stay_in_the_title():
    q = parse("study for the 10/14 test due fri")
    assert q.due_date == date(2026, 10, 14)
    assert "fri" in q.title.lower()


def test_a_weekday_inside_a_word_is_not_a_date():
    q = parse("frisbee practice notes")
    assert q.due_date is None
    assert q.title == "Frisbee practice notes"


def test_slash_dates_inside_urls_or_fractions_of_words_are_ignored():
    assert parse("unit3/lesson4 notes").due_date is None


# ── Times ─────────────────────────────────────────────────────────────

TIME_CASES = [
    ("quiz fri at 3pm", time(15, 0), date(2026, 10, 2)),
    ("quiz fri 3:30pm", time(15, 30), date(2026, 10, 2)),
    ("essay by 11:59 pm thurs", time(23, 59), date(2026, 10, 8)),
    ("essay fri at 9am", time(9, 0), date(2026, 10, 2)),
    ("essay fri at 12pm", time(12, 0), date(2026, 10, 2)),
    ("essay fri at 12am", time(0, 0), date(2026, 10, 2)),
    ("essay fri at 15:00", time(15, 0), date(2026, 10, 2)),
    ("essay fri at 4p", time(16, 0), date(2026, 10, 2)),
    ("essay fri noon", time(12, 0), date(2026, 10, 2)),
    ("essay fri midnight", time(23, 59), date(2026, 10, 2)),
    # a bare time means today when it is still ahead, else tomorrow
    ("call tutor at 5pm", time(17, 0), date(2026, 10, 1)),
    ("call tutor at 9am", time(9, 0), date(2026, 10, 2)),
]


@pytest.mark.parametrize("text,expected_time,expected_date", TIME_CASES)
def test_times(text, expected_time, expected_date):
    q = parse(text)
    assert q.due_time == expected_time
    assert q.due_date == expected_date


@pytest.mark.parametrize("text", ["do 2a and 2b", "period 4a review", "room 3b"])
def test_letters_after_numbers_are_not_times(text):
    assert parse(text).due_time is None


# ── Durations ─────────────────────────────────────────────────────────

DURATION_CASES = [
    ("bio lab due fri 2h", 120),
    ("reading 45m", 45),
    ("reading 45 min", 45),
    ("reading 45mins", 45),
    ("reading 45 minutes", 45),
    ("essay 1.5h", 90),
    ("essay 1.5 hrs", 90),
    ("essay 2 hours", 120),
    ("essay 1 hour", 60),
    ("essay 1hr", 60),
    ("math hw 1h30", 90),
    ("math hw 1h 30m", 90),
    ("math hw 1h30m", 90),
    ("review for 2h", 120),
    ("review for half an hour", 30),
    ("review 90m fri", 90),
]


@pytest.mark.parametrize("text,minutes", DURATION_CASES)
def test_durations(text, minutes):
    assert parse(text).minutes == minutes


@pytest.mark.parametrize("text", ["essay 900h", "essay 0m", "read ch 4"])
def test_absurd_or_missing_durations_are_none(text):
    assert parse(text).minutes is None


def test_a_duration_is_not_also_read_as_a_time_or_date():
    q = parse("lab 2h fri")
    assert q.minutes == 120
    assert q.due_time is None
    assert q.due_date == date(2026, 10, 2)


# ── Courses ───────────────────────────────────────────────────────────

COURSE_CASES = [
    ("bio lab due fri", "AP Biology"),
    ("biology reading", "AP Biology"),
    ("chem test next tue", "Chemistry Honors - Period 3"),
    ("chemistry worksheet", "Chemistry Honors - Period 3"),
    ("apush reading tmrw", "AP US History"),
    ("us history dbq", "AP US History"),
    ("english essay", "English 10"),
    ("eng essay", "English 10"),
    ("alg 2 homework", "Algebra II"),
    ("span vocab quiz", "Spanish III"),
    ("physics lab", "Physics"),
    ("phys problems", "Physics"),
    ("#chem worksheet", "Chemistry Honors - Period 3"),
    ("#bio worksheet", "AP Biology"),
    ("CHEM quiz", "Chemistry Honors - Period 3"),
]


@pytest.mark.parametrize("text,course", COURSE_CASES)
def test_course_inference(text, course):
    assert parse(text).course == course


def test_course_words_stay_in_the_title_but_hashtags_do_not():
    assert parse("bio lab due fri").title == "Bio lab"
    assert parse("#chem worksheet").title == "Worksheet"


def test_an_ambiguous_abbreviation_matches_neither_course():
    courses = ["AP Biology", "BIO 101"]
    assert parse("bio lab", courses=courses).course == ""
    # the full code still picks one
    assert parse("bio 101 lab", courses=courses).course == "BIO 101"


def test_no_courses_means_no_course():
    assert parse("bio lab fri", courses=[]).course == ""


def test_an_unknown_hashtag_is_kept_as_the_course_name():
    q = parse("#robotics build log", courses=[])
    assert q.course == "robotics"
    assert q.title == "Build log"


def test_type_words_never_become_course_aliases():
    aliases = course_aliases(["Lab Science", "Test Prep"])
    assert "lab" not in aliases and "test" not in aliases


def test_ambiguity_is_visible_in_the_alias_table():
    aliases = course_aliases(["AP Biology", "BIO 101"])
    assert aliases["bio"] == {"AP Biology", "BIO 101"}


# ── Kind and priority ─────────────────────────────────────────────────

KIND_CASES = [
    ("chem test fri", "test", "test"),
    ("pop quiz tmrw", "quiz", "test"),
    ("final exam 12/15", "exam", "exam"),
    ("midterm review", "exam", "exam"),
    ("bio lab", "lab", "lab"),
    ("lab report due mon", "lab", "lab"),
    ("english essay", "essay", "project"),
    ("history paper", "essay", "project"),
    ("science project", "project", "project"),
    ("slides for presentation", "presentation", "project"),
    ("read ch 4", "reading", "homework"),
    ("math worksheet", "homework", "homework"),
    ("call grandma", "", "homework"),
]


@pytest.mark.parametrize("text,kind,planner", KIND_CASES)
def test_kinds(text, kind, planner):
    q = parse(text)
    assert q.kind == kind
    assert q.planner_kind == planner == planner_kind(kind)


def test_infer_kind_is_the_same_rule():
    assert infer_kind("Unit 3 Test") == "test"
    assert infer_kind("") == ""


PRIORITY_CASES = [
    ("essay p1", "High"),
    ("essay p2", "Medium"),
    ("essay p3", "Low"),
    ("essay !!!", "High"),
    ("essay !!", "Medium"),
    ("essay !", "Low"),
    ("urgent essay", "High"),
    ("essay", ""),
]


@pytest.mark.parametrize("text,priority", PRIORITY_CASES)
def test_priority(text, priority):
    assert parse(text).priority == priority


def test_priority_tokens_leave_the_title_but_urgent_stays():
    assert parse("essay p1").title == "Essay"
    assert parse("urgent essay").title == "Urgent essay"


# ── Titles ────────────────────────────────────────────────────────────

TITLE_CASES = [
    ("bio lab due fri 2h", "Bio lab"),
    ("chem test next tue", "Chem test"),
    ("spanish vocab quiz oct 14 at 3pm", "Spanish vocab quiz"),
    ("english essay draft by 11:59pm thurs", "English essay draft"),
    ("problems 10-14 due mon", "Problems 10-14"),
    ("add: read ch 4 tonight", "Read ch 4"),
    ("study for 1.5 hrs this thu", "Study"),
    ("   lots   of   space   fri  ", "Lots of space"),
]


@pytest.mark.parametrize("text,title", TITLE_CASES)
def test_titles(text, title):
    assert parse(text).title == title


def test_a_line_that_is_all_metadata_still_gets_a_name():
    assert parse("fri 2h", courses=[]).title == "New task"
    assert parse("#chem fri").title == "Chemistry Honors - Period 3"


def test_empty_input_is_an_empty_title():
    assert parse("").title == ""
    assert parse("   ").title == ""


def test_very_long_input_is_bounded():
    q = parse("x" * 2000)
    assert len(q.title) <= 512


def test_to_dict_has_what_the_preview_chips_need():
    d = parse("bio lab due fri 2h").to_dict()
    assert d["due_date"] == "2026-10-02"
    assert d["minutes"] == 120
    assert d["course"] == "AP Biology"
    kinds = {m["kind"] for m in d["matched"]}
    assert {"date", "minutes", "course", "kind"} <= kinds


# ── Timezones ─────────────────────────────────────────────────────────


def test_local_now_converts_from_utc():
    utc = datetime(2026, 10, 2, 3, 0)  # 03:00 UTC Friday
    assert local_now("America/Los_Angeles", utc=utc) == datetime(2026, 10, 1, 20, 0)
    assert local_now("Asia/Tokyo", utc=utc) == datetime(2026, 10, 2, 12, 0)
    assert local_now("UTC", utc=utc) == utc


@pytest.mark.parametrize("zone", ["", None, "Mars/Olympus", "x" * 80])
def test_unknown_timezones_fall_back_to_utc(zone):
    utc = datetime(2026, 10, 2, 3, 0)
    assert local_now(zone, utc=utc) == utc
    assert valid_timezone(zone) is None


TZ_CASES = [
    # 03:00 UTC on Friday 2 Oct is still Thursday evening in Los Angeles,
    # so "tmrw" there is Friday — but Saturday in UTC and in Tokyo.
    ("America/Los_Angeles", "quiz tmrw", date(2026, 10, 2)),
    ("UTC", "quiz tmrw", date(2026, 10, 3)),
    ("Asia/Tokyo", "quiz tmrw", date(2026, 10, 3)),
    ("America/Los_Angeles", "quiz today", date(2026, 10, 1)),
    ("Asia/Tokyo", "quiz today", date(2026, 10, 2)),
    # "fri" said on a Thursday evening (LA) is tomorrow; said on Friday
    # morning (Tokyo) it is next week.
    ("America/Los_Angeles", "quiz fri", date(2026, 10, 2)),
    ("Asia/Tokyo", "quiz fri", date(2026, 10, 9)),
    # a bare time: 8pm has passed in LA (20:00 local), not in Tokyo (noon)
    ("America/Los_Angeles", "call at 7pm", date(2026, 10, 2)),
    ("Asia/Tokyo", "call at 7pm", date(2026, 10, 2)),
]


@pytest.mark.parametrize("zone,text,expected", TZ_CASES)
def test_relative_dates_follow_the_students_timezone(zone, text, expected):
    now = local_now(zone, utc=datetime(2026, 10, 2, 3, 0))
    assert parse(text, now=now).due_date == expected


def test_an_ordinal_already_past_this_month_is_next_month():
    assert parse("review the 3rd", now=datetime(2026, 10, 20, 9)).due_date == date(2026, 11, 3)
    # a day the next month does not have is not invented
    assert parse("review the 31st", now=datetime(2026, 10, 31, 23, 0)).due_date == date(2026, 10, 31)


def test_new_years_rollover():
    now = datetime(2026, 12, 30, 10, 0)
    assert parse("essay 1/4", now=now).due_date == date(2027, 1, 4)
    assert parse("essay tmrw", now=now).due_date == date(2026, 12, 31)
    assert parse("essay in 3 days", now=now).due_date == date(2027, 1, 2)
    assert parse("essay next mon", now=now).due_date == date(2027, 1, 4)


def test_leap_day():
    assert parse("quiz 2/29", now=datetime(2028, 2, 1, 9)).due_date == date(2028, 2, 29)
    assert parse("quiz 2/29", now=datetime(2026, 2, 1, 9)).due_date is None
