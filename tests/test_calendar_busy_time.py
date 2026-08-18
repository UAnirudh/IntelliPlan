"""Real calendar events are time the student does not have.

The scheduler has always subtracted the weekly commitments typed into
settings. It never looked at the calendar it already had permission to read,
so it would book study time on top of a dentist appointment.
"""

from datetime import date, datetime, timedelta

import pytest

import google_calendar_helper as gcal
import scheduler_engine
from scheduler_engine import windows_for_date

# A Monday far enough ahead that windows are never trimmed to "now".
MONDAY = date.today() + timedelta(days=(7 - date.today().weekday()) % 7 or 7)
AVAILABILITY = {"Mon": ["afternoon", "evening"]}
NOW = datetime.combine(date.today(), datetime.min.time())


def minutes(windows):
    return sum(w.minutes for w in windows)


# ── Subtraction ───────────────────────────────────────────────────────


def test_a_calendar_event_removes_study_time():
    plain = windows_for_date(MONDAY, AVAILABILITY, "evening", None, now=NOW)
    busy = windows_for_date(
        MONDAY, AVAILABILITY, "evening", None, now=NOW,
        busy_by_date={MONDAY: [(18 * 60, 19 * 60)]},
    )
    assert minutes(busy) == minutes(plain) - 60


def test_no_study_block_overlaps_a_calendar_event():
    busy_start, busy_end = 18 * 60, 19 * 60
    windows = windows_for_date(
        MONDAY, AVAILABILITY, "evening", None, now=NOW,
        busy_by_date={MONDAY: [(busy_start, busy_end)]},
    )
    for w in windows:
        start = w.start.hour * 60 + w.start.minute
        end = w.end.hour * 60 + w.end.minute
        assert end <= busy_start or start >= busy_end, (start, end)


def test_events_on_other_days_are_ignored():
    other = MONDAY + timedelta(days=1)
    plain = windows_for_date(MONDAY, AVAILABILITY, "evening", None, now=NOW)
    same = windows_for_date(
        MONDAY, AVAILABILITY, "evening", None, now=NOW,
        busy_by_date={other: [(18 * 60, 20 * 60)]},
    )
    assert minutes(same) == minutes(plain)


def test_calendar_events_and_typed_commitments_both_apply():
    both = windows_for_date(
        MONDAY, AVAILABILITY, "evening", "soccer Mon 4-5 pm", now=NOW,
        busy_by_date={MONDAY: [(19 * 60, 20 * 60)]},
    )
    only_typed = windows_for_date(
        MONDAY, AVAILABILITY, "evening", "soccer Mon 4-5 pm", now=NOW,
    )
    assert minutes(both) == minutes(only_typed) - 60


def test_an_all_day_event_leaves_no_study_time():
    windows = windows_for_date(
        MONDAY, AVAILABILITY, "evening", None, now=NOW,
        busy_by_date={MONDAY: [(0, 24 * 60)]},
    )
    assert windows == []


def test_overlapping_events_are_not_double_subtracted():
    once = windows_for_date(
        MONDAY, AVAILABILITY, "evening", None, now=NOW,
        busy_by_date={MONDAY: [(18 * 60, 20 * 60)]},
    )
    twice = windows_for_date(
        MONDAY, AVAILABILITY, "evening", None, now=NOW,
        busy_by_date={MONDAY: [(18 * 60, 19 * 60), (18 * 60 + 30, 20 * 60)]},
    )
    assert minutes(twice) == minutes(once)


@pytest.mark.parametrize(
    "interval", [("x", "y"), (600, 600), (700, 600), (None, 60)]
)
def test_malformed_intervals_are_skipped_not_fatal(interval):
    windows = windows_for_date(
        MONDAY, AVAILABILITY, "evening", None, now=NOW,
        busy_by_date={MONDAY: [interval]},
    )
    assert windows


def test_no_calendar_data_changes_nothing():
    plain = windows_for_date(MONDAY, AVAILABILITY, "evening", None, now=NOW)
    for empty in (None, {}, {MONDAY: []}):
        assert minutes(
            windows_for_date(MONDAY, AVAILABILITY, "evening", None, now=NOW,
                             busy_by_date=empty)
        ) == minutes(plain)


# ── Reading the calendar ──────────────────────────────────────────────


class _FakeFreebusy:
    def __init__(self, periods):
        self._periods = periods
        self.body = None

    def query(self, body):
        self.body = body
        return self

    def execute(self):
        return {"calendars": {"primary": {"busy": self._periods}}}


class _FakeService:
    def __init__(self, periods):
        self._fb = _FakeFreebusy(periods)

    def freebusy(self):
        return self._fb


@pytest.fixture
def fake_calendar(monkeypatch):
    holder = {}

    def install(periods):
        service = _FakeService(periods)
        holder["service"] = service
        monkeypatch.setattr(
            gcal, "get_calendar_service", lambda token: (service, None)
        )
        return service

    return install


def test_utc_times_are_converted_to_the_students_local_clock(fake_calendar):
    fake_calendar([{"start": "2026-03-02T22:00:00Z", "end": "2026-03-02T23:00:00Z"}])
    # UTC-5: 22:00Z is 17:00 local, on the same day.
    busy = gcal.busy_minutes_by_date(
        {"token": "x"}, date(2026, 3, 2), days=2, utc_offset_minutes=-300
    )
    assert busy[date(2026, 3, 2)] == [(17 * 60, 18 * 60)]


def test_an_event_spanning_midnight_is_busy_on_both_days(fake_calendar):
    """Clipping it to the first day hands the student a free evening they do
    not have."""
    fake_calendar([{"start": "2026-03-02T23:00:00Z", "end": "2026-03-03T01:00:00Z"}])
    busy = gcal.busy_minutes_by_date(
        {"token": "x"}, date(2026, 3, 2), days=3, utc_offset_minutes=0
    )
    assert busy[date(2026, 3, 2)] == [(23 * 60, 24 * 60)]
    assert busy[date(2026, 3, 3)] == [(0, 60)]


def test_the_whole_horizon_is_one_query(fake_calendar):
    """Fourteen round trips on the critical path of generating a plan is the
    difference between a scheduler that feels instant and one nobody waits
    for."""
    service = fake_calendar([])
    gcal.busy_minutes_by_date({"token": "x"}, date(2026, 3, 2), days=14)
    body = service.freebusy().body
    assert body["timeMin"].startswith("2026-03-02")
    assert body["timeMax"].startswith("2026-03-16")


def test_offset_aware_timestamps_are_accepted(fake_calendar):
    fake_calendar([{"start": "2026-03-02T14:00:00+02:00", "end": "2026-03-02T15:00:00+02:00"}])
    busy = gcal.busy_minutes_by_date(
        {"token": "x"}, date(2026, 3, 2), days=2, utc_offset_minutes=0
    )
    assert busy[date(2026, 3, 2)] == [(12 * 60, 13 * 60)]


def test_unparseable_periods_are_skipped(fake_calendar):
    fake_calendar([
        {"start": "not-a-time", "end": "also-not"},
        {"start": "2026-03-02T10:00:00Z", "end": "2026-03-02T11:00:00Z"},
    ])
    busy = gcal.busy_minutes_by_date({"token": "x"}, date(2026, 3, 2), days=2)
    assert busy[date(2026, 3, 2)] == [(600, 660)]


def test_a_zero_length_event_is_not_busy_time(fake_calendar):
    fake_calendar([{"start": "2026-03-02T10:00:00Z", "end": "2026-03-02T10:00:00Z"}])
    assert gcal.busy_minutes_by_date({"token": "x"}, date(2026, 3, 2), days=2) == {}
