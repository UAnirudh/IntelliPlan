"""Class timetables, including the rotating kind.

A student's school day is the largest fixed block of time in their week,
and until this existed the scheduler had no idea when it was. Plans could
put an hour of chemistry at 10 AM on a Tuesday, and nothing on the "today"
surfaces could say which room the next class was in or whether it was an A
or a B day.

Most real timetables are not "Period 3 is every weekday". This module
covers the four shapes schools actually use:

* ``none``  — the same classes every school day (weekday filters still apply);
* ``ab``    — alternating A/B days;
* ``cycle`` — an N-day cycle ("Day 1" … "Day 6");
* ``week``  — alternating weeks ("Week 1" / "Week 2").

A/B and N-day cycles advance one step per *school day*: weekends and the
student's no-school days do not consume a rotation day, which is how every
rotating school counts them. Week rotations advance per calendar week and
ignore holidays — a week off is still a week.

Purity
------
No Flask, no ORM, no clock reads. Every function takes the date it is
asked about.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "ROTATION_KINDS",
    "WEEKDAY_ABBR",
    "Meeting",
    "Rotation",
    "SkipDay",
    "busy_by_date",
    "is_school_day",
    "meetings_on",
    "next_meeting",
    "parse_clock",
    "parse_weekdays",
    "rotation_day",
    "rotation_label",
    "skip_reason",
]

ROTATION_KINDS = ("none", "ab", "cycle", "week")
WEEKDAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

#: How far either side of the anchor a rotation will count. A school year is
#: well inside this; anything further out is a data error, and walking a
#: decade of days to answer it would be a slow way to say "unknown".
MAX_ROTATION_SPAN_DAYS = 800
MAX_CYCLE_LENGTH = 10


# ── Parsing helpers ──────────────────────────────────────────────────

_CLOCK_RE = re.compile(r"^\s*(\d{1,2})(?:[:.](\d{2}))?(?::\d{2})?\s*([ap])?\.?\s*m?\.?\s*$", re.I)


def parse_clock(value: Any) -> int | None:
    """``"7:45 AM"`` / ``"07:45"`` / ``"14:05:00"`` → minutes past midnight.

    School systems send all three. A bare hour without a meridiem is read as
    24-hour, because bell schedules are never written as "1-2" meaning 1 AM.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minutes = int(value)
        return minutes if 0 <= minutes <= 24 * 60 else None
    text = str(value).strip()
    if not text:
        return None
    # ISO datetimes ("2026-09-30T07:45:00") — keep only the clock part.
    if "T" in text and len(text) >= 16:
        text = text.split("T", 1)[1][:8]
    m = _CLOCK_RE.match(text)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    meridiem = (m.group(3) or "").lower()
    if minute > 59 or hour > 24:
        return None
    if meridiem == "p" and hour < 12:
        hour += 12
    elif meridiem == "a" and hour == 12:
        hour = 0
    total = hour * 60 + minute
    return total if total <= 24 * 60 else None


def fmt_clock(minutes: int) -> str:
    h, m = divmod(int(minutes) % (24 * 60), 60)
    return f"{h:02d}:{m:02d}"


_WEEKDAY_TOKENS = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    "mo": 0, "tu": 1, "we": 2, "th": 3, "fr": 4, "sa": 5, "su": 6,
    # Single letters as printed on US timetables ("MWF", "TR").
    "m": 0, "t": 1, "w": 2, "r": 3, "f": 4, "s": 5, "u": 6,
}


def parse_weekdays(value: Any) -> tuple[int, ...]:
    """``"Mon,Wed"`` / ``["M", "W"]`` / ``[0, 2]`` → ``(0, 2)`` (Monday = 0).

    Empty means "every school day". Unknown tokens are dropped rather than
    raising: a timetable with one garbled day is still a timetable.
    """
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        tokens: Iterable[Any] = re.split(r"[\s,;/]+", value.strip())
    elif isinstance(value, (list, tuple, set)):
        tokens = value
    else:
        return ()
    out: list[int] = []
    for token in tokens:
        idx: int | None = None
        if isinstance(token, bool):
            continue
        if isinstance(token, int):
            idx = token if 0 <= token <= 6 else None
        else:
            text = str(token).strip().lower()
            if not text:
                continue
            if text.isdigit():
                n = int(text)
                idx = n if 0 <= n <= 6 else None
            elif len(text) >= 3 and text[:3] in _WEEKDAY_TOKENS:
                idx = _WEEKDAY_TOKENS[text[:3]]
            elif len(text) <= 2 and text in _WEEKDAY_TOKENS:
                idx = _WEEKDAY_TOKENS[text]
            elif set(text) <= set("mtwrfsu"):
                # Run-together letters: "MWF", "TR".
                for ch in text:
                    n = _WEEKDAY_TOKENS[ch]
                    if n not in out:
                        out.append(n)
        if idx is not None and idx not in out:
            out.append(idx)
    return tuple(sorted(out))


def parse_int_list(value: Any, *, lo: int = 1, hi: int = MAX_CYCLE_LENGTH) -> tuple[int, ...]:
    """``"1,3"`` / ``[1, 3]`` / ``["A", "B"]`` → ``(1, 3)``. A=1, B=2."""
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        tokens: Iterable[Any] = re.split(r"[\s,;/]+", value.strip())
    elif isinstance(value, (list, tuple, set)):
        tokens = value
    else:
        tokens = [value]
    out: list[int] = []
    for token in tokens:
        text = str(token).strip().upper()
        if not text:
            continue
        text = re.sub(r"^(DAY|WEEK|WK)\s*", "", text)
        if text in ("A", "B", "C", "D", "E", "F") and len(text) == 1:
            n = ord(text) - ord("A") + 1
        else:
            try:
                n = int(text)
            except ValueError:
                continue
        if lo <= n <= hi and n not in out:
            out.append(n)
    return tuple(sorted(out))


# ── Types ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SkipDay:
    """A no-school day or range (holiday, break, teacher in-service)."""

    start: date
    end: date
    label: str = ""

    def covers(self, day: date) -> bool:
        return self.start <= day <= self.end


@dataclass(frozen=True)
class Rotation:
    """How a school's days rotate. The default is a plain Mon–Fri school."""

    kind: str = "none"
    length: int = 1
    #: A school day that is known to be rotation day ``anchor_day``.
    anchor_date: date | None = None
    anchor_day: int = 1
    school_weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)
    skip_days: tuple[SkipDay, ...] = ()

    def __post_init__(self) -> None:
        kind = self.kind if self.kind in ROTATION_KINDS else "none"
        object.__setattr__(self, "kind", kind)
        if kind == "ab":
            length = 2
        elif kind == "none":
            length = 1
        else:
            try:
                length = int(self.length)
            except (TypeError, ValueError):
                length = 2
            length = max(2, min(MAX_CYCLE_LENGTH, length))
        object.__setattr__(self, "length", length)
        try:
            anchor_day = int(self.anchor_day)
        except (TypeError, ValueError):
            anchor_day = 1
        object.__setattr__(self, "anchor_day", min(max(1, anchor_day), length))
        weekdays = tuple(sorted({int(d) for d in self.school_weekdays if 0 <= int(d) <= 6}))
        object.__setattr__(self, "school_weekdays", weekdays or (0, 1, 2, 3, 4))

    @property
    def rotates(self) -> bool:
        return self.kind != "none"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "Rotation":
        data = data or {}
        skips: list[SkipDay] = []
        for raw in data.get("skip_days") or []:
            skip = parse_skip_day(raw)
            if skip is not None:
                skips.append(skip)
        return cls(
            kind=str(data.get("kind") or data.get("rotation_kind") or "none"),
            length=data.get("length") or data.get("cycle_length") or 1,
            anchor_date=_as_date(data.get("anchor_date")),
            anchor_day=data.get("anchor_day") or 1,
            school_weekdays=parse_weekdays(data.get("school_weekdays")) or (0, 1, 2, 3, 4),
            skip_days=tuple(sorted(skips, key=lambda s: s.start)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "length": self.length,
            "anchor_date": self.anchor_date.isoformat() if self.anchor_date else None,
            "anchor_day": self.anchor_day,
            "school_weekdays": [WEEKDAY_ABBR[d] for d in self.school_weekdays],
            "skip_days": [
                {"start": s.start.isoformat(), "end": s.end.isoformat(), "label": s.label}
                for s in self.skip_days
            ],
        }


@dataclass(frozen=True)
class Meeting:
    """One class on the timetable, with the days it meets."""

    course: str
    start_minute: int | None = None
    end_minute: int | None = None
    period: str = ""
    room: str = ""
    teacher: str = ""
    #: Weekdays it meets on (Monday = 0). Empty = every school day.
    weekdays: tuple[int, ...] = ()
    #: Rotation days it meets on (A = 1, B = 2; Day N; Week N). Empty = all.
    rotation_days: tuple[int, ...] = ()
    id: Any = None
    color: str = ""
    source: str = "manual"

    @property
    def timed(self) -> bool:
        return (
            self.start_minute is not None
            and self.end_minute is not None
            and self.end_minute > self.start_minute
        )

    def with_bell(self, bell: Mapping[str, Sequence[Any]] | None) -> "Meeting":
        """Fill missing times from the bell schedule for this period."""
        if self.timed or not bell or not self.period:
            return self
        slot = bell.get(str(self.period)) or bell.get(str(self.period).strip().lstrip("0"))
        if not slot or len(slot) < 2:
            return self
        start, end = parse_clock(slot[0]), parse_clock(slot[1])
        if start is None or end is None or end <= start:
            return self
        return Meeting(**{**_meeting_fields(self), "start_minute": start, "end_minute": end})

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "course": self.course,
            "period": self.period,
            "room": self.room,
            "teacher": self.teacher,
            "start": fmt_clock(self.start_minute) if self.start_minute is not None else "",
            "end": fmt_clock(self.end_minute) if self.end_minute is not None else "",
            "weekdays": [WEEKDAY_ABBR[d] for d in self.weekdays],
            "rotation_days": list(self.rotation_days),
            "color": self.color,
            "source": self.source,
        }


def _meeting_fields(m: Meeting) -> dict[str, Any]:
    return {
        "course": m.course, "start_minute": m.start_minute, "end_minute": m.end_minute,
        "period": m.period, "room": m.room, "teacher": m.teacher, "weekdays": m.weekdays,
        "rotation_days": m.rotation_days, "id": m.id, "color": m.color, "source": m.source,
    }


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def parse_skip_day(raw: Any) -> SkipDay | None:
    if isinstance(raw, SkipDay):
        return raw
    if isinstance(raw, Mapping):
        start = _as_date(raw.get("start") or raw.get("date"))
        end = _as_date(raw.get("end")) or start
        label = str(raw.get("label") or "")[:80]
    else:
        start = end = _as_date(raw)
        label = ""
    if start is None or end is None:
        return None
    if end < start:
        start, end = end, start
    # A "skip day" spanning more than a season is a typo, not a break.
    if (end - start).days > 120:
        return None
    return SkipDay(start=start, end=end, label=label)


# ── Rotation math ────────────────────────────────────────────────────


def skip_reason(day: date, rotation: Rotation) -> str | None:
    """Why ``day`` has no school, or ``None`` when it is a school day."""
    if day.weekday() not in rotation.school_weekdays:
        return "weekend"
    for skip in rotation.skip_days:
        if skip.covers(day):
            return skip.label or "no school"
    return None


def is_school_day(day: date, rotation: Rotation) -> bool:
    return skip_reason(day, rotation) is None


def _school_days_between(a: date, b: date, rotation: Rotation) -> int:
    """School days in ``(a, b]`` when ``b > a``, negated for ``b < a``."""
    if a == b:
        return 0
    sign = 1 if b > a else -1
    lo, hi = (a, b) if b > a else (b, a)
    count = 0
    cursor = lo + timedelta(days=1)
    while cursor <= hi:
        if is_school_day(cursor, rotation):
            count += 1
        cursor += timedelta(days=1)
    return sign * count


def rotation_day(day: date, rotation: Rotation) -> int | None:
    """The 1-based rotation day ``day`` falls on, or ``None``.

    ``None`` means one of: no school that day, a timetable that does not
    rotate, or a rotation with no anchor yet (we do not guess which day
    today is — a wrong guess sends a student to the wrong class).
    """
    if not rotation.rotates or rotation.anchor_date is None:
        return None
    if not is_school_day(day, rotation):
        return None
    if abs((day - rotation.anchor_date).days) > MAX_ROTATION_SPAN_DAYS:
        return None
    zero_based_anchor = rotation.anchor_day - 1
    if rotation.kind == "week":
        anchor_monday = rotation.anchor_date - timedelta(days=rotation.anchor_date.weekday())
        monday = day - timedelta(days=day.weekday())
        weeks = (monday - anchor_monday).days // 7
        return (zero_based_anchor + weeks) % rotation.length + 1
    # A/B and N-day cycles step once per school day. An anchor that was set
    # on a day that turned out to be a holiday still counts from that date.
    steps = _school_days_between(rotation.anchor_date, day, rotation)
    return (zero_based_anchor + steps) % rotation.length + 1


def rotation_label(day: date, rotation: Rotation) -> str:
    """``"A Day"``, ``"Day 3"``, ``"Week 2"``, or ``""``."""
    n = rotation_day(day, rotation)
    if n is None:
        return ""
    if rotation.kind == "ab":
        return f"{'AB'[n - 1]} Day"
    if rotation.kind == "week":
        return f"Week {n}"
    return f"Day {n}"


def label_for_index(rotation: Rotation, n: int) -> str:
    if rotation.kind == "ab":
        return f"{'AB'[(n - 1) % 2]} Day"
    if rotation.kind == "week":
        return f"Week {n}"
    if rotation.kind == "cycle":
        return f"Day {n}"
    return ""


def anchor_for_today(day: date, today_is: int, rotation: Rotation) -> Rotation:
    """Re-anchor so ``day`` is rotation day ``today_is``.

    Rotations drift — a snow day, an assembly schedule — and the student
    is the one who knows. "Today is a B day" is the whole correction.
    """
    return Rotation(
        kind=rotation.kind, length=rotation.length, anchor_date=day,
        anchor_day=today_is, school_weekdays=rotation.school_weekdays,
        skip_days=rotation.skip_days,
    )


def meets_on(meeting: Meeting, day: date, rotation: Rotation) -> bool:
    if not is_school_day(day, rotation):
        return False
    if meeting.weekdays and day.weekday() not in meeting.weekdays:
        return False
    if rotation.rotates and meeting.rotation_days:
        n = rotation_day(day, rotation)
        # Rotation unknown (no anchor): a class restricted to certain days
        # cannot be placed, so leave it off rather than double-booking.
        if n is None or n not in meeting.rotation_days:
            return False
    return True


def meetings_on(
    day: date,
    meetings: Iterable[Meeting],
    rotation: Rotation,
    bell: Mapping[str, Sequence[Any]] | None = None,
) -> list[Meeting]:
    """Timed classes that meet on ``day``, in clock order."""
    out = []
    for m in meetings:
        m = m.with_bell(bell)
        if m.timed and meets_on(m, day, rotation):
            out.append(m)
    out.sort(key=lambda m: (m.start_minute, m.end_minute, m.course))
    return out


def busy_by_date(
    start: date,
    days: int,
    meetings: Sequence[Meeting],
    rotation: Rotation,
    bell: Mapping[str, Sequence[Any]] | None = None,
    *,
    passing_minutes: int = 0,
) -> dict[date, list[tuple[int, int]]]:
    """``{date: [(start_minute, end_minute)]}`` of class time.

    The same shape the scheduler already subtracts calendar events in, so
    class time joins the existing "not the student's to spend" path
    rather than inventing a second one.
    """
    out: dict[date, list[tuple[int, int]]] = {}
    span = max(0, min(int(days or 0), 120))
    for offset in range(span):
        day = start + timedelta(days=offset)
        for m in meetings_on(day, meetings, rotation, bell):
            out.setdefault(day, []).append(
                (max(0, m.start_minute - passing_minutes),
                 min(24 * 60, m.end_minute + passing_minutes))
            )
    return out


def next_meeting(
    now: datetime,
    meetings: Sequence[Meeting],
    rotation: Rotation,
    bell: Mapping[str, Sequence[Any]] | None = None,
    horizon_days: int = 7,
) -> tuple[date, Meeting] | None:
    """The next class that has not started yet, within a week."""
    minute = now.hour * 60 + now.minute
    for offset in range(horizon_days + 1):
        day = now.date() + timedelta(days=offset)
        for m in meetings_on(day, meetings, rotation, bell):
            if offset > 0 or m.start_minute >= minute:
                return day, m
    return None
