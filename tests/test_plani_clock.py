"""Plani's schedule tool must accept the times its own suggested prompts use.

The Command Center offers "Schedule my week for 2 hours a day after 4:30 PM".
That is a start time with no end time, written with AM/PM; the tool used to
reject both.
"""

import pytest

import plani_agent as pa


@pytest.mark.parametrize("raw, expected", [
    ("4:30 PM", "16:30"),
    ("4:30pm", "16:30"),
    ("4pm", "16:00"),
    ("4 P.M.", "16:00"),
    ("16:30", "16:30"),
    ("7:05", "07:05"),
    ("12am", "00:00"),
    ("12:15 pm", "12:15"),
])
def test_norm_clock_reads_common_formats(raw, expected):
    assert pa._norm_clock(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "25:00", "13pm", "4:75", "after school"])
def test_norm_clock_rejects_garbage(raw):
    assert pa._norm_clock(raw) == ""


def test_start_only_window_fits_hours_plus_breaks():
    # 2h of study + 45 min of breaks after 16:30.
    assert pa._add_minutes("16:30", 2 * 60 + 45, cap="23:30") == "19:15"


def test_window_is_capped_late_and_early():
    assert pa._add_minutes("22:30", 165, cap="23:30") == "23:30"
    assert pa._add_minutes("07:00", -165, cap="06:00") == "06:00"
