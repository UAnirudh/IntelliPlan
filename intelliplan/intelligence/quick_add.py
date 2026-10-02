"""Turning "bio lab due fri 2h" into a task, instantly and without a model.

A teacher says "quiz Friday" and the student has about three seconds to get it
down before the next sentence. Whatever captures it has to answer before they
look up again, work offline, and never hallucinate a due date — which rules out
a language model on the fast path. ``/api/tasks/extract`` already exists for
long, messy pastes; this is the other end: one line, typed in a hurry.

What it understands
-------------------
* **Dates.** ``today``/``tonight``, ``tmrw``/``tomorrow`` and their spellings,
  weekday names and abbreviations (``fri``, ``thurs``), ``this fri``,
  ``next tue``, ``in 3 days``, ``in 2 weeks``, ``10/14``, ``10/14/26``,
  ``2026-10-14``, ``oct 14``, ``14 oct``, ``the 14th``. Lead-in words (``due``,
  ``by``, ``on``) are consumed with the date.
* **Times.** ``at 3pm``, ``3:30pm``, ``by 11:59 pm``, ``at 15:00``, ``noon``,
  ``midnight``. Stored as ``HH:MM``; most school work has no time, so this is
  optional.
* **Durations.** ``2h``, ``1.5 hrs``, ``45m``, ``90 min``, ``1h30``,
  ``1h 30m``, ``for 2 hours``. This is how long the *work* takes, not when it
  is due.
* **Course.** Matched against the student's own course names, with the
  abbreviations students actually type (``bio``, ``chem``, ``apush``,
  ``calc``). ``#bio`` forces a match. An abbreviation that fits two courses
  matches neither: guessing the wrong class is worse than asking.
* **Type.** ``test``, ``quiz``, ``exam``, ``midterm``, ``final``, ``lab``,
  ``essay``, ``paper``, ``project``, ``presentation``, ``reading``,
  ``worksheet``. Feeds the size estimate when no duration was typed.
* **Priority.** ``p1``–``p4`` and ``!!!``/``!!``/``!``.

What the words mean (the decisions)
-----------------------------------
* A bare weekday is the *next* one, never today. "Quiz Friday" said on a
  Friday is next week's quiz; today's would be "quiz today". ``this fri``
  includes today.
* ``next tue`` is Tuesday of next calendar week (weeks start Monday). That is
  what "next Tuesday" means to most people on a Monday, and on a Saturday it
  coincides with plain "tue".
* ``10/14`` is month/day (US school calendars). A date already more than a
  week gone rolls to next year; a few days gone stays put, because capturing
  something that is already overdue is a real thing students do.
* Course words stay in the title ("Bio lab" reads better than "lab"); only a
  ``#tag`` is removed.

Purity
------
No Flask, no ORM, no network. The caller supplies "now" in the student's own
timezone (:func:`time_utils.local_now`) — the parser never reads a clock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Sequence

__all__ = [
    "QuickAdd",
    "parse_quick_add",
    "course_aliases",
    "planner_kind",
    "infer_kind",
    "MAX_MINUTES",
]

#: A typed duration above this is a typo ("200h"), not a plan.
MAX_MINUTES = 600

_WEEKDAYS = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}
_WEEKDAY_RE = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))

_TODAY_WORDS = ("today", "tonight", "tonite", "tod")
_TOMORROW_WORDS = ("tomorrow", "tomorow", "tommorow", "tommorrow", "tmrw",
                   "tmr", "tmw", "tmrow", "tomo", "2moro", "2morrow")

#: Words that introduce a date and are noise once the date is taken.
_LEAD = r"(?:(?:due|by|on|for|before)\s+)*"

#: Type words → the kind shown to the student. Order matters: "final exam"
#: should read as an exam, and "lab report" as a lab.
_KIND_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("exam", ("exam", "exams", "midterm", "midterms", "final", "finals")),
    ("test", ("test", "tests", "unit test", "chapter test")),
    ("quiz", ("quiz", "quizzes", "pop quiz")),
    ("lab", ("lab", "labs", "lab report", "practical")),
    ("presentation", ("presentation", "slides", "speech", "pitch")),
    ("essay", ("essay", "paper", "draft", "dbq", "leq", "saq", "frq",
               "reflection", "write-up", "writeup")),
    ("project", ("project", "poster", "portfolio", "model")),
    ("reading", ("read", "reading", "chapter", "ch", "pages", "pp")),
    ("homework", ("worksheet", "homework", "hw", "problem set", "pset",
                  "problems", "assignment")),
)

#: Kind → the scheduler's own kind vocabulary (see ``_planner_task_rows``).
_PLANNER_KIND = {
    "exam": "exam", "test": "test", "quiz": "test", "lab": "lab",
    "essay": "project", "project": "project", "presentation": "project",
    "reading": "homework", "homework": "homework",
}

#: Abbreviation → the word it stands for in a course name. Students type the
#: left side; course rosters say the right side.
_ABBREVIATIONS = {
    "bio": ("biology",), "chem": ("chemistry",), "phys": ("physics",),
    "calc": ("calculus",), "precalc": ("precalculus", "pre-calculus"),
    "alg": ("algebra",), "geom": ("geometry",), "trig": ("trigonometry",),
    "stats": ("statistics",), "stat": ("statistics",),
    "lit": ("literature",), "eng": ("english",), "lang": ("language",),
    "hist": ("history",), "gov": ("government",), "govt": ("government",),
    "econ": ("economics",), "psych": ("psychology",), "soc": ("sociology",),
    "span": ("spanish",), "esp": ("spanish",), "fren": ("french",),
    "ger": ("german",), "lat": ("latin",), "cs": ("computer science",),
    "compsci": ("computer science",), "csp": ("computer science principles",),
    "apes": ("environmental science",), "enviro": ("environmental",),
    "apush": ("us history", "united states history", "u.s. history"),
    "apwh": ("world history",), "apeuro": ("european history",),
    "apgov": ("government",), "apbio": ("biology",), "apchem": ("chemistry",),
    "apcalc": ("calculus",), "apstats": ("statistics",), "apcsa": ("computer science",),
    "pe": ("physical education",), "health": ("health",), "art": ("art",),
    "music": ("music",), "anat": ("anatomy",), "astro": ("astronomy",),
    "philo": ("philosophy",), "anthro": ("anthropology",),
}

#: Words that appear in many course names and identify none of them.
_COURSE_FILLER = frozenset({
    "ap", "ib", "hl", "sl", "honors", "honours", "hon", "h", "accelerated",
    "advanced", "intro", "introduction", "to", "of", "the", "and", "&", "in",
    "for", "a", "an", "i", "ii", "iii", "iv", "1", "2", "3", "4", "period",
    "per", "section", "sec", "block", "class", "course", "dual", "enrollment",
    "de", "cp", "college", "prep", "level", "grade", "semester", "sem", "year",
    "fall", "spring", "s1", "s2", "q1", "q2", "q3", "q4", "general", "gen",
    "studies", "study", "principles", "fundamentals", "topics", "special",
})

#: Short words that are also common English in a task title. Never treat them
#: as a course alias on their own — "read ch 4 for art" is fine because "art"
#: is a whole course name, but "lab" must never become a course.
_NEVER_ALIAS = frozenset({
    "lab", "test", "quiz", "exam", "essay", "paper", "project", "read",
    "reading", "due", "by", "on", "fri", "sat", "sun", "mon", "tue", "wed",
    "thu", "today", "tomorrow", "final", "unit", "chapter", "notes", "review",
    "homework", "hw", "worksheet", "study", "work",
})


@dataclass(frozen=True)
class QuickAdd:
    """One parsed line. ``matched`` records what was understood, for the
    preview chips ("Fri", "2h", "Biology") the capture box shows as you type."""

    title: str
    due_date: date | None = None
    due_time: time | None = None
    minutes: int | None = None
    course: str = ""
    kind: str = ""
    priority: str = ""
    matched: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def planner_kind(self) -> str:
        return planner_kind(self.kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "due_date": self.due_date.isoformat() if self.due_date else "",
            "due_time": self.due_time.strftime("%H:%M") if self.due_time else "",
            "minutes": self.minutes,
            "course": self.course,
            "kind": self.kind,
            "priority": self.priority,
            "matched": [{"kind": k, "text": t} for k, t in self.matched],
        }


def planner_kind(kind: str) -> str:
    return _PLANNER_KIND.get((kind or "").strip().lower(), "homework")


# ── Course aliases ────────────────────────────────────────────────────


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w&.\- ]+", " ", str(text or "").lower())).strip()


def course_aliases(courses: Iterable[str]) -> dict[str, set[str]]:
    """``{alias: {course name, ...}}`` for the student's courses.

    An alias pointing at more than one course is kept (with every course it
    could mean) so the matcher can see the ambiguity and refuse it, rather
    than whichever course happened to be listed first winning silently.
    """
    out: dict[str, set[str]] = {}

    def add(alias: str, course: str) -> None:
        alias = alias.strip()
        if len(alias) < 2 or alias in _NEVER_ALIAS:
            return
        out.setdefault(alias, set()).add(course)

    seen: set[str] = set()
    for raw in courses:
        course = re.sub(r"\s+", " ", str(raw or "")).strip()
        if not course or course.lower() in seen:
            continue
        seen.add(course.lower())
        lower = _norm(course)
        # Strip "- Period 3" / "(Smith)" style suffixes before anything else.
        core = re.split(r"\s+-\s+|\(|\bperiod\b|\bper\.?\s*\d", lower)[0].strip() or lower
        add(lower, course)
        add(core, course)
        words = [w.strip(".-") for w in core.split()]
        meaningful = [w for w in words if w and w not in _COURSE_FILLER]
        if meaningful:
            add(" ".join(meaningful), course)
        for w in meaningful:
            if len(w) >= 4 or (len(w) >= 3 and w.isalpha()):
                add(w, course)
        # Course codes: "BIO 101" → "bio101", "bio 101", and "bio".
        code = re.match(r"^([a-z]{2,5})\s*-?\s*(\d{2,4}[a-z]?)\b", core)
        if code:
            add(code.group(1) + code.group(2), course)
            add(f"{code.group(1)} {code.group(2)}", course)
            add(code.group(1), course)
        for abbr, expansions in _ABBREVIATIONS.items():
            if any(re.search(rf"\b{re.escape(e)}\b", lower) for e in expansions):
                add(abbr, course)
    return out


# ── Parsing helpers ───────────────────────────────────────────────────


class _Text:
    """The input with consumed spans blanked out, so later passes cannot
    re-read "fri" as part of a title or "14" as a duration."""

    def __init__(self, raw: str):
        self.raw = raw
        self.lower = raw.lower()
        self.taken = [False] * len(raw)

    def free(self, start: int, end: int) -> bool:
        return not any(self.taken[start:end])

    def take(self, start: int, end: int) -> None:
        for i in range(start, end):
            self.taken[i] = True

    def first(self, pattern: re.Pattern[str]):
        for m in pattern.finditer(self.lower):
            if self.free(m.start(), m.end()):
                return m
        return None

    def remainder(self) -> str:
        chars = [c if not self.taken[i] else " " for i, c in enumerate(self.raw)]
        return "".join(chars)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _roll_year(candidate: date | None, today: date, explicit_year: bool) -> date | None:
    if candidate is None or explicit_year:
        return candidate
    if candidate < today - timedelta(days=7):
        return _safe_date(candidate.year + 1, candidate.month, candidate.day)
    return candidate


def _full_year(value: str | None, today: date) -> int | None:
    if not value:
        return None
    year = int(value)
    if year < 100:
        year += (today.year // 100) * 100
    return year


_RE_REL_DAY = re.compile(
    rf"\b{_LEAD}(?P<word>day after (?:tomorrow|tmrw)|{'|'.join(_TOMORROW_WORDS + _TODAY_WORDS)})\b"
)
_RE_IN_N = re.compile(
    rf"\b{_LEAD}in\s+(?P<n>\d{{1,3}}|a|an|one|two|three|four|five|six|seven)\s+"
    r"(?P<unit>days?|weeks?|wks?)\b"
)
_RE_NEXT_WEEK = re.compile(rf"\b{_LEAD}(?P<which>next|this)\s+week(?:end)?\b")
_RE_WEEKDAY = re.compile(
    rf"\b{_LEAD}(?:(?P<mod>next|this|coming)\s+)?(?P<day>{_WEEKDAY_RE})\.?(?![\w/])"
)
_RE_ISO = re.compile(rf"\b{_LEAD}(?P<y>\d{{4}})-(?P<m>\d{{1,2}})-(?P<d>\d{{1,2}})\b")
_RE_SLASH = re.compile(
    rf"(?<![\w/]){_LEAD}(?P<m>\d{{1,2}})/(?P<d>\d{{1,2}})(?:/(?P<y>\d{{2}}|\d{{4}}))?(?![\w/])"
)
_RE_MONTH_DAY = re.compile(
    rf"\b{_LEAD}(?P<mon>{_MONTH_RE})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?"
    r"(?:,?\s+(?P<y>\d{4}))?\b"
)
_RE_DAY_MONTH = re.compile(
    rf"\b{_LEAD}(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<mon>{_MONTH_RE})\b\.?"
    r"(?:,?\s+(?P<y>\d{4}))?"
)
_RE_ORDINAL = re.compile(rf"\b{_LEAD}the\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)\b")

_RE_TIME_AMPM = re.compile(
    r"\b(?:(?:at|by|@|due)\s+)*(?P<h>\d{1,2})(?::(?P<m>[0-5]\d))?\s*(?P<ap>a\.?m\.?|p\.?m\.?|am|pm|a|p)(?![\w])"
)
_RE_TIME_24 = re.compile(r"\b(?:at|by|@)\s+(?P<h>[01]?\d|2[0-3]):(?P<m>[0-5]\d)\b")
_RE_TIME_WORD = re.compile(r"\b(?:(?:at|by)\s+)?(?P<w>noon|midnight)\b")

_RE_DURATION = re.compile(
    r"(?<![\w/:])(?:for\s+)?(?:"
    r"(?P<h>\d+(?:\.\d+)?)\s*(?:hours|hour|hrs|hr|h)(?![a-z])"
    r"(?:\s*(?P<hm>\d{1,2})\s*(?:minutes|minute|mins|min|m)?\b)?"
    r"|(?P<m>\d{1,3})\s*(?:m|min|mins|minutes?)\b"
    r"|(?P<half>half\s+(?:an?\s+)?hour)\b"
    r")"
)
_RE_PRIORITY = re.compile(r"(?<!\S)(?:p(?P<p>[1-4])|(?P<bang>!{1,3}))(?!\S)")
_RE_URGENT = re.compile(r"\b(?:urgent|asap|important)\b")
_RE_HASHTAG = re.compile(r"(?<![\w#])#(?P<tag>[\w&.-]{2,40})")

_WORD_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
                 "five": 5, "six": 6, "seven": 7}


def _parse_date(text: _Text, today: date) -> tuple[date | None, str]:
    """First date expression in ``text``; consumes it. Returns (date, label)."""
    candidates = []
    for pattern in (_RE_REL_DAY, _RE_IN_N, _RE_NEXT_WEEK, _RE_ISO, _RE_SLASH,
                    _RE_MONTH_DAY, _RE_DAY_MONTH, _RE_WEEKDAY, _RE_ORDINAL):
        m = text.first(pattern)
        if m:
            candidates.append((m.start(), pattern, m))
    if not candidates:
        return None, ""
    # The leftmost expression wins; the rest stay in the title. "Study for
    # the 10/14 test due fri" is due Friday… but that reading needs a model,
    # and a predictable rule beats a clever one in a three-second capture box.
    _, pattern, m = min(candidates, key=lambda c: c[0])
    result: date | None = None
    if pattern is _RE_REL_DAY:
        word = m.group("word")
        if word.startswith("day after"):
            result = today + timedelta(days=2)
        elif word in _TODAY_WORDS:
            result = today
        else:
            result = today + timedelta(days=1)
    elif pattern is _RE_IN_N:
        raw = m.group("n")
        n = int(raw) if raw.isdigit() else _WORD_NUMBERS.get(raw, 1)
        days = n * 7 if m.group("unit").startswith("w") else n
        result = today + timedelta(days=min(days, 366))
    elif pattern is _RE_NEXT_WEEK:
        monday = today - timedelta(days=today.weekday())
        if m.group("which") == "next":
            monday += timedelta(days=7)
        weekend = m.group(0).rstrip().endswith("end")
        result = monday + timedelta(days=5 if weekend else 4)
        if result < today:
            result = today
    elif pattern is _RE_ISO:
        result = _safe_date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
    elif pattern is _RE_SLASH:
        year = _full_year(m.group("y"), today)
        result = _roll_year(
            _safe_date(year or today.year, int(m.group("m")), int(m.group("d"))),
            today, year is not None,
        )
    elif pattern in (_RE_MONTH_DAY, _RE_DAY_MONTH):
        year = _full_year(m.group("y"), today)
        result = _roll_year(
            _safe_date(year or today.year, _MONTHS[m.group("mon")], int(m.group("d"))),
            today, year is not None,
        )
    elif pattern is _RE_WEEKDAY:
        target = _WEEKDAYS[m.group("day")]
        mod = m.group("mod") or ""
        if mod == "next":
            monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
            result = monday + timedelta(days=target)
        else:
            ahead = (target - today.weekday()) % 7
            if ahead == 0 and mod != "this":
                ahead = 7
            result = today + timedelta(days=ahead)
    elif pattern is _RE_ORDINAL:
        day = int(m.group("d"))
        result = _safe_date(today.year, today.month, day)
        if result is None or result < today:
            nxt = today.replace(day=1) + timedelta(days=32)
            result = _safe_date(nxt.year, nxt.month, day)
    if result is None:
        # "2/30" is not a date. Leave the text alone rather than eat it.
        return None, ""
    text.take(m.start(), m.end())
    return result, m.group(0).strip()


def _parse_time(text: _Text) -> tuple[time | None, str]:
    m = text.first(_RE_TIME_WORD)
    if m:
        text.take(m.start(), m.end())
        return (time(12, 0) if m.group("w") == "noon" else time(23, 59)), m.group(0).strip()
    for pattern in (_RE_TIME_AMPM, _RE_TIME_24):
        m = text.first(pattern)
        if not m:
            continue
        hour = int(m.group("h"))
        minute = int(m.group("m") or 0)
        if pattern is _RE_TIME_AMPM:
            ap = m.group("ap")[0]
            # A bare "4p"/"4a" is only a time with a lead-in ("at 4p"), or
            # "period 4a" and "2a" (problem 2a) become clock times.
            if len(m.group("ap").replace(".", "")) == 1 and not re.match(r"(at|by|@|due)\s", m.group(0)):
                continue
            if not 1 <= hour <= 12:
                continue
            hour = hour % 12 + (12 if ap == "p" else 0)
        text.take(m.start(), m.end())
        return time(hour, minute), m.group(0).strip()
    return None, ""


def _parse_duration(text: _Text) -> tuple[int | None, str]:
    m = text.first(_RE_DURATION)
    if not m:
        return None, ""
    if m.group("half"):
        minutes = 30
    elif m.group("h") is not None:
        minutes = int(round(float(m.group("h")) * 60)) + int(m.group("hm") or 0)
    else:
        minutes = int(m.group("m"))
    if not 1 <= minutes <= MAX_MINUTES:
        return None, ""
    text.take(m.start(), m.end())
    return minutes, m.group(0).strip()


def _parse_priority(text: _Text) -> tuple[str, str]:
    m = text.first(_RE_PRIORITY)
    if m:
        text.take(m.start(), m.end())
        if m.group("p"):
            level = {"1": "High", "2": "Medium", "3": "Low", "4": "Low"}[m.group("p")]
        else:
            level = {3: "High", 2: "Medium", 1: "Low"}[len(m.group("bang"))]
        return level, m.group(0)
    if _RE_URGENT.search(text.lower):
        # Left in the title: "urgent" is part of what the student wrote.
        return "High", "urgent"
    return "", ""


def _parse_kind(lower: str) -> str:
    for kind, words in _KIND_WORDS:
        for word in words:
            if re.search(rf"\b{re.escape(word)}\b", lower):
                return kind
    return ""


def infer_kind(text: str) -> str:
    """The type word in ``text`` ("lab", "quiz", "essay"…), or ``""``."""
    return _parse_kind(str(text or "").lower())


def _match_course(text: _Text, aliases: dict[str, set[str]]) -> tuple[str, str]:
    m = text.first(_RE_HASHTAG)
    if m:
        tag = m.group("tag").lower().replace("_", " ")
        hits = aliases.get(tag) or set()
        if len(hits) != 1:
            # "#bio" with no Biology on the roster still names a subject.
            # Prefix match before giving up: "#chem" → "Chemistry Honors".
            hits = {c for a, cs in aliases.items() if a.startswith(tag) for c in cs}
        text.take(m.start(), m.end())
        if len(hits) == 1:
            return next(iter(hits)), m.group(0)
        return m.group("tag"), m.group(0)
    if not aliases:
        return "", ""
    best: tuple[int, str, str] | None = None
    for alias, hits in aliases.items():
        if len(hits) != 1:
            continue
        for found in re.finditer(rf"(?<![\w]){re.escape(alias)}(?![\w])", text.lower):
            if not text.free(found.start(), found.end()):
                continue
            rank = len(alias)
            if best is None or rank > best[0]:
                best = (rank, next(iter(hits)), text.raw[found.start():found.end()])
            break
    if best is None:
        return "", ""
    return best[1], best[2]


def _clean_title(remainder: str) -> str:
    title = re.sub(r"\s+", " ", remainder).strip()
    # Lead-in words orphaned by what was taken out after them ("quiz due").
    title = re.sub(r"(?:\s+(?:due|by|on|at|for|before|@|-|–|,))+$", "", title, flags=re.I)
    title = re.sub(r"^(?:(?:due|by|on|at|add|todo|to-do|task)\b[:\s]*)+", "", title, flags=re.I)
    title = title.strip(" ,;:-–")
    if title and title[0].islower():
        title = title[0].upper() + title[1:]
    return title


# ── Entry point ───────────────────────────────────────────────────────


def parse_quick_add(
    text: str,
    *,
    now: datetime,
    courses: Sequence[str] = (),
    aliases: dict[str, set[str]] | None = None,
) -> QuickAdd:
    """Parse one capture line. ``now`` is the student's local wall clock."""
    raw = re.sub(r"\s+", " ", str(text or "")).strip()[:500]
    if not raw:
        return QuickAdd(title="")
    today = now.date()
    t = _Text(raw)
    matched: list[tuple[str, str]] = []

    priority, ptxt = _parse_priority(t)
    if ptxt and ptxt != "urgent":
        matched.append(("priority", priority))
    elif priority:
        matched.append(("priority", priority))

    # Durations before times and dates: "2h" must not be eaten as a time,
    # and "45m" must not be read as anything else.
    minutes, dtxt = _parse_duration(t)
    if minutes:
        matched.append(("minutes", dtxt))

    due_time, ttxt = _parse_time(t)
    if due_time:
        matched.append(("time", ttxt))

    due, datetxt = _parse_date(t, today)
    if due:
        matched.append(("date", datetxt))
    elif due_time:
        # A bare time means today — or tomorrow if that time has passed.
        due = today if datetime.combine(today, due_time) > now else today + timedelta(days=1)

    table = aliases if aliases is not None else course_aliases(courses)
    course, ctxt = _match_course(t, table)
    if course:
        matched.append(("course", course))

    title = _clean_title(t.remainder())
    if not title:
        # Everything was metadata ("fri 2h"). A task still needs a name the
        # student can recognise and rename, and an empty one cannot be saved.
        title = course or "New task"
    kind = _parse_kind(title.lower()) or _parse_kind(raw.lower())
    if kind:
        matched.append(("kind", kind))

    return QuickAdd(
        title=title[:512],
        due_date=due,
        due_time=due_time,
        minutes=minutes,
        course=course,
        kind=kind,
        priority=priority,
        matched=tuple(matched),
    )
