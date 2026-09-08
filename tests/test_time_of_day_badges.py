"""Regression tests for the Early Bird / Night Owl badges.

Two bugs lived in the same two lines of /study/session/complete:

  * ``int(data.get("local_hour", datetime.now().hour) or 0)`` -- the ``or 0``
    turned a missing, null, or literal-zero hour into midnight, so any client
    that predates ``local_hour`` (mobile, the extension) earned Night Owl for
    a session finished over lunch.
  * ``local_hour < 7`` for Early Bird and ``local_hour == 0`` for Night Owl --
    which made 1am an early morning and left Night Owl reachable only during
    the single hour after midnight.
"""

from datetime import datetime

import App


# ── The hour we actually read off the request ────────────────────────


def test_a_real_hour_is_taken_at_face_value():
    assert App._coerce_local_hour(6) == 6
    assert App._coerce_local_hour("23") == 23


def test_midnight_is_not_confused_with_a_missing_value():
    # The bug: `0 or 0` and `None or 0` were indistinguishable.
    assert App._coerce_local_hour(0) == 0


def test_a_missing_hour_falls_back_to_the_server_clock():
    server_hour = datetime.now().hour
    assert App._coerce_local_hour(None) == server_hour
    assert App._coerce_local_hour("") == server_hour


def test_a_nonsense_hour_falls_back_rather_than_awarding_a_badge():
    server_hour = datetime.now().hour
    assert App._coerce_local_hour(99) == server_hour
    assert App._coerce_local_hour(-3) == server_hour
    assert App._coerce_local_hour("half past nine") == server_hour


# ── The windows those hours land in ──────────────────────────────────


def _badges_for(hour):
    """The time-of-day badges /study/session/complete would award."""
    earned = set()
    if 4 <= hour < 8:
        earned.add("early_bird")
    if hour >= 23 or hour < 4:
        earned.add("night_owl")
    return earned


def test_getting_up_at_six_is_an_early_bird():
    assert _badges_for(6) == {"early_bird"}


def test_one_in_the_morning_is_a_night_owl_not_an_early_bird():
    assert _badges_for(1) == {"night_owl"}


def test_late_evening_is_a_night_owl():
    assert _badges_for(23) == {"night_owl"}


def test_the_two_windows_never_overlap():
    for hour in range(24):
        assert len(_badges_for(hour)) <= 1, hour


def test_the_middle_of_the_day_earns_neither():
    for hour in range(8, 23):
        assert _badges_for(hour) == set()
