"""Rotation math for class timetables.

Rotating schools count A/B and N-day cycles in *school days*: a weekend or
a holiday does not use up a rotation day. Getting this wrong by one sends a
student to the wrong class, so these pin the counting down exactly.
"""

from datetime import date, datetime, timedelta

import pytest

from intelliplan.domain.timetable import (
    Meeting,
    Rotation,
    SkipDay,
    anchor_for_today,
    busy_by_date,
    is_school_day,
    meetings_on,
    next_meeting,
    parse_clock,
    parse_int_list,
    parse_weekdays,
    rotation_day,
    rotation_label,
)

MON = date(2026, 9, 28)  # a Monday


def labels(rotation, start, days):
    return [rotation_label(start + timedelta(d), rotation) for d in range(days)]


# ── A/B ──────────────────────────────────────────────────────────────


def test_ab_alternates_and_skips_weekends():
    rot = Rotation(kind="ab", anchor_date=MON)
    assert labels(rot, MON, 8) == [
        "A Day", "B Day", "A Day", "B Day", "A Day",  # Mon–Fri
        "", "",                                       # weekend: no school
        "B Day",                                      # Monday continues the count
    ]


def test_ab_holiday_does_not_consume_a_day():
    holiday = MON + timedelta(days=2)  # Wednesday off
    rot = Rotation(kind="ab", anchor_date=MON, skip_days=(SkipDay(holiday, holiday, "Teacher day"),))
    assert labels(rot, MON, 5) == ["A Day", "B Day", "", "A Day", "B Day"]


def test_ab_counts_backwards_before_the_anchor():
    rot = Rotation(kind="ab", anchor_date=MON, anchor_day=2)  # Monday is B
    assert rotation_label(MON - timedelta(days=3), rot) == "A Day"  # previous Friday


def test_rotation_without_an_anchor_is_unknown_rather_than_guessed():
    rot = Rotation(kind="ab")
    assert rotation_day(MON, rot) is None
    assert rotation_label(MON, rot) == ""


# ── N-day cycles ─────────────────────────────────────────────────────


def test_six_day_cycle_skips_weekends_and_a_break():
    brk_start, brk_end = MON + timedelta(days=9), MON + timedelta(days=11)  # Wed–Fri week 2
    rot = Rotation(kind="cycle", length=6, anchor_date=MON,
                   skip_days=(SkipDay(brk_start, brk_end, "Fall break"),))
    school = [MON + timedelta(d) for d in range(21) if is_school_day(MON + timedelta(d), rot)]
    # 15 weekdays minus a 3-day break: 12 school days, exactly two cycles.
    assert [rotation_day(d, rot) for d in school] == [1, 2, 3, 4, 5, 6, 1, 2, 3, 4, 5, 6]
    assert rotation_day(brk_start, rot) is None
    assert rotation_label(MON + timedelta(days=7), rot) == "Day 6"   # second Monday
    assert rotation_label(MON + timedelta(days=14), rot) == "Day 2"  # Monday after the break


def test_cycle_length_is_clamped_to_something_sane():
    assert Rotation(kind="cycle", length=99).length == 10
    assert Rotation(kind="cycle", length=0).length == 2
    assert Rotation(kind="ab", length=7).length == 2


def test_reanchoring_fixes_a_drifted_rotation():
    rot = Rotation(kind="cycle", length=6, anchor_date=MON)
    thursday = MON + timedelta(days=3)
    assert rotation_day(thursday, rot) == 4
    fixed = anchor_for_today(thursday, 1, rot)   # "today is actually Day 1"
    assert rotation_day(thursday, fixed) == 1
    assert rotation_day(thursday + timedelta(days=1), fixed) == 2


# ── Week 1 / Week 2 ──────────────────────────────────────────────────


def test_week_rotation_flips_each_calendar_week_even_across_holidays():
    rot = Rotation(kind="week", length=2, anchor_date=MON + timedelta(days=2),
                   skip_days=(SkipDay(MON + timedelta(days=7), MON + timedelta(days=11), "Break"),))
    assert rotation_label(MON, rot) == "Week 1"
    assert rotation_label(MON + timedelta(days=8), rot) == ""          # on break
    assert rotation_label(MON + timedelta(days=14), rot) == "Week 1"   # break week still counted
    assert rotation_label(MON + timedelta(days=21), rot) == "Week 2"


# ── Meetings ─────────────────────────────────────────────────────────


def test_classes_meet_only_on_their_rotation_days():
    rot = Rotation(kind="ab", anchor_date=MON)
    chem = Meeting(course="Chem", start_minute=8 * 60, end_minute=9 * 60, rotation_days=(1,))
    art = Meeting(course="Art", start_minute=8 * 60, end_minute=9 * 60, rotation_days=(2,))
    every = Meeting(course="Homeroom", start_minute=7 * 60 + 40, end_minute=8 * 60)
    assert [m.course for m in meetings_on(MON, [chem, art, every], rot)] == ["Homeroom", "Chem"]
    assert [m.course for m in meetings_on(MON + timedelta(1), [chem, art, every], rot)] == ["Homeroom", "Art"]
    assert meetings_on(MON + timedelta(5), [chem, art, every], rot) == []  # Saturday


def test_weekday_filter_and_bell_schedule():
    rot = Rotation()
    pe = Meeting(course="PE", period="3", weekdays=(1, 3))  # Tue/Thu, no times of its own
    bell = {"3": ["10:05", "10:55"]}
    assert meetings_on(MON, [pe], rot, bell) == []
    tue = meetings_on(MON + timedelta(1), [pe], rot, bell)
    assert [(m.start_minute, m.end_minute) for m in tue] == [(605, 655)]
    # Without the bell it has no times, so it cannot block anything.
    assert meetings_on(MON + timedelta(1), [pe], rot) == []


def test_busy_by_date_is_the_scheduler_shape():
    rot = Rotation(kind="ab", anchor_date=MON)
    chem = Meeting(course="Chem", start_minute=480, end_minute=540, rotation_days=(1,))
    busy = busy_by_date(MON, 7, [chem], rot)
    assert busy == {MON: [(480, 540)], MON + timedelta(2): [(480, 540)], MON + timedelta(4): [(480, 540)]}


def test_next_meeting_rolls_to_the_next_school_day():
    rot = Rotation()
    m = Meeting(course="Math", start_minute=9 * 60, end_minute=10 * 60)
    friday_evening = datetime.combine(MON + timedelta(days=4), datetime.min.time()).replace(hour=18)
    day, found = next_meeting(friday_evening, [m], rot)
    assert day == MON + timedelta(days=7) and found.course == "Math"


# ── Parsing ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("7:45 AM", 465), ("12:30 pm", 750), ("12:05 AM", 5), ("14:05:00", 845),
    ("2026-09-30T07:45:00", 465), ("08:00", 480), ("nope", None), ("", None), ("25:00", None),
])
def test_parse_clock(raw, expected):
    assert parse_clock(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("Mon,Wed,Fri", (0, 2, 4)), ("MWF", (0, 2, 4)), ("TR", (1, 3)), (["Tue", "Thursday"], (1, 3)),
    ([0, 6], (0, 6)), ("", ()), ("blah", ()),
])
def test_parse_weekdays(raw, expected):
    assert parse_weekdays(raw) == expected


def test_parse_rotation_days_accepts_letters():
    assert parse_int_list(["A"]) == (1,)
    assert parse_int_list("B, Day 3") == (2, 3)
    assert parse_int_list("11") == ()


def test_round_trip_through_dict():
    rot = Rotation(kind="cycle", length=6, anchor_date=MON, anchor_day=3,
                   skip_days=(SkipDay(MON, MON + timedelta(2), "Break"),))
    again = Rotation.from_dict(rot.to_dict())
    assert again == rot
