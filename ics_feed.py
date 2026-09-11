"""Import assignments from a calendar feed, with no Developer Key involved.

Why this exists
---------------
Canvas OAuth is per school: a Developer Key only works against the Canvas
instance that issued it, so "Continue with Canvas" can only ever light up
for schools whose own admin has registered IntelliPlan. There is no global
key and no way around that from our side -- it is Instructure's design, not
a gap we can patch.

The personal access token fallback works everywhere, but it costs a student
seven steps in a settings page they have never opened, and every step is
somewhere to give up.

A calendar feed costs one. Every Canvas user already has a personal feed URL
(Canvas -> Calendar -> "Calendar Feed"), and it needs no admin, no key, no
token creation, and behaves identically on every Canvas instance. Copying one
URL is the whole flow.

What it is not
--------------
A feed carries what is due and when. It does not carry grades, scores,
submission state, or points possible -- Canvas simply does not put them in
the ICS. So this is the fastest way to get a student planning on day one,
not a replacement for a token. Anyone who wants the gradebook still needs
one, and the UI should say so rather than let them discover it missing.

Scope
-----
Deliberately generic RFC 5545, not Canvas-specific parsing: the same feed
URL shape is what Blackboard, Moodle, and Google Calendar hand out, so an
importer that only understands the spec works for all of them. The one
Canvas-shaped concession is the ``Title [Course Name]`` summary convention,
which is applied only when it matches and left alone when it does not.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin

import requests

import net_guard

#: Feeds are small (a term of assignments), so a low ceiling is safe and
#: stops a hostile or misconfigured URL from streaming forever into memory.
MAX_FEED_BYTES = 4 * 1024 * 1024

#: Anything older than this is history the planner cannot act on.
DEFAULT_LOOKBACK_DAYS = 14

PRIORITY_COLORS = {"High": "#ef4444", "Medium": "#f59e0b", "Low": "#22c55e"}


class FeedError(Exception):
    """Something the student can act on: a bad URL, an unreachable host."""


# ── RFC 5545 plumbing ────────────────────────────────────────────────


def _unfold(text):
    """Undo line folding.

    RFC 5545 wraps long lines by inserting CRLF followed by one space or
    tab. A 90-character assignment title arrives split across two lines, so
    parsing without this truncates every long title at column 75.
    """
    return re.sub(r"\r?\n[ \t]", "", text.replace("\r\n", "\n"))


def _unescape(value):
    """Reverse the TEXT escaping in RFC 5545 section 3.3.11."""
    out = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append({"n": "\n", "N": "\n"}.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _split_property(line):
    """Return (name, params, value) for one unfolded content line.

    The name may carry parameters (``DTSTART;VALUE=DATE:20260910``) and the
    value may itself contain colons (a URL in DESCRIPTION), so the split is
    on the first colon only, and parameters come off the name side.
    """
    colon = line.find(":")
    if colon == -1:
        return None, {}, ""
    head, value = line[:colon], line[colon + 1:]
    parts = head.split(";")
    name = parts[0].upper()
    params = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.upper()] = v.strip('"')
    return name, params, value


def _parse_dt(value, params):
    """An ICS date/date-time to a ``date``, or None.

    Only the date is kept. A feed states due times in whichever zone the
    school set, and the planner schedules by day, so carrying a time here
    would imply a precision that is not really there.
    """
    value = (value or "").strip()
    if not value:
        return None
    if params.get("VALUE") == "DATE" or len(value) == 8:
        try:
            return datetime.strptime(value[:8], "%Y%m%d").date()
        except ValueError:
            return None
    try:
        if value.endswith("Z"):
            return datetime.strptime(value[:15], "%Y%m%dT%H%M%S").replace(
                tzinfo=timezone.utc).date()
        return datetime.strptime(value[:15], "%Y%m%dT%H%M%S").date()
    except ValueError:
        return None


def parse_events(ics_text):
    """Every VEVENT in the feed, as plain dicts of its properties."""
    events = []
    current = None
    for line in _unfold(ics_text or "").split("\n"):
        line = line.rstrip()
        if not line:
            continue
        if line.upper() == "BEGIN:VEVENT":
            current = {}
            continue
        if line.upper() == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
            continue
        if current is None:
            continue
        name, params, value = _split_property(line)
        if name:
            current[name] = (params, value)
    return events


# ── Feed events to assignments ───────────────────────────────────────

#: Canvas writes "Essay 3 [AP US History]". Course last, in square
#: brackets, at the very end.
_COURSE_SUFFIX = re.compile(r"^(?P<title>.*?)\s*\[(?P<course>[^\[\]]+)\]\s*$")


def split_title_and_course(summary):
    """Canvas's ``Title [Course]`` convention, applied only when it fits.

    A title that genuinely ends in brackets -- "Problem Set 4 [revised]" --
    is indistinguishable from the convention, and guessing wrong renames the
    assignment. Treating the bracketed part as a course is still the better
    bet: it is what Canvas actually emits, and a course label that reads
    "revised" is a smaller error than a title that loses its suffix.
    """
    summary = (summary or "").strip()
    m = _COURSE_SUFFIX.match(summary)
    if not m:
        return summary, ""
    title = m.group("title").strip()
    course = m.group("course").strip()
    if not title:
        # "[Something]" alone: nothing left to call the assignment.
        return summary, ""
    return title, course


def _priority_for(days_until_due):
    if days_until_due is None:
        return "Low"
    if days_until_due < 0 or days_until_due <= 3:
        return "High"
    if days_until_due <= 7:
        return "Medium"
    return "Low"


def events_to_assignments(events, today=None, lookback_days=DEFAULT_LOOKBACK_DAYS):
    """Turn parsed VEVENTs into the assignment shape the planner consumes.

    Matches canvas_helper.get_assignments so the rest of the app cannot tell
    which source a task arrived from. The fields a feed cannot know
    (points, score, submission types) are left at their empty defaults
    rather than invented -- the estimator already handles a task whose size
    is unknown, and a fabricated points value would flow into priority and
    quietly distort the plan.
    """
    today = today or date.today()
    cutoff = today - timedelta(days=lookback_days)
    out = []
    seen = set()

    for ev in events:
        summary = _unescape(ev.get("SUMMARY", ({}, ""))[1]).strip()
        if not summary:
            continue

        params, raw = ev.get("DTSTART", ({}, ""))
        due = _parse_dt(raw, params)
        if due is None:
            params, raw = ev.get("DUE", ({}, ""))
            due = _parse_dt(raw, params)
        if due is None or due < cutoff:
            continue

        title, course = split_title_and_course(summary)
        uid = _unescape(ev.get("UID", ({}, ""))[1]).strip()

        # A feed can legitimately repeat an event across refreshes, and a
        # student may hold two feeds that overlap. Key on identity, falling
        # back to title+date when the feed omits UID.
        key = uid or f"{title}|{due.isoformat()}"
        if key in seen:
            continue
        seen.add(key)

        days = (due - today).days
        priority = _priority_for(days)
        out.append({
            "id": uid,
            "course_id": "",
            "title": title,
            "course": course or "Calendar",
            "due_date": due.isoformat(),
            "points_possible": None,
            "priority": priority,
            "estimated_time": None,
            "display_score": "",
            "color": PRIORITY_COLORS.get(priority, "#60a5fa"),
            "description": _unescape(ev.get("DESCRIPTION", ({}, ""))[1])[:4000],
            "submission_types": [],
            "rubric": 0,
            "quiz_id": None,
            "is_quiz": False,
            "source": "calendar_feed",
        })

    return sorted(out, key=lambda a: a["due_date"])


# ── Fetching ─────────────────────────────────────────────────────────


def normalize_feed_url(url):
    """Accept what a student actually copies.

    Canvas hands out ``webcal://`` links, which no HTTP client speaks; the
    calendar apps they are meant for rewrite them silently, so a student has
    no reason to think it is not a normal URL.
    """
    url = (url or "").strip()
    if not url:
        raise FeedError("Paste your calendar feed URL.")
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    return url


#: The same message for a URL we refuse and one that does not resolve. A
#: student's next move is identical, and any finer distinction would report
#: back whether an internal host exists.
_UNREACHABLE = "Could not reach that calendar feed. Check the link and try again."


def fetch_feed(url, timeout=20, session=None, host_check=None):
    """Download a calendar feed and return its text.

    The URL comes from whoever is at the keyboard and we fetch it from the
    server, so every hop is checked against ``host_check`` before the
    request. Redirects are followed by hand rather than by requests: a
    perfectly public host is free to 302 to 169.254.169.254, and automatic
    redirects would follow it with no second look.

    Errors are phrased for the student, not the log: at this point they have
    pasted something and want to know whether it worked.
    """
    url = normalize_feed_url(url)
    check = net_guard.resolves_to_public_host if host_check is None else host_check
    getter = (session or requests).get

    resp = None
    for _ in range(net_guard.MAX_REDIRECTS + 1):
        if not check(url):
            raise FeedError(_UNREACHABLE)
        try:
            resp = getter(url, timeout=timeout, stream=True, allow_redirects=False)
        except requests.RequestException:
            raise FeedError(_UNREACHABLE)
        if resp.status_code not in (301, 302, 303, 307, 308):
            break
        location = (getattr(resp, "headers", None) or {}).get("Location")
        if not location:
            break
        url = normalize_feed_url(urljoin(url, location))
    else:
        raise FeedError(
            "That calendar feed redirected too many times. Copy the link "
            "again from your school's calendar."
        )

    if resp.status_code == 404:
        raise FeedError(
            "That feed link is no longer valid. Canvas resets it if you reset "
            "your calendar feed, so copy the current one."
        )
    if resp.status_code >= 400:
        raise FeedError(
            f"That calendar feed returned an error ({resp.status_code}). "
            "Copy the link again from Canvas → Calendar → Calendar Feed."
        )

    body = resp.content[:MAX_FEED_BYTES] if hasattr(resp, "content") else b""
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
    if "BEGIN:VCALENDAR" not in text.upper():
        raise FeedError(
            "That link did not return a calendar. In Canvas, open Calendar, "
            "click Calendar Feed, and copy the whole link."
        )
    return text


def import_assignments(url, today=None, session=None, host_check=None):
    """Fetch a feed and return planner-ready assignments."""
    return events_to_assignments(
        parse_events(fetch_feed(url, session=session, host_check=host_check)),
        today=today,
    )
