"""Importing assignments from a calendar feed.

Canvas OAuth is per school and there is no global Developer Key, so one-click
sign-in can only work where a school's own admin has registered IntelliPlan.
The token fallback works everywhere but costs seven steps in a settings page
most students have never opened.

A calendar feed costs one copied URL, needs no admin and no key, and behaves
the same on every Canvas instance. It carries what is due and when -- not
grades, not points -- so it is the fastest way onto a plan, not a replacement
for a token.

These tests use realistic feed text rather than a live fetch: the sandbox
cannot reach instructure.com, and the parser is where the risk lives anyway.
The fixtures below encode the parts of RFC 5545 that a naive split-on-colon
parser gets wrong, each of which silently corrupts a student's plan rather
than failing loudly.
"""

from __future__ import annotations

from datetime import date

import pytest
import requests

import ics_feed


TODAY = date(2026, 9, 10)


def feed(*events):
    body = "\r\n".join(events)
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Instructure//Canvas//EN\r\n"
        f"{body}\r\n"
        "END:VCALENDAR\r\n"
    )


def event(summary, dtstart, uid="e1", extra=""):
    return (
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        f"DTSTART;VALUE=DATE:{dtstart}\r\n"
        f"SUMMARY:{summary}\r\n"
        f"{extra}"
        "END:VEVENT"
    )


def imported(text, today=TODAY):
    return ics_feed.events_to_assignments(ics_feed.parse_events(text), today=today)


# ── The shape the planner needs ──────────────────────────────────────


def test_an_assignment_arrives_with_a_title_course_and_due_date():
    rows = imported(feed(event("Essay 3 [AP US History]", "20260915")))
    assert len(rows) == 1
    assert rows[0]["title"] == "Essay 3"
    assert rows[0]["course"] == "AP US History"
    assert rows[0]["due_date"] == "2026-09-15"


def test_the_rows_match_the_shape_the_canvas_helper_produces():
    """The planner must not be able to tell which source a task came from."""
    row = imported(feed(event("Lab [Chem]", "20260915")))[0]
    for key in ("id", "course_id", "title", "course", "due_date", "points_possible",
                "priority", "estimated_time", "display_score", "color",
                "description", "submission_types", "rubric", "quiz_id", "is_quiz"):
        assert key in row, f"missing {key}"


def test_unknown_facts_are_left_empty_rather_than_invented():
    """A feed carries no points. Guessing one would flow into priority and
    quietly distort the plan, which is worse than admitting ignorance."""
    row = imported(feed(event("Essay [Hist]", "20260915")))[0]
    assert row["points_possible"] is None
    assert row["estimated_time"] is None
    assert row["display_score"] == ""


def test_rows_are_sorted_by_due_date():
    rows = imported(feed(
        event("Late [A]", "20260930", uid="c"),
        event("Soon [B]", "20260912", uid="a"),
        event("Middle [C]", "20260920", uid="b"),
    ))
    assert [r["due_date"] for r in rows] == ["2026-09-12", "2026-09-20", "2026-09-30"]


# ── RFC 5545 details a naive parser gets wrong ───────────────────────


def test_a_folded_long_title_is_not_truncated():
    """RFC 5545 wraps past ~75 chars with CRLF + a space. Ignoring that cuts
    every long assignment title in half."""
    text = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x\r\n"
        "DTSTART;VALUE=DATE:20260915\r\n"
        "SUMMARY:Read chapters four through nine and prepare a written respo\r\n"
        " nse for seminar [Literature]\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    row = imported(text)[0]
    assert row["title"] == (
        "Read chapters four through nine and prepare a written response for seminar")
    assert row["course"] == "Literature"


def test_escaped_commas_and_semicolons_survive():
    """Canvas escapes them per the spec; leaving them escaped shows students
    backslashes in their own assignment titles."""
    row = imported(feed(event(r"Essay: cause\, effect\; and impact [Hist]", "20260915")))[0]
    assert row["title"] == "Essay: cause, effect; and impact"


def test_an_escaped_newline_becomes_a_real_one():
    row = imported(feed(event("Task [C]", "20260915",
                              extra="DESCRIPTION:Line one\\nLine two\r\n")))[0]
    assert row["description"] == "Line one\nLine two"


def test_a_value_containing_colons_is_not_cut_at_the_first_one():
    """DESCRIPTION carries a URL. Splitting on every colon loses most of it."""
    row = imported(feed(event("Task [C]", "20260915",
                              extra="DESCRIPTION:See https://x.test/a?b=1\r\n")))[0]
    assert row["description"] == "See https://x.test/a?b=1"


def test_a_title_with_a_colon_survives():
    row = imported(feed(event("Essay: part two [Hist]", "20260915")))[0]
    assert row["title"] == "Essay: part two"


def test_a_datetime_start_works_as_well_as_a_date():
    text = feed(
        "BEGIN:VEVENT\r\nUID:x\r\nDTSTART:20260915T235900Z\r\n"
        "SUMMARY:Quiz [Bio]\r\nEND:VEVENT")
    assert imported(text)[0]["due_date"] == "2026-09-15"


# ── The course-name convention ───────────────────────────────────────


def test_a_summary_with_no_brackets_keeps_its_whole_title():
    row = imported(feed(event("Read chapter 4", "20260915")))[0]
    assert row["title"] == "Read chapter 4"
    assert row["course"] == "Calendar"


def test_brackets_in_the_middle_are_not_treated_as_a_course():
    row = imported(feed(event("Essay [draft] due Friday", "20260915")))[0]
    assert row["title"] == "Essay [draft] due Friday"
    assert row["course"] == "Calendar"


def test_a_summary_that_is_only_brackets_keeps_something_to_call_it():
    """Stripping the brackets would leave the assignment with no name."""
    row = imported(feed(event("[Chemistry]", "20260915")))[0]
    assert row["title"] == "[Chemistry]"


# ── What to keep and what to drop ────────────────────────────────────


def test_long_past_work_is_dropped():
    rows = imported(feed(event("Ancient [Hist]", "20250101")))
    assert rows == []


def test_recently_overdue_work_is_kept():
    """Two days late is exactly what a student opens a planner to deal with."""
    rows = imported(feed(event("Missed essay [Hist]", "20260908")))
    assert len(rows) == 1
    assert rows[0]["priority"] == "High"


def test_an_event_with_no_date_is_skipped_not_crashed_on():
    text = feed("BEGIN:VEVENT\r\nUID:x\r\nSUMMARY:No date [C]\r\nEND:VEVENT")
    assert imported(text) == []


def test_an_event_with_no_summary_is_skipped():
    text = feed("BEGIN:VEVENT\r\nUID:x\r\nDTSTART;VALUE=DATE:20260915\r\nEND:VEVENT")
    assert imported(text) == []


def test_a_repeated_uid_is_imported_once():
    rows = imported(feed(
        event("Essay [Hist]", "20260915", uid="same"),
        event("Essay [Hist]", "20260915", uid="same"),
    ))
    assert len(rows) == 1


def test_two_different_assignments_due_the_same_day_both_survive():
    """The inverse guard: dedupe must not collapse a real pair."""
    rows = imported(feed(
        event("Essay [Hist]", "20260915", uid="a"),
        event("Lab [Chem]", "20260915", uid="b"),
    ))
    assert len(rows) == 2


def test_an_empty_calendar_is_not_an_error():
    assert imported(feed()) == []


# ── Priority ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("due,expected", [
    ("20260908", "High"),    # overdue
    ("20260911", "High"),    # tomorrow
    ("20260913", "High"),    # 3 days
    ("20260915", "Medium"),  # 5 days
    ("20260917", "Medium"),  # 7 days
    ("20260925", "Low"),     # far out
])
def test_priority_tracks_urgency(due, expected):
    assert imported(feed(event(f"T [C]", due)))[0]["priority"] == expected


# ── The URL a student actually pastes ────────────────────────────────


def test_a_webcal_link_is_rewritten_to_https():
    """Canvas hands out webcal://. No HTTP client speaks it, and the calendar
    apps it is meant for rewrite it silently, so students have no reason to
    think it is unusual."""
    assert ics_feed.normalize_feed_url(
        "webcal://x.instructure.com/feeds/calendars/user_abc.ics"
    ) == "https://x.instructure.com/feeds/calendars/user_abc.ics"


def test_a_bare_host_gets_a_scheme():
    assert ics_feed.normalize_feed_url("x.instructure.com/feeds/c.ics").startswith("https://")


def test_surrounding_whitespace_is_forgiven():
    assert ics_feed.normalize_feed_url("  https://x.test/a.ics \n") == "https://x.test/a.ics"


def test_an_empty_url_asks_for_one():
    with pytest.raises(ics_feed.FeedError):
        ics_feed.normalize_feed_url("   ")


# ── Fetch failures are explanations, not stack traces ────────────────


class _Resp:
    def __init__(self, status=200, content=b""):
        self.status_code = status
        self.content = content


class _Session:
    def __init__(self, result):
        self._result = result

    def get(self, *a, **k):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def test_an_unreachable_feed_explains_itself():
    with pytest.raises(ics_feed.FeedError, match="Could not reach"):
        ics_feed.fetch_feed("https://x.test/a.ics",
                            session=_Session(requests.ConnectionError()))


def test_a_stale_feed_link_says_it_was_reset():
    """Canvas issues a new URL when a student resets their feed, and the old
    one 404s. "Something went wrong" would leave them re-pasting the dead
    link forever."""
    with pytest.raises(ics_feed.FeedError, match="no longer valid"):
        ics_feed.fetch_feed("https://x.test/a.ics", session=_Session(_Resp(404)))


def test_a_server_error_names_the_status():
    with pytest.raises(ics_feed.FeedError, match="500"):
        ics_feed.fetch_feed("https://x.test/a.ics", session=_Session(_Resp(500)))


def test_a_page_that_is_not_a_calendar_is_caught():
    """Pasting the Canvas calendar *page* instead of the feed is the obvious
    mistake, and it returns a cheerful 200 of HTML."""
    with pytest.raises(ics_feed.FeedError, match="did not return a calendar"):
        ics_feed.fetch_feed("https://x.test/calendar",
                            session=_Session(_Resp(200, b"<html>Calendar</html>")))


def test_a_real_feed_is_accepted():
    body = feed(event("Essay [Hist]", "20260915")).encode()
    assert "BEGIN:VCALENDAR" in ics_feed.fetch_feed(
        "https://x.test/a.ics", session=_Session(_Resp(200, body)))


def test_an_oversized_feed_is_truncated_rather_than_swallowed():
    huge = b"BEGIN:VCALENDAR\r\n" + b"X" * (ics_feed.MAX_FEED_BYTES * 2)
    text = ics_feed.fetch_feed("https://x.test/a.ics", session=_Session(_Resp(200, huge)))
    assert len(text) <= ics_feed.MAX_FEED_BYTES


def test_import_assignments_goes_end_to_end():
    body = feed(event("Essay 3 [AP US History]", "20260915")).encode()
    rows = ics_feed.import_assignments(
        "webcal://x.test/a.ics", today=TODAY, session=_Session(_Resp(200, body)))
    assert [r["title"] for r in rows] == ["Essay 3"]
