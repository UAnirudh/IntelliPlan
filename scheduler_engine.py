"""Personalized scheduling engine.

This module is the layer that turns a generic AI block list into a plan shaped
by one specific student's real week and real habits.

Two things make a schedule feel personal, and neither of them is prompt wording:

1. **Real windows.** Blocks land inside the hours the student actually told us
   they are free (``UserIdentity.availability``), minus their stated weekly
   commitments — not at a fixed anchor hour derived from a three-value
   ``preferred_time`` enum.
2. **Real habits.** Placement order, block length, and load per day come from
   the student's own completion history (``TaskFeedback``) and how much of
   past schedules they actually checked off (``SavedSchedule.progress_json``).

Design constraint: this module imports nothing from Flask, SQLAlchemy, or
``App``. Callers pass plain dicts in and get plain data out, so the whole
engine is unit-testable without an app context or a database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

# ── Constants ─────────────────────────────────────────────────────

#: Clock ranges the settings UI's availability toggles map to.
SLOT_WINDOWS: dict[str, tuple[int, int]] = {
    "morning": (6, 12),
    "afternoon": (12, 17),
    "evening": (17, 22),
}
SLOT_ORDER: tuple[str, ...] = ("morning", "afternoon", "evening")
DAY_ABBR: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def slot_for_hour(hour: int) -> str:
    """Name the availability slot an hour-of-day falls in.

    Late-night hours (22:00–06:00) fold into "evening": they are outside
    every configured slot, and calling 1 AM "morning" would credit a
    student's 1 AM sitting to a slot they never study in and steer next
    week's hardest work there.
    """
    for name, (lo, hi) in SLOT_WINDOWS.items():
        if lo <= hour < hi:
            return name
    return "evening" if hour >= 17 or hour < 6 else "morning"

#: A free window shorter than this can't hold a useful study block.
MIN_WINDOW_MINUTES = 20
#: Below this many history rows a signal is noise, so we don't act on it.
MIN_SAMPLES_FOR_SIGNAL = 4
#: Fallback single-block length when the student has no history yet.
DEFAULT_STAMINA_MINUTES = 45
STAMINA_BOUNDS = (20, 90)
#: Estimation-bias ratios outside this range are almost certainly bad data.
RATIO_BOUNDS = (0.25, 4.0)
#: Minutes of continuous work before the engine forces a real break.
LONG_BREAK_AFTER_MINUTES = 90
# How far past the per-sitting cap a block has to run before it is worth
# splitting. See split_oversized_blocks() for why this is not 1.0.
SPLIT_THRESHOLD_RATIO = 1.5

_DAY_TOKEN = r"mon|tue|wed|thu|fri|sat|sun"
_TIME = r"\d{1,2}(?::\d{2})?"

_RANGE_RE = re.compile(
    rf"(?P<days>(?:{_DAY_TOKEN})[a-z]*(?:\s*(?:[/,&+]|and)\s*(?:{_DAY_TOKEN})[a-z]*)*)"
    rf"\s*(?:from\s+)?(?P<t1>{_TIME})\s*(?P<ap1>am|pm)?"
    rf"\s*(?:[-–—]|to)\s*(?P<t2>{_TIME})\s*(?P<ap2>am|pm)?",
    re.IGNORECASE,
)
_SINGLE_RE = re.compile(
    rf"(?P<days>(?:{_DAY_TOKEN})[a-z]*(?:\s*(?:[/,&+]|and)\s*(?:{_DAY_TOKEN})[a-z]*)*)"
    rf"\s*(?:at\s+)?(?P<t1>{_TIME})\s*(?P<ap1>am|pm)?",
    re.IGNORECASE,
)


# ── Free/busy windows ─────────────────────────────────────────────


@dataclass(frozen=True)
class Window:
    """A contiguous stretch of clock time the student is free to study."""

    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return max(0, int((self.end - self.start).total_seconds() // 60))

    def slot(self) -> str:
        """Which availability slot this window mostly sits in."""
        return slot_for_hour(self.start.hour)


def _parse_clock(text: str, meridiem: str | None) -> tuple[int, int] | None:
    """Parse ``"4"`` / ``"4:30"`` plus an optional am/pm into (hour, minute)."""
    try:
        if ":" in text:
            h_s, m_s = text.split(":", 1)
            hour, minute = int(h_s), int(m_s)
        else:
            hour, minute = int(text), 0
    except ValueError:
        return None
    if not (0 <= hour <= 24 and 0 <= minute < 60):
        return None
    ap = (meridiem or "").lower()
    if ap == "pm" and hour < 12:
        hour += 12
    elif ap == "am" and hour == 12:
        hour = 0
    elif not ap and hour < 8:
        # No meridiem given. For a student's extracurriculars, "4-6" is
        # overwhelmingly the afternoon, not 4 AM.
        hour += 12
    return (hour % 24, minute)


def _days_in(token: str) -> list[str]:
    """Expand ``"Mon/Wed"`` or ``"Monday and Friday"`` into abbreviations."""
    out: list[str] = []
    for m in re.finditer(_DAY_TOKEN, token, re.IGNORECASE):
        abbr = m.group(0).title()
        if abbr not in out:
            out.append(abbr)
    return out


def parse_commitments(text: str | None) -> dict[str, list[tuple[int, int]]]:
    """Extract busy intervals from free-text weekly commitments.

    Handles the shapes the settings placeholder actually suggests, e.g.
    ``"soccer Mon/Wed 4–6 pm, piano Fri 5 pm"``. A single time with no range
    is treated as a one-hour commitment.

    Returns ``{day_abbr: [(start_minute_of_day, end_minute_of_day), ...]}``.
    Unparseable text yields an empty dict — this is best-effort enrichment,
    never a hard requirement.
    """
    busy: dict[str, list[tuple[int, int]]] = {}
    if not text or not text.strip():
        return busy

    def _add(days: Sequence[str], start: tuple[int, int], end: tuple[int, int]) -> None:
        s = start[0] * 60 + start[1]
        e = end[0] * 60 + end[1]
        if e <= s:
            e = min(24 * 60, s + 60)
        for d in days:
            busy.setdefault(d, []).append((s, e))

    consumed: list[tuple[int, int]] = []
    for m in _RANGE_RE.finditer(text):
        days = _days_in(m.group("days"))
        if not days:
            continue
        ap1, ap2 = m.group("ap1"), m.group("ap2")
        # "4–6 pm": the trailing meridiem governs both ends, unless applying
        # it would invert the range (e.g. "11–1 pm" is 11 AM to 1 PM).
        start = _parse_clock(m.group("t1"), ap1 or ap2)
        end = _parse_clock(m.group("t2"), ap2 or ap1)
        if not start or not end:
            continue
        if not ap1 and ap2 and start >= end:
            flipped = _parse_clock(m.group("t1"), "am" if ap2.lower() == "pm" else "pm")
            if flipped and flipped < end:
                start = flipped
        _add(days, start, end)
        consumed.append(m.span())

    def _overlaps_consumed(span: tuple[int, int]) -> bool:
        return any(span[0] < c[1] and c[0] < span[1] for c in consumed)

    for m in _SINGLE_RE.finditer(text):
        if _overlaps_consumed(m.span()):
            continue
        days = _days_in(m.group("days"))
        start = _parse_clock(m.group("t1"), m.group("ap1"))
        if not days or not start:
            continue
        _add(days, start, (start[0] + 1, start[1]))
    return busy


def _merge(intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping (start, end) minute intervals."""
    ordered = sorted(intervals)
    merged: list[tuple[int, int]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _subtract(base: tuple[int, int], busy: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Remove busy intervals from a single base interval."""
    pieces = [base]
    for b_start, b_end in busy:
        nxt: list[tuple[int, int]] = []
        for p_start, p_end in pieces:
            if b_end <= p_start or b_start >= p_end:
                nxt.append((p_start, p_end))
                continue
            if b_start > p_start:
                nxt.append((p_start, b_start))
            if b_end < p_end:
                nxt.append((b_end, p_end))
        pieces = nxt
    return pieces


def windows_for_date(
    target: _date,
    availability: Mapping[str, Any] | None = None,
    preferred_time: str = "evening",
    commitments: str | None = None,
    now: datetime | None = None,
    busy_by_date: Mapping[_date, Sequence[tuple[int, int]]] | None = None,
) -> list[Window]:
    """Return the free study windows on ``target``, in chronological order.

    Falls back to the ``preferred_time`` slot when the student hasn't recorded
    availability for that weekday, so this is safe for brand-new users and
    guests. Windows on today are trimmed to start no earlier than "now".

    ``busy_by_date`` is dated committed time — ``{date: [(start_minute,
    end_minute)]}`` — from the student's real calendar. Weekly commitments
    typed into settings recur; a dentist appointment does not, and until this
    existed the scheduler would happily book study time on top of one. It is
    subtracted alongside the recurring commitments, so both kinds of "I am not
    available" are treated as what they are: time that exists on the clock and
    is not the student's to spend.
    """
    day_key = DAY_ABBR[target.weekday()]
    slots: list[str] = []
    raw = (availability or {}).get(day_key)
    if raw is None:
        # Settings stores short keys, but the API accepts full day names.
        for k, v in (availability or {}).items():
            if str(k)[:3].title() == day_key:
                raw = v
                break
    if isinstance(raw, str):
        slots = [s.strip() for s in raw.split(",") if s.strip()]
    elif isinstance(raw, (list, tuple)):
        slots = [str(s).strip() for s in raw if str(s).strip()]
    slots = [s for s in slots if s in SLOT_WINDOWS]
    if not slots:
        fallback = (preferred_time or "evening").lower()
        slots = [fallback if fallback in SLOT_WINDOWS else "evening"]
    slots.sort(key=lambda s: SLOT_ORDER.index(s))

    dated: list[tuple[int, int]] = []
    for start_m, end_m in (busy_by_date or {}).get(target, ()) or ():
        try:
            start_i, end_i = int(start_m), int(end_m)
        except (TypeError, ValueError):
            continue
        if end_i > start_i:
            dated.append((max(0, start_i), min(24 * 60, end_i)))
    busy = _merge(list(parse_commitments(commitments).get(day_key, [])) + dated)
    bases = _merge((SLOT_WINDOWS[s][0] * 60, SLOT_WINDOWS[s][1] * 60) for s in slots)

    now = now or datetime.now()
    floor_minute = 0
    if target == now.date():
        soonest = now + timedelta(minutes=15)
        floor_minute = soonest.hour * 60 + (soonest.minute // 5) * 5

    windows: list[Window] = []
    for base in bases:
        for start_m, end_m in _subtract(base, busy):
            start_m = max(start_m, floor_minute)
            if end_m - start_m < MIN_WINDOW_MINUTES:
                continue
            windows.append(
                Window(
                    start=datetime.combine(target, datetime.min.time())
                    + timedelta(minutes=start_m),
                    end=datetime.combine(target, datetime.min.time())
                    + timedelta(minutes=end_m),
                )
            )
    return windows


# ── Study DNA ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class StudyDNA:
    """A student's measured working habits, distilled from their own history.

    Every field is either ``None`` (not enough evidence yet) or backed by at
    least ``MIN_SAMPLES_FOR_SIGNAL`` observations. Callers must treat ``None``
    as "behave exactly as we did before personalization existed".
    """

    sample_size: int = 0
    #: median(actual / estimated) — >1 means the student underestimates.
    estimation_ratio: float | None = None
    course_ratios: dict[str, float] = field(default_factory=dict)
    #: Slot with the most completed work, e.g. "evening".
    best_slot: str | None = None
    slot_counts: dict[str, int] = field(default_factory=dict)
    #: Weekday abbreviations the student reliably finishes work on.
    strong_days: list[str] = field(default_factory=list)
    weak_days: list[str] = field(default_factory=list)
    #: Typical length of a block they actually finish, in minutes.
    stamina_minutes: int = DEFAULT_STAMINA_MINUTES
    #: Fraction of past scheduled blocks checked off, 0..1.
    adherence: float | None = None

    # ── Measured from real sittings (/active) ────────────────────────
    #
    # Everything above is inferred from what the student *told* us after
    # the fact. These come from the timer: how long they actually held
    # focus, how often they broke it, how often a sitting ended without
    # the work being done. This is the difference between a planner that
    # models a student and one that models a generic student.
    #
    #: Median longest uninterrupted focused stretch, in minutes. Measured
    #: by the focus check-in rather than self-reported, so it is the
    #: truest statement available of how long this person can actually
    #: work — and a far better block size than "how long did that take".
    focus_streak_minutes: int | None = None
    #: Distractions per hour of active work. Drives break cadence: a
    #: student who drifts every 12 minutes is not served by a 90-minute
    #: run at anything.
    distractions_per_hour: float | None = None
    #: Share of sittings that ended with the work actually finished.
    #: Distinct from adherence, which counts blocks ticked off a plan —
    #: this counts sittings that achieved something.
    session_completion: float | None = None
    #: Number of real sittings behind the fields above.
    session_samples: int = 0

    @property
    def has_signal(self) -> bool:
        return self.sample_size >= MIN_SAMPLES_FOR_SIGNAL

    @property
    def has_measured_focus(self) -> bool:
        """True when the timer, not a self-report, is driving block size."""
        return (
            self.session_samples >= MIN_SAMPLES_FOR_SIGNAL
            and self.focus_streak_minutes is not None
        )

    def block_minutes(self) -> int:
        """The length of a single sitting this student can actually hold.

        Measured focus wins over remembered duration when we have it:
        "this task took me 90 minutes" includes every time they got up,
        checked their phone and came back, whereas the longest focused
        streak is the part they could actually sustain. Planning to the
        second number produces blocks people finish.
        """
        if self.has_measured_focus:
            lo, hi = STAMINA_BOUNDS
            return int(max(lo, min(hi, self.focus_streak_minutes)))
        return int(self.stamina_minutes or DEFAULT_STAMINA_MINUTES)

    def adjust_estimate(self, minutes: int, course: str = "") -> int:
        """Correct a raw estimate using the student's measured bias."""
        ratio = self.course_ratios.get((course or "").strip().lower())
        if ratio is None:
            ratio = self.estimation_ratio
        if not ratio:
            return int(minutes)
        return max(10, min(240, int(round(minutes * ratio))))

    def to_prompt(self) -> str:
        """Render the measured facts an LLM can actually plan against.

        Deliberately numeric. "Personalize this" produces generic output;
        "this student runs 35% over their own estimates" does not.
        """
        if not self.has_signal:
            return ""
        bits: list[str] = []
        if self.estimation_ratio and abs(self.estimation_ratio - 1.0) >= 0.15:
            pct = int(round(abs(self.estimation_ratio - 1.0) * 100))
            direction = "longer than" if self.estimation_ratio > 1 else "less time than"
            bits.append(
                f"Tasks take this student ~{pct}% {direction} they estimate — "
                f"size blocks accordingly instead of trusting the raw estimate."
            )
        if self.best_slot:
            bits.append(
                f"They complete the most work in the {self.best_slot} "
                f"({self.slot_counts.get(self.best_slot, 0)} of "
                f"{sum(self.slot_counts.values())} finished tasks). "
                f"Put the hardest work there."
            )
        if self.has_measured_focus:
            bits.append(
                f"Measured focus: their longest uninterrupted focused stretch is "
                f"typically {self.focus_streak_minutes} minutes (from {self.session_samples} "
                f"timed sittings, not self-reported). Size single blocks to this — "
                f"work scheduled past it is work they stop partway through."
            )
        elif self.stamina_minutes:
            bits.append(
                f"Their typical finished block is {self.stamina_minutes} minutes — "
                f"do not schedule single blocks much longer than this."
            )
        if self.distractions_per_hour is not None and self.distractions_per_hour >= 1.5:
            bits.append(
                f"They lose focus about {self.distractions_per_hour:.1f} times per hour — "
                f"schedule shorter runs with breaks between them rather than long stretches."
            )
        if self.session_completion is not None and self.session_samples >= MIN_SAMPLES_FOR_SIGNAL:
            pct = int(round(self.session_completion * 100))
            if pct < 50:
                bits.append(
                    f"Only ~{pct}% of their study sittings end with the work finished — "
                    f"break work into smaller pieces so a sitting can actually complete one."
                )
        if self.strong_days:
            bits.append(f"Historically productive days: {', '.join(self.strong_days)}.")
        if self.weak_days:
            bits.append(
                f"Historically low-output days: {', '.join(self.weak_days)} — "
                f"keep these lighter."
            )
        if self.adherence is not None:
            pct = int(round(self.adherence * 100))
            if pct < 55:
                bits.append(
                    f"They complete only ~{pct}% of scheduled blocks — plan fewer, "
                    f"shorter blocks so the plan stays achievable."
                )
            elif pct >= 85:
                bits.append(
                    f"They complete ~{pct}% of scheduled blocks — they can handle a "
                    f"full, ambitious plan."
                )
        if self.course_ratios:
            slow = sorted(self.course_ratios.items(), key=lambda kv: -kv[1])[:2]
            slow = [(c, r) for c, r in slow if r >= 1.2]
            if slow:
                names = ", ".join(f"{c.title()} (~{int(round(r * 100))}% of estimate)" for c, r in slow)
                bits.append(f"Consistently runs over on: {names}.")
        if not bits:
            return ""
        return (
            "\n=== MEASURED STUDY HABITS (from this student's own history — "
            "plan against these, never quote them back) ===\n"
            + "\n".join(f"  - {b}" for b in bits)
            + "\n=== END MEASURED STUDY HABITS ===\n"
        )


def _clamp_ratio(value: float) -> float | None:
    lo, hi = RATIO_BOUNDS
    return value if lo <= value <= hi else None


def build_study_dna(
    feedback_rows: Sequence[Mapping[str, Any]] | None = None,
    progress_records: Sequence[Mapping[str, Any]] | None = None,
    session_rows: Sequence[Mapping[str, Any]] | None = None,
) -> StudyDNA:
    """Distill completion history into a :class:`StudyDNA`.

    ``feedback_rows`` are dict views of ``TaskFeedback`` (keys: ``estimated_time``,
    ``actual_time``, ``course``, ``day_of_week``, ``time_of_day``).
    ``progress_records`` are ``{"total": int, "done": int}`` summaries derived
    from ``SavedSchedule.progress_json``.
    ``session_rows`` are dict views of ``ActiveSession`` — real timed sittings
    (keys: ``planned_minutes``, ``active_minutes``, ``course``, ``day_of_week``,
    ``time_of_day``, ``completed_work``, ``focus_streak_minutes``,
    ``distraction_events``).

    Sessions are the strongest evidence available and were previously not
    consulted at all: the app measured how long students actually worked,
    when they worked, and when they lost focus, and then planned their week
    from what they had typed into an estimate box. Where a session and a
    self-report disagree, the session wins — see :meth:`StudyDNA.block_minutes`.

    Returns a zeroed DNA when there is nothing to learn from, which every
    consumer treats as "no personalization".
    """
    rows = [r for r in (feedback_rows or []) if isinstance(r, Mapping)]
    sessions = [s for s in (session_rows or []) if isinstance(s, Mapping)]

    def _sess_int(s: Mapping[str, Any], key: str) -> int:
        try:
            return int(s.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    # A sitting only tells us about duration if it was actually worked and
    # was planned against something. Free-form sessions still inform slot,
    # weekday and focus, just not estimation bias.
    sess_timed = [
        s for s in sessions
        if _sess_int(s, "planned_minutes") > 0 and _sess_int(s, "active_minutes") > 0
    ]
    timed = [
        r
        for r in rows
        if r.get("actual_time") and r.get("estimated_time")
        and int(r["actual_time"]) > 0 and int(r["estimated_time"]) > 0
    ]

    # Estimation bias, from both sources. A timed sitting against a planned
    # length is the same measurement as estimated-vs-actual, taken by the
    # clock instead of by memory, so the two pool cleanly.
    all_ratios = [
        r for r in (
            _clamp_ratio(int(t["actual_time"]) / int(t["estimated_time"])) for t in timed
        ) if r is not None
    ] + [
        r for r in (
            _clamp_ratio(_sess_int(s, "active_minutes") / _sess_int(s, "planned_minutes"))
            for s in sess_timed
        ) if r is not None
    ]
    estimation_ratio = None
    if len(all_ratios) >= MIN_SAMPLES_FOR_SIGNAL:
        estimation_ratio = round(median(all_ratios), 3)

    course_ratios: dict[str, float] = {}
    by_course: dict[str, list[float]] = {}
    for t in timed:
        course = str(t.get("course") or "").strip().lower()
        if not course:
            continue
        ratio = _clamp_ratio(int(t["actual_time"]) / int(t["estimated_time"]))
        if ratio is not None:
            by_course.setdefault(course, []).append(ratio)
    for s in sess_timed:
        course = str(s.get("course") or "").strip().lower()
        if not course:
            continue
        ratio = _clamp_ratio(_sess_int(s, "active_minutes") / _sess_int(s, "planned_minutes"))
        if ratio is not None:
            by_course.setdefault(course, []).append(ratio)
    for course, ratios in by_course.items():
        if len(ratios) >= 2:
            course_ratios[course] = round(median(ratios), 3)

    # When did they work? Sessions count double-weight here in the sense
    # that they are simply additional observations — but they are the only
    # ones taken at the time rather than recalled afterwards.
    slot_counts: dict[str, int] = {}
    for r in list(rows) + list(sessions):
        slot = str(r.get("time_of_day") or "").strip().lower()
        if slot in SLOT_WINDOWS:
            slot_counts[slot] = slot_counts.get(slot, 0) + 1
    best_slot = None
    if sum(slot_counts.values()) >= MIN_SAMPLES_FOR_SIGNAL and slot_counts:
        best_slot = max(slot_counts.items(), key=lambda kv: kv[1])[0]

    dow_counts: dict[str, int] = {}
    for r in list(rows) + list(sessions):
        day = str(r.get("day_of_week") or "").strip()[:3].title()
        if day in DAY_ABBR:
            dow_counts[day] = dow_counts.get(day, 0) + 1
    strong_days: list[str] = []
    weak_days: list[str] = []
    if sum(dow_counts.values()) >= MIN_SAMPLES_FOR_SIGNAL * 2:
        avg = sum(dow_counts.values()) / 7.0
        strong_days = [d for d in DAY_ABBR if dow_counts.get(d, 0) >= avg * 1.5]
        weak_days = [d for d in DAY_ABBR if dow_counts.get(d, 0) <= avg * 0.4]

    stamina = DEFAULT_STAMINA_MINUTES
    actuals = [int(t["actual_time"]) for t in timed] + \
        [_sess_int(s, "active_minutes") for s in sess_timed]
    if len(actuals) >= MIN_SAMPLES_FOR_SIGNAL:
        lo, hi = STAMINA_BOUNDS
        stamina = int(max(lo, min(hi, median(actuals))))

    # ── Signals only a timed sitting can provide ────────────────────
    focus_streaks = [
        m for m in (_sess_int(s, "focus_streak_minutes") for s in sessions) if m > 0
    ]
    focus_streak_minutes = (
        int(round(median(focus_streaks))) if len(focus_streaks) >= MIN_SAMPLES_FOR_SIGNAL
        else None
    )

    distractions_per_hour = None
    focus_minutes_total = sum(_sess_int(s, "active_minutes") for s in sessions)
    distraction_total = sum(_sess_int(s, "distraction_events") for s in sessions)
    # An hour of observed work is the floor for a rate — below that, one
    # distraction during a ten-minute sitting reads as "six per hour".
    if len(sessions) >= MIN_SAMPLES_FOR_SIGNAL and focus_minutes_total >= 60:
        distractions_per_hour = round(distraction_total / (focus_minutes_total / 60.0), 2)

    session_completion = None
    if len(sessions) >= MIN_SAMPLES_FOR_SIGNAL:
        finished = sum(1 for s in sessions if s.get("completed_work"))
        session_completion = round(finished / len(sessions), 3)

    adherence = None
    totals = sum(int(p.get("total") or 0) for p in (progress_records or []) if isinstance(p, Mapping))
    dones = sum(int(p.get("done") or 0) for p in (progress_records or []) if isinstance(p, Mapping))
    if totals >= MIN_SAMPLES_FOR_SIGNAL:
        adherence = round(min(1.0, dones / totals), 3)

    return StudyDNA(
        # Sessions are evidence too — a student who has never filled in a
        # feedback form but has run twenty timed sittings knows more about
        # themselves than has_signal used to admit.
        sample_size=len(rows) + len(sessions),
        estimation_ratio=estimation_ratio,
        course_ratios=course_ratios,
        best_slot=best_slot,
        slot_counts=slot_counts,
        strong_days=strong_days,
        weak_days=weak_days,
        stamina_minutes=stamina,
        adherence=adherence,
        focus_streak_minutes=focus_streak_minutes,
        distractions_per_hour=distractions_per_hour,
        session_completion=session_completion,
        session_samples=len(sessions),
    )


def summarize_progress(progress_json: Any) -> dict[str, int]:
    """Reduce one ``SavedSchedule.progress_json`` blob to {total, done}.

    The client writes ``{block_id: {done: bool, checked: [...]}}``; older
    payloads used a bare boolean. Both shapes are accepted.
    """
    if isinstance(progress_json, str):
        import json

        try:
            progress_json = json.loads(progress_json)
        except (TypeError, ValueError):
            return {"total": 0, "done": 0}
    if not isinstance(progress_json, Mapping):
        return {"total": 0, "done": 0}
    total = done = 0
    for value in progress_json.values():
        total += 1
        if value is True:
            done += 1
        elif isinstance(value, Mapping) and value.get("done"):
            done += 1
    return {"total": total, "done": done}


# ── Placement ─────────────────────────────────────────────────────


def _fmt12(dt: datetime) -> str:
    """12-hour time with no leading zero, identically on Windows and POSIX."""
    return dt.strftime("%I:%M %p").lstrip("0") or "12:00 AM"


def _difficulty_rank(block: Mapping[str, Any]) -> int:
    return {"Hard": 0, "Medium": 1, "Easy": 2}.get(str(block.get("difficulty") or "Medium"), 1)


def long_break_after_for(dna: "StudyDNA | None") -> int:
    """Minutes of continuous work before this student has earned a real break.

    Two sittings is the natural unit: one sitting is what they finish in a go,
    and the second is the one they push through. A student whose measured
    focus length is 25 minutes should not have to work 90 minutes straight to
    earn a break, which is what a fixed interval asked of them.

    Bounded on both sides — below 45 the plan becomes more break than work,
    and above 120 the break stops functioning as one. The default stamina
    lands exactly on the old fixed value, so a student with no measured
    history sees no change.
    """
    if dna is None:
        return LONG_BREAK_AFTER_MINUTES

    base = dna.block_minutes()
    if not base:
        return LONG_BREAK_AFTER_MINUTES

    run = base * 2
    # A student the timer has watched drift several times an hour does not
    # get two sittings before a break — the second sitting is the one they
    # were already losing. Shorten the run toward a single sitting as the
    # measured distraction rate climbs.
    if dna.distractions_per_hour is not None and dna.distractions_per_hour >= 1.5:
        run = base * (1 if dna.distractions_per_hour >= 3 else 1.5)

    return max(45, min(120, int(round(run))))


def _due_rank(block: Mapping[str, Any]) -> str:
    """Sort key for a block's deadline. ISO dates compare correctly as strings.

    Work with no deadline sorts after everything dated rather than before it.
    Blocks arrive without one whenever the model renamed an assignment and the
    title lookup missed, and a missing deadline is not evidence of urgency — it
    is an absence of evidence, so it must not push dated work down the day.
    """
    return str(block.get("due_date") or "").strip() or "9999-12-31"


def strip_auto_breaks(blocks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Drop breaks this engine inserted, keeping ones the plan itself authored.

    Re-placing a schedule runs the break rule again, so without this a plan
    that is re-placed on every drag accumulates a new "Long break" each time.
    """
    return [dict(b) for b in blocks if not (b.get("is_break") and b.get("auto"))]


def split_oversized_blocks(
    blocks: Iterable[Mapping[str, Any]], cap_minutes: int
) -> list[dict[str, Any]]:
    """Break work longer than ``cap_minutes`` into several equal sittings.

    A three-hour assignment given to a student whose measured focus length is
    thirty minutes is not a three-hour block and it is not a forty-five minute
    block either — it is four sittings. Returning a single trimmed block, which
    is what this engine used to do, deletes the rest of the work from the plan:
    the student sees a schedule that cannot finish the assignment and has no
    way to know minutes went missing.

    Parts are equal rather than greedy. Filling to the cap and keeping the
    remainder turns 100 minutes into 45 + 45 + 10, and nobody opens a textbook
    for ten minutes; three sittings of 34/33/33 is the same total and every one
    of them is worth sitting down for.

    A block only splits once it is worth more than one sitting — 1.5x the cap.
    Splitting at the cap exactly means 46 minutes against a 45-minute cap
    becomes two 23-minute sittings, half the student's actual capacity, to
    avoid one minute of overshoot. A minute over is not two sittings of work.

    Breaks pass through untouched, and a block that already carries
    ``part_total`` is left alone so re-placing a plan does not split the parts
    of an earlier split.
    """
    out: list[dict[str, Any]] = []
    for raw in blocks:
        block = dict(raw)
        duration = int(block.get("duration_minutes") or 25)
        if (
            cap_minutes <= 0
            or block.get("is_break")
            or block.get("part_total")
            or duration < cap_minutes * SPLIT_THRESHOLD_RATIO
        ):
            out.append(block)
            continue

        parts = -(-duration // cap_minutes)  # ceil, without importing math
        base, extra = divmod(duration, parts)
        title = str(block.get("assignment") or "Task").strip()
        for i in range(parts):
            piece = dict(block)
            piece["duration_minutes"] = base + (1 if i < extra else 0)
            piece["part_index"] = i + 1
            piece["part_total"] = parts
            # Keep the original title alongside the numbered one so callers can
            # group the sittings back together without parsing the suffix out.
            piece["parent_title"] = title
            piece["assignment"] = f"{title} (part {i + 1} of {parts})"
            piece["split_note"] = (
                f"Split into {parts} sittings to match your usual focus length."
            )
            out.append(piece)
    return out


def place_day_blocks(
    blocks: list[dict[str, Any]],
    windows: Sequence[Window],
    dna: StudyDNA | None = None,
    long_break_after: int = LONG_BREAK_AFTER_MINUTES,
    preserve_order: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Lay ``blocks`` into real ``windows``, in order, with breaks.

    Returns ``(placed, overflow)``. Anything that genuinely does not fit in the
    student's free time is returned as overflow rather than being stacked past
    the end of their day — a plan that silently runs to 2 AM is the single
    fastest way to teach someone to ignore their planner.

    Hard work is steered toward the window matching the student's measured
    best slot, unless ``preserve_order`` is set — which the reflow path uses,
    because the student just arranged these by hand and re-sorting them would
    undo the drag they performed.
    """
    if not blocks:
        return [], []
    dna = dna or StudyDNA()
    usable = [w for w in windows if w.minutes >= MIN_WINDOW_MINUTES]
    if not usable:
        return [], list(blocks)

    # Break oversized work into sittings first, so ordering and placement both
    # operate on blocks the student can actually finish in one go. Skipped on
    # the reflow path: those blocks are where the student just dragged them,
    # and multiplying them would undo the arrangement.
    if not preserve_order:
        # block_minutes(), not stamina_minutes: where the timer has measured
        # how long this student actually holds focus, that is the sitting
        # length worth splitting to. A remembered "that took 90 minutes"
        # counts the interruptions; the focus streak does not.
        blocks = split_oversized_blocks(blocks, int(dna.block_minutes() * 1.5))

    # Front-load demanding work into the student's best measured slot.
    if dna.best_slot and len(usable) > 1 and not preserve_order:
        usable = sorted(usable, key=lambda w: (w.slot() != dna.best_slot, w.start))
        work = [b for b in blocks if not b.get("is_break")]
        if work:
            # Deadline first, difficulty only to break ties. Sorting on
            # difficulty alone put a Hard assignment due next week ahead of an
            # Easy one due tomorrow, and when the evening ran out it was the
            # one with the deadline that overflowed to the following day.
            work.sort(key=lambda b: (_due_rank(b), _difficulty_rank(b)))
            ordered, it = [], iter(work)
            for b in blocks:
                ordered.append(b if b.get("is_break") else next(it))
            blocks = ordered

    placed: list[dict[str, Any]] = []
    overflow: list[dict[str, Any]] = []
    # One cursor and one work-streak per window, rather than a single index
    # walking forward. The old version could only ever move to a later window:
    # once a block was too big for the morning and spilled into the evening,
    # every block after it was stuck in the evening too — including short ones
    # that fit the gap the big block left behind. A three-hour morning could
    # end up holding one block and wasting an hour.
    cursors = [w.start for w in usable]
    runs = [0] * len(usable)
    # Lowest window a block may still be placed in. Stays 0 while the engine
    # owns the order, so any window with room can take the next block. On the
    # reflow path it advances with each placement, which reproduces the
    # forward-only walk: backfilling is right when the engine chose the order
    # and wrong when the student did — dragging three blocks into place and
    # getting the third back above the second is the plan rearranging itself
    # under their hands.
    floor_w = 0

    for idx, block in enumerate(blocks):
        more_work_ahead = any(not b.get("is_break") for b in blocks[idx + 1:])
        duration = int(block.get("duration_minutes") or 25)

        # First window with room. Blocks are already in priority order, so
        # scanning from the start can only fill gaps — a block considered later
        # can never take space from one considered earlier, which is why this
        # cannot push urgent work into overflow.
        w_idx = next(
            (
                i
                for i in range(floor_w, len(usable))
                if (usable[i].end - max(cursors[i], usable[i].start)).total_seconds() / 60
                >= duration
            ),
            None,
        )
        if w_idx is None:
            overflow.append(block)
            continue

        window = usable[w_idx]
        if preserve_order:
            floor_w = w_idx
        cursor = max(cursors[w_idx], window.start)
        end = cursor + timedelta(minutes=duration)
        block["time_slot"] = f"{_fmt12(cursor)} - {_fmt12(end)}"
        block["start_iso"] = cursor.isoformat()
        block["end_iso"] = end.isoformat()
        block["window_slot"] = window.slot()
        placed.append(block)

        if block.get("is_break"):
            runs[w_idx] = 0
        else:
            runs[w_idx] += duration
        run = runs[w_idx]
        gap = 10 if duration >= 60 else 5
        cursor = end + timedelta(minutes=gap)
        cursors[w_idx] = cursor

        # Force a real break after a long continuous run — but only if the
        # window still has room AND there's work left to come back to. A break
        # at the end of the day is just a block that says "stop working".
        if (
            run >= long_break_after
            and more_work_ahead
            and (window.end - cursor).total_seconds() / 60 >= 15
        ):
            brk_end = cursor + timedelta(minutes=15)
            placed.append(
                {
                    "assignment": "Long break",
                    "course": "",
                    "duration_minutes": 15,
                    "time_slot": f"{_fmt12(cursor)} - {_fmt12(brk_end)}",
                    "start_iso": cursor.isoformat(),
                    "end_iso": brk_end.isoformat(),
                    "notes": "You've worked a solid stretch. Step away, eat, walk.",
                    "is_break": True,
                    "auto": True,   # engine-inserted — see strip_auto_breaks()
                    "window_slot": window.slot(),
                }
            )
            cursors[w_idx] = brk_end + timedelta(minutes=5)
            runs[w_idx] = 0

    # Backfilling means a block can be placed into an earlier window after a
    # later one already has work in it, so the append order is no longer clock
    # order. The scheduler renders this list top to bottom, so sort it — a plan
    # that lists 7 PM above 8 AM is not a plan anyone can follow.
    placed.sort(key=lambda b: b["start_iso"])

    # The break rule asks "is there more work after this block?" in list order,
    # which stopped being the same question as "later in the day" once blocks
    # could backfill. A block placed after the break can now sit hours before
    # it, leaving the evening to close on "step away, eat, walk". Trim any
    # engine-inserted break left at the end; a break the plan authored itself
    # stays, because that one was somebody's decision.
    while placed and placed[-1].get("is_break") and placed[-1].get("auto"):
        placed.pop()

    return placed, overflow


def describe_week(
    availability: Mapping[str, Any] | None,
    commitments: str | None = None,
) -> str:
    """Render the student's real weekly free time for the prompt.

    Gives the model the actual shape of the week so it stops proposing work
    at hours the student already said they're unavailable.
    """
    if not availability:
        return ""
    busy = parse_commitments(commitments)
    lines: list[str] = []
    for day in DAY_ABBR:
        raw = availability.get(day)
        if raw is None:
            for k, v in availability.items():
                if str(k)[:3].title() == day:
                    raw = v
                    break
        slots = (
            [s.strip() for s in raw.split(",")]
            if isinstance(raw, str)
            else [str(s).strip() for s in (raw or [])]
        )
        slots = [s for s in slots if s in SLOT_WINDOWS]
        if not slots:
            lines.append(f"  - {day}: no study time available")
            continue
        parts = [
            f"{s} ({SLOT_WINDOWS[s][0] % 12 or 12}"
            f"{'am' if SLOT_WINDOWS[s][0] < 12 else 'pm'}–"
            f"{SLOT_WINDOWS[s][1] % 12 or 12}"
            f"{'am' if SLOT_WINDOWS[s][1] < 12 else 'pm'})"
            for s in sorted(slots, key=lambda x: SLOT_ORDER.index(x))
        ]
        line = f"  - {day}: {', '.join(parts)}"
        if busy.get(day):
            blocked = ", ".join(
                f"{s // 60 % 12 or 12}:{s % 60:02d}–{e // 60 % 12 or 12}:{e % 60:02d}"
                for s, e in _merge(busy[day])
            )
            line += f" — busy {blocked}"
        lines.append(line)
    if not lines:
        return ""
    return (
        "\n=== THIS STUDENT'S REAL WEEK (do not schedule outside these hours) ===\n"
        + "\n".join(lines)
        + "\n=== END REAL WEEK ===\n"
    )
