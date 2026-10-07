"""Rules for tracking a course a student takes somewhere else.

A student can paste a link to a Khan Academy unit, a Coursera course, an
edX class or anything else, set a weekly time goal, and IntelliPlan holds
them to it. This module is the part with no Flask, no database and no
clock: which site a link belongs to, what counts as a week, and whether a
student is on pace.

Where progress comes from
-------------------------
Khan Academy closed its public API and Coursera only shares progress with
enterprise customers, so IntelliPlan cannot ask either site how a student
is doing. Two sources remain, and they are kept apart on purpose:

* ``self``      -- the student logged minutes here.
* ``extension`` -- the IntelliPlan browser extension read a completion
                   percentage off the course page in the student's own
                   browser.

Only the second is called "verified" anywhere in the product. A number a
student typed is never presented as something IntelliPlan checked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable
from urllib.parse import quote_plus, urlsplit, urlunsplit

MAX_URL_LENGTH = 500
MAX_TITLE_LENGTH = 160
MIN_WEEKLY_GOAL = 15
MAX_WEEKLY_GOAL = 20 * 60
DEFAULT_WEEKLY_GOAL = 120
MAX_CHECKIN_MINUTES = 8 * 60
MAX_ACTIVE_COURSES = 12

SOURCE_SELF = "self"
SOURCE_EXTENSION = "extension"


@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    #: Host suffixes that identify the site.
    hosts: tuple[str, ...]
    #: ``{q}`` is replaced with the URL-encoded subject. IntelliPlan owns
    #: this string; no model ever writes a course URL.
    search_template: str = ""
    good_for: str = ""


PROVIDERS: tuple[Provider, ...] = (
    Provider(
        "khan", "Khan Academy", ("khanacademy.org",),
        "https://www.khanacademy.org/search?page_search_query={q}",
        "Free practice and short lessons for school maths, science and test prep.",
    ),
    Provider(
        "coursera", "Coursera", ("coursera.org",),
        "https://www.coursera.org/search?query={q}",
        "University courses with weekly modules. Free to audit; certificates cost money.",
    ),
    Provider(
        "edx", "edX", ("edx.org",),
        "https://www.edx.org/search?q={q}",
        "University courses, many free to audit.",
    ),
    Provider(
        "mitocw", "MIT OpenCourseWare", ("ocw.mit.edu",),
        "https://ocw.mit.edu/search/?q={q}",
        "Full MIT course materials, free, no account.",
    ),
    Provider("udemy", "Udemy", ("udemy.com",)),
    Provider("codecademy", "Codecademy", ("codecademy.com",)),
    Provider("youtube", "YouTube", ("youtube.com", "youtu.be")),
)

OTHER = Provider("other", "Other course", ())

_BY_KEY = {p.key: p for p in PROVIDERS}


def provider_for(key: str) -> Provider:
    return _BY_KEY.get(key, OTHER)


def clean_url(raw: object) -> str:
    """An https link with no fragment, or "" when it is not a usable link.

    Scheme-less input gets ``https://`` because that is how people paste
    links. Anything that is not http(s) after that is refused, which is
    what keeps ``javascript:`` out of an ``href``.
    """
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if not text or len(text) > MAX_URL_LENGTH or any(ch.isspace() for ch in text):
        return ""
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or "." not in (parts.hostname or ""):
        return ""
    if parts.username or parts.password:
        return ""
    return urlunsplit(("https", parts.netloc.lower(), parts.path, parts.query, ""))


def detect_provider(url: str) -> Provider:
    host = (urlsplit(url).hostname or "").lower()
    for provider in PROVIDERS:
        if any(host == h or host.endswith("." + h) for h in provider.hosts):
            return provider
    return OTHER


def match_key(url: str) -> str:
    """Host and path, lowercased, without ``www.`` or a trailing slash.

    What the extension's reading is matched against. The query string is
    dropped: the same course page is reached with many different ones.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host + parts.path.rstrip("/").lower()


def same_course(tracked_url: str, seen_url: str) -> bool:
    """Whether a page the extension saw belongs to a tracked course.

    A tracked link is usually the course's front page and the student is
    usually somewhere inside it, so a seen page matches when it is the
    tracked page or sits underneath it.
    """
    tracked, seen = match_key(tracked_url), match_key(seen_url)
    if not tracked or not seen or "/" not in tracked:
        # A bare host ("khanacademy.org") would match every page on the site.
        return tracked == seen and bool(tracked)
    return seen == tracked or seen.startswith(tracked + "/")


def clamp_goal(raw: object) -> int:
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_WEEKLY_GOAL
    return max(MIN_WEEKLY_GOAL, min(MAX_WEEKLY_GOAL, value))


def clean_title(raw: object, url: str = "") -> str:
    text = " ".join(str(raw or "").split())[:MAX_TITLE_LENGTH]
    if text:
        return text
    # No title given: name it after the site and the last path segment.
    provider = detect_provider(url)
    tail = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").strip()
    label = provider.name if provider is not OTHER else (urlsplit(url).hostname or "Course")
    return (f"{label}: {tail.title()}" if tail else label)[:MAX_TITLE_LENGTH]


def week_start(day: date) -> date:
    """The Monday of ``day``'s week. Weeks run Monday to Sunday."""
    return day - timedelta(days=day.weekday())


def week_end(day: date) -> date:
    return week_start(day) + timedelta(days=6)


@dataclass(frozen=True)
class WeekStatus:
    minutes: int
    goal: int
    remaining: int
    percent: int
    days_left: int
    #: "done" | "on_track" | "behind" | "not_started"
    state: str
    message: str


def week_status(minutes_this_week: int, goal: int, today: date,
                started: date | None = None) -> WeekStatus:
    """Where a student stands against this week's goal.

    "Behind" means behind an even pace: by the end of day N of 7 an even
    pace has done N/7 of the goal. It is deliberately not triggered on a
    Monday with nothing logged, which is "not started", because telling
    someone they are behind before the week has had a chance is noise.

    ``started`` is the day the course was added. In the week it was added
    the pace runs from that day, not from Monday: a course added on a
    Thursday is not three days behind the moment it appears.
    """
    minutes = max(0, int(minutes_this_week or 0))
    goal = max(1, int(goal or DEFAULT_WEEKLY_GOAL))
    remaining = max(0, goal - minutes)
    percent = min(100, round(100 * minutes / goal))
    day_number = today.weekday() + 1
    days_left = 7 - day_number
    first_day = 1
    if started is not None and week_start(started) == week_start(today):
        first_day = started.weekday() + 1
    span = 7 - first_day + 1
    elapsed = max(0, day_number - first_day)  # full days already gone

    if remaining == 0:
        return WeekStatus(minutes, goal, 0, 100, days_left, "done",
                          "This week's goal is met.")
    expected_by_yesterday = goal * elapsed / span
    if minutes == 0 and elapsed <= 1:
        return WeekStatus(minutes, goal, remaining, 0, days_left, "not_started",
                          f"{remaining} min to do this week.")
    if minutes >= expected_by_yesterday:
        return WeekStatus(minutes, goal, remaining, percent, days_left, "on_track",
                          f"On pace. {remaining} min left this week.")
    when = "today" if days_left == 0 else f"in the next {days_left + 1} days"
    return WeekStatus(minutes, goal, remaining, percent, days_left, "behind",
                      f"Behind pace. {remaining} min left, due {when}.")


def weeks_met_in_a_row(weekly_minutes: Iterable[tuple[date, int]], goal: int,
                       today: date) -> int:
    """Consecutive completed weeks ending with the most recent one.

    The current week counts once its goal is met and is skipped, not
    treated as a miss, while it is still in progress.
    """
    by_week = {week_start(day): int(total or 0) for day, total in weekly_minutes}
    goal = max(1, int(goal or DEFAULT_WEEKLY_GOAL))
    cursor = week_start(today)
    streak = 0
    if by_week.get(cursor, 0) >= goal:
        streak += 1
    cursor -= timedelta(days=7)
    while by_week.get(cursor, 0) >= goal:
        streak += 1
        cursor -= timedelta(days=7)
    return streak


def clamp_percent(raw: object) -> float | None:
    """A completion percentage from 0 to 100, or None when unusable."""
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if value != value or value < 0 or value > 100:  # NaN or out of range
        return None
    return round(value, 1)


def recommendations(subject: object, limit: int = 4) -> list[dict[str, str]]:
    """Places to look for a course on ``subject``.

    Search links on each provider's own site, built here. A model asked
    for course URLs produces ones that look right and 404; a search page
    for the subject always exists.
    """
    text = " ".join(str(subject or "").split())[:80]
    if not text:
        return []
    out = []
    for provider in PROVIDERS:
        if not provider.search_template:
            continue
        out.append({
            "provider": provider.key,
            "name": provider.name,
            "url": provider.search_template.replace("{q}", quote_plus(text)),
            "why": provider.good_for,
        })
    return out[:limit]
