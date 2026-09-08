"""Quiet hours must land on the student's clock, not the server's.

The window is stored as local wall-clock hours because that is how people
think about them -- "don't buzz me after 10" means 10 where the student is.
Converting to that clock needed ``utc_offset_minutes``, and two things were
wrong with it.

**Nothing ever set it.** The only client of /api/notifications/preferences
posts ``marketing_emails_opt_in`` and nothing else, so the column sat at its
default of 0 and quiet hours were applied to UTC. For a student in Los
Angeles that is not a drift, it is an inversion: a 22:00-07:00 window landed
on 15:00-00:00 local, so notifications were suppressed through the entire
after-school study block and allowed at two in the morning. Fourteen of
twenty-four hours behaved the opposite of how they were configured.

**A fixed offset cannot express DST**, so even a correctly populated one is
wrong for half the year in any region that observes it.

The app already knew the answer: the streak engine stores an IANA timezone
on the streak row and resolves it with ZoneInfo. Preferences now prefer that
name, converted per moment, and keep the integer offset only as a fallback
for a profile with no timezone recorded yet.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from intelliplan.notifications.events import EventKind
from intelliplan.notifications.preferences import (
    Preferences,
    QuietHours,
    preferences_from_user,
)

NIGHT = QuietHours(start_hour=22, end_hour=7, enabled=True)

LA = "America/Los_Angeles"
LONDON = "Europe/London"


def la():
    return Preferences(user_id=1, tz_name=LA, quiet_hours=NIGHT)


# ── The reported inversion ───────────────────────────────────────────


def test_the_evening_study_block_is_no_longer_suppressed():
    """17:00 local in LA is prime study time. Under the UTC fallback it fell
    inside the quiet window and every non-urgent message was held."""
    prefs = la()
    evening_utc = datetime(2026, 7, 16, 0, 0)      # 17:00 the previous day, LA
    assert prefs.to_local(evening_utc).hour == 17
    assert NIGHT.contains(prefs.to_local(evening_utc)) is False


def test_two_in_the_morning_is_quiet_again():
    prefs = la()
    small_hours_utc = datetime(2026, 7, 15, 9, 0)  # 02:00 LA
    assert prefs.to_local(small_hours_utc).hour == 2
    assert NIGHT.contains(prefs.to_local(small_hours_utc)) is True


def test_the_whole_day_now_matches_the_students_clock():
    """The regression in aggregate: with the old UTC fallback, 14 of the 24
    hours disagreed with what the student configured."""
    prefs = la()
    stale = Preferences(user_id=1, utc_offset_minutes=0, quiet_hours=NIGHT)
    disagreements = 0
    for hour in range(24):
        moment = datetime(2026, 7, 15, hour, 0)
        if NIGHT.contains(stale.to_local(moment)) != NIGHT.contains(prefs.to_local(moment)):
            disagreements += 1
    assert disagreements == 14, "the bug this fixes should be exactly this large"


# ── DST ──────────────────────────────────────────────────────────────


def test_the_same_utc_instant_maps_to_different_local_hours_across_dst():
    prefs = Preferences(user_id=1, tz_name=LONDON, quiet_hours=NIGHT)
    assert prefs.to_local(datetime(2026, 1, 15, 12, 0)).hour == 12   # GMT
    assert prefs.to_local(datetime(2026, 7, 15, 12, 0)).hour == 13   # BST


def test_a_held_message_is_released_at_seven_local_in_both_seasons():
    """The point of the whole change: the student picked 07:00, so 07:00 is
    what they get in January and in July, even though the UTC instant moves."""
    prefs = Preferences(user_id=1, tz_name=LONDON, quiet_hours=NIGHT)
    for month in (1, 7):
        earliest = datetime(2026, month, 15, 3, 0)
        sent = prefs.delivery_time(EventKind.PLAN_CHANGED, earliest)
        assert prefs.to_local(sent).hour == 7


def test_a_fixed_offset_would_have_drifted_by_an_hour():
    """Why the offset is not enough even when populated: pinned to winter,
    it is an hour out all summer."""
    pinned_to_winter = Preferences(user_id=1, utc_offset_minutes=0, quiet_hours=NIGHT)
    real = Preferences(user_id=1, tz_name=LONDON, quiet_hours=NIGHT)
    summer = datetime(2026, 7, 15, 12, 0)
    assert real.to_local(summer).hour - pinned_to_winter.to_local(summer).hour == 1


# ── Conversion is sound ──────────────────────────────────────────────


@pytest.mark.parametrize("month", [1, 7])
@pytest.mark.parametrize("hour", range(24))
def test_local_and_utc_round_trip(month, hour):
    prefs = Preferences(user_id=1, tz_name=LONDON, quiet_hours=NIGHT)
    moment = datetime(2026, month, 15, hour, 30)
    assert prefs.to_utc(prefs.to_local(moment)) == moment


def test_an_urgent_kind_still_ignores_quiet_hours():
    """The inverse guard: a session starting in fifteen minutes is exactly
    what a late buzz is for, and holding it delivers it after the fact."""
    prefs = la()
    middle_of_the_night = datetime(2026, 7, 15, 9, 0)   # 02:00 LA
    assert prefs.delivery_time(
        EventKind.SESSION_UPCOMING, middle_of_the_night) == middle_of_the_night


def test_quiet_hours_switched_off_sends_immediately():
    prefs = Preferences(
        user_id=1, tz_name=LA,
        quiet_hours=QuietHours(start_hour=22, end_hour=7, enabled=False))
    night = datetime(2026, 7, 15, 9, 0)
    assert prefs.delivery_time(EventKind.PLAN_CHANGED, night) == night


# ── Fallbacks: a bad timezone must never stop the delivery sweep ─────


def test_an_unrecognised_timezone_falls_back_to_the_offset():
    prefs = Preferences(
        user_id=1, tz_name="Mars/Olympus_Mons",
        utc_offset_minutes=-420, quiet_hours=NIGHT)
    assert prefs.to_local(datetime(2026, 7, 15, 12, 0)).hour == 5


def test_no_timezone_recorded_still_uses_the_stored_offset():
    prefs = Preferences(user_id=1, tz_name="", utc_offset_minutes=330, quiet_hours=NIGHT)
    local = prefs.to_local(datetime(2026, 7, 15, 12, 0))
    assert (local.hour, local.minute) == (17, 30)


def test_a_bad_timezone_still_round_trips_through_the_offset():
    prefs = Preferences(
        user_id=1, tz_name="not a zone", utc_offset_minutes=-300, quiet_hours=NIGHT)
    moment = datetime(2026, 7, 15, 12, 0)
    assert prefs.to_utc(prefs.to_local(moment)) == moment


# ── Reading it off a profile ─────────────────────────────────────────


class _User:
    id = 7
    push_reminders_opt_in = True
    email_reminders_opt_in = False
    sms_reminders_opt_in = False
    phone = None
    email = "s@example.com"
    notification_kinds = None
    utc_offset_minutes = 0
    quiet_hours_start = 22
    quiet_hours_end = 7
    quiet_hours_enabled = True
    reminder_lead_minutes = 30


def test_the_timezone_is_taken_from_the_argument():
    prefs = preferences_from_user(_User(), push_subscribed=True, tz_name=LA)
    assert prefs.tz_name == LA
    assert prefs.to_local(datetime(2026, 7, 15, 0, 0)).hour == 17


def test_no_timezone_argument_leaves_the_offset_in_charge():
    prefs = preferences_from_user(_User(), push_subscribed=True)
    assert prefs.tz_name == ""
    assert prefs.to_local(datetime(2026, 7, 15, 12, 0)).hour == 12


# ── The wiring ───────────────────────────────────────────────────────


def test_the_glue_reads_the_timezone_off_the_streak_row():
    """The fix is only real if _preferences_for actually finds the name.

    The timezone lives on UserStreak, not User -- which is exactly why the
    notification path never saw it and fell back to an offset no client
    sets. This pins the lookup so a future refactor cannot quietly sever it
    again and leave the whole subsystem back on UTC.
    """
    import App
    import notifications_glue
    from App import User, UserStreak, db

    with App.app.app_context():
        user = User(
            email="tzwire+a@example.com",
            password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
            name="TZ Wire",
        )
        db.session.add(user)
        db.session.commit()
        user_id = user.id

        try:
            # No streak row yet: nothing to find, and that must not raise.
            assert notifications_glue._timezone_for(user_id) == ""

            db.session.add(UserStreak(user_id=user_id, timezone=LA))
            db.session.commit()
            assert notifications_glue._timezone_for(user_id) == LA

            prefs = notifications_glue._preferences_for(user)
            assert prefs.tz_name == LA
            # 00:00 UTC is 17:00 the previous day in LA -- squarely study time.
            assert prefs.to_local(datetime(2026, 7, 16, 0, 0)).hour == 17
        finally:
            # Child row first: user_streaks.user_id references users.id, so
            # deleting the user first fails the commit and leaves the session
            # dirty for whatever test runs next -- which is exactly what it
            # did, surfacing as an unrelated SQLAlchemy error much later in
            # the suite. try/finally so a failed assertion above still cleans
            # up rather than poisoning the rest of the run.
            db.session.rollback()
            UserStreak.query.filter_by(user_id=user_id).delete()
            User.query.filter_by(id=user_id).delete()
            db.session.commit()
