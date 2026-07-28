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
        hour = self.start.hour
        for name, (lo, hi) in SLOT_WINDOWS.items():
            if lo <= hour < hi:
                return name
        return "evening" if hour >= 17 else "morning"


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
) -> list[Window]:
    """Return the free study windows on ``target``, in chronological order.

    Falls back to the ``preferred_time`` slot when the student hasn't recorded
    availability for that weekday, so this is safe for brand-new users and
    guests. Windows on today are trimmed to start no earlier than "now".
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

    busy = _merge(parse_commitments(commitments).get(day_key, []))
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

    @property
    def has_signal(self) -> bool:
        return self.sample_size >= MIN_SAMPLES_FOR_SIGNAL

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
        if self.stamina_minutes:
            bits.append(
                f"Their typical finished block is {self.stamina_minutes} minutes — "
                f"do not schedule single blocks much longer than this."
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
) -> StudyDNA:
    """Distill completion history into a :class:`StudyDNA`.

    ``feedback_rows`` are dict views of ``TaskFeedback`` (keys: ``estimated_time``,
    ``actual_time``, ``course``, ``day_of_week``, ``time_of_day``).
    ``progress_records`` are ``{"total": int, "done": int}`` summaries derived
    from ``SavedSchedule.progress_json``.

    Returns a zeroed DNA when there is nothing to learn from, which every
    consumer treats as "no personalization".
    """
    rows = [r for r in (feedback_rows or []) if isinstance(r, Mapping)]
    timed = [
        r
        for r in rows
        if r.get("actual_time") and r.get("estimated_time")
        and int(r["actual_time"]) > 0 and int(r["estimated_time"]) > 0
    ]

    estimation_ratio = None
    if len(timed) >= MIN_SAMPLES_FOR_SIGNAL:
        ratios = [
            r
            for r in (
                _clamp_ratio(int(t["actual_time"]) / int(t["estimated_time"])) for t in timed
            )
            if r is not None
        ]
        if len(ratios) >= MIN_SAMPLES_FOR_SIGNAL:
            estimation_ratio = round(median(ratios), 3)

    course_ratios: dict[str, float] = {}
    by_course: dict[str, list[float]] = {}
    for t in timed:
        course = str(t.get("course") or "").strip().lower()
        if not course:
            continue
        ratio = _clamp_ratio(int(t["actual_time"]) / int(t["estimated_time"]))
        if ratio is not None:
            by_course.setdefault(course, []).append(ratio)
    for course, ratios in by_course.items():
        if len(ratios) >= 2:
            course_ratios[course] = round(median(ratios), 3)

    slot_counts: dict[str, int] = {}
    for r in rows:
        slot = str(r.get("time_of_day") or "").strip().lower()
        if slot in SLOT_WINDOWS:
            slot_counts[slot] = slot_counts.get(slot, 0) + 1
    best_slot = None
    if sum(slot_counts.values()) >= MIN_SAMPLES_FOR_SIGNAL and slot_counts:
        best_slot = max(slot_counts.items(), key=lambda kv: kv[1])[0]

    dow_counts: dict[str, int] = {}
    for r in rows:
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
    actuals = [int(t["actual_time"]) for t in timed]
    if len(actuals) >= MIN_SAMPLES_FOR_SIGNAL:
        lo, hi = STAMINA_BOUNDS
        stamina = int(max(lo, min(hi, median(actuals))))

    adherence = None
    totals = sum(int(p.get("total") or 0) for p in (progress_records or []) if isinstance(p, Mapping))
    dones = sum(int(p.get("done") or 0) for p in (progress_records or []) if isinstance(p, Mapping))
    if totals >= MIN_SAMPLES_FOR_SIGNAL:
        adherence = round(min(1.0, dones / totals), 3)

    return StudyDNA(
        sample_size=len(rows),
        estimation_ratio=estimation_ratio,
        course_ratios=course_ratios,
        best_slot=best_slot,
        slot_counts=slot_counts,
        strong_days=strong_days,
        weak_days=weak_days,
        stamina_minutes=stamina,
        adherence=adherence,
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


def strip_auto_breaks(blocks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Drop breaks this engine inserted, keeping ones the plan itself authored.

    Re-placing a schedule runs the break rule again, so without this a plan
    that is re-placed on every drag accumulates a new "Long break" each time.
    """
    return [dict(b) for b in blocks if not (b.get("is_break") and b.get("auto"))]


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

    # Front-load demanding work into the student's best measured slot.
    if dna.best_slot and len(usable) > 1 and not preserve_order:
        usable = sorted(usable, key=lambda w: (w.slot() != dna.best_slot, w.start))
        work = [b for b in blocks if not b.get("is_break")]
        if work:
            work.sort(key=_difficulty_rank)
            ordered, it = [], iter(work)
            for b in blocks:
                ordered.append(b if b.get("is_break") else next(it))
            blocks = ordered

    placed: list[dict[str, Any]] = []
    overflow: list[dict[str, Any]] = []
    w_idx = 0
    cursor = usable[0].start
    run = 0

    for idx, block in enumerate(blocks):
        more_work_ahead = any(not b.get("is_break") for b in blocks[idx + 1:])
        duration = int(block.get("duration_minutes") or 25)
        # Never schedule a single block longer than the student finishes.
        if not block.get("is_break") and duration > dna.stamina_minutes * 1.5:
            duration = int(dna.stamina_minutes * 1.5)
            block["duration_minutes"] = duration
            block["split_note"] = "Trimmed to match your usual focus length."

        # Advance to a window that can hold this block.
        while w_idx < len(usable):
            window = usable[w_idx]
            if cursor < window.start:
                cursor = window.start
            if (window.end - cursor).total_seconds() / 60 >= duration:
                break
            w_idx += 1
            run = 0
            if w_idx < len(usable):
                cursor = usable[w_idx].start
        if w_idx >= len(usable):
            overflow.append(block)
            continue

        window = usable[w_idx]
        end = cursor + timedelta(minutes=duration)
        block["time_slot"] = f"{_fmt12(cursor)} - {_fmt12(end)}"
        block["start_iso"] = cursor.isoformat()
        block["end_iso"] = end.isoformat()
        block["window_slot"] = window.slot()
        placed.append(block)

        if block.get("is_break"):
            run = 0
        else:
            run += duration
        gap = 10 if duration >= 60 else 5
        cursor = end + timedelta(minutes=gap)

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
            cursor = brk_end + timedelta(minutes=5)
            run = 0

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
