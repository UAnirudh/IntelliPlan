"""Feasibility verification for a finished schedule.

``scheduler_engine`` and ``intelligence.planner`` both enforce constraints
*while building* a plan. That is necessary and it is not sufficient, because
plans do not only come from the builder: they are dragged around in the
Interactive View, patched by the recovery path, edited by overrides, restored
from a saved row written by an older version of the code, and — on the AI
fallback path — produced by a model. By the time a plan reaches a student,
nobody has re-asked the only question that matters:

    Is this schedule actually feasible?

This module answers that. It takes a finished ``schedule_data`` blob and the
same real-world inputs the builder had, and returns every way the plan is
impossible, each one named. It never repairs anything — a checker that
silently fixes what it finds is a checker whose findings you never see.

Purity
------
No Flask, no ORM, no clock reads. ``now`` is passed in where it is needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "Violation",
    "FeasibilityReport",
    "check_schedule",
    "Severity",
    "MIN_BLOCK_MINUTES",
]

#: A block shorter than this is not a study session, it is a calendar
#: decoration. Matches ``planner.MIN_SESSION_MINUTES``.
MIN_BLOCK_MINUTES = 15

#: Minutes of continuous work that must be followed by a break. Matches
#: ``scheduler_engine.LONG_BREAK_AFTER_MINUTES``.
LONG_RUN_MINUTES = 90

#: Tolerance when comparing a day's scheduled minutes against its capacity.
#: Placement inserts small gaps between blocks, so an exactly-full day can
#: measure a few minutes over without anything being wrong.
CAPACITY_SLACK_MINUTES = 10


class Severity:
    """How much a violation matters.

    ``HARD`` means the plan cannot be executed as written — overlapping
    blocks, work after a deadline, study booked over a dentist appointment.
    ``SOFT`` means it can be executed but should not be — a four-hour
    unbroken run, a day loaded past its own capacity.
    """

    HARD = "hard"
    SOFT = "soft"


@dataclass(frozen=True, slots=True)
class Violation:
    """One specific way the plan does not work.

    ``key`` is stable and safe to branch on or aggregate in telemetry.
    ``detail`` is human-readable and safe to show a student.
    """

    key: str
    severity: str
    day: date | None
    detail: str
    block_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FeasibilityReport:
    """The verdict, plus everything it was based on."""

    feasible: bool
    violations: tuple[Violation, ...]
    blocks_checked: int
    days_checked: int

    @property
    def hard(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.severity == Severity.HARD)

    @property
    def soft(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.severity == Severity.SOFT)

    def to_dict(self) -> dict[str, Any]:
        return {
            "feasible": self.feasible,
            "blocks_checked": self.blocks_checked,
            "days_checked": self.days_checked,
            "violations": [
                {
                    "key": v.key,
                    "severity": v.severity,
                    "day": v.day.isoformat() if v.day else None,
                    "detail": v.detail,
                    "block_ids": list(v.block_ids),
                }
                for v in self.violations
            ],
        }


# ── parsing helpers ──────────────────────────────────────────────────


def _as_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _as_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _minutes_of_day(dt: datetime) -> int:
    return dt.hour * 60 + dt.minute


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


@dataclass(frozen=True, slots=True)
class _Placed:
    """A block reduced to the four things feasibility cares about."""

    block_id: str
    title: str
    start: datetime
    end: datetime
    is_break: bool
    due_date: date | None

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)

    @property
    def span(self) -> tuple[int, int]:
        return (_minutes_of_day(self.start), _minutes_of_day(self.end))


def _placed_blocks(blocks: Iterable[Mapping[str, Any]]) -> list[_Placed]:
    """Blocks that carry a real clock placement, in clock order.

    Blocks without ``start_iso`` are skipped rather than guessed at. An
    unplaced block is a different problem (it is reported as ``unplaced``
    by the builder) and inventing a start time for it here would manufacture
    overlaps that do not exist.
    """
    out: list[_Placed] = []
    for b in blocks or []:
        if not isinstance(b, Mapping):
            continue
        start = _as_dt(b.get("start_iso"))
        end = _as_dt(b.get("end_iso"))
        if start is None:
            continue
        if end is None:
            try:
                end = start + timedelta(minutes=int(b.get("duration_minutes") or 0))
            except (TypeError, ValueError):
                continue
        out.append(
            _Placed(
                block_id=str(b.get("id") or ""),
                title=str(b.get("assignment") or b.get("parent_title") or "Study"),
                start=start,
                end=end,
                is_break=bool(b.get("is_break")),
                due_date=_as_date(b.get("due_date")),
            )
        )
    out.sort(key=lambda p: p.start)
    return out


# ── the check ────────────────────────────────────────────────────────


def check_schedule(
    schedule_data: Mapping[str, Any] | None,
    *,
    windows_for: Any = None,
    busy_by_date: Mapping[date, Sequence[tuple[int, int]]] | None = None,
    now: datetime | None = None,
) -> FeasibilityReport:
    """Verify a finished plan against the real world.

    ``windows_for(day) -> Sequence[Window]`` is the same callable shape
    ``scheduler_engine.windows_for_date`` produces, injected rather than
    imported so this module stays free of the placement engine. When it is
    ``None`` the availability check is skipped — and skipped visibly, by
    simply producing no violations of that kind, rather than by passing.

    ``now`` enables the "scheduled in the past" check. Omit it to check a
    plan's internal consistency without reference to the current time.
    """
    days = (schedule_data or {}).get("schedule") or []
    violations: list[Violation] = []
    blocks_checked = 0
    days_checked = 0

    for day_entry in days:
        if not isinstance(day_entry, Mapping):
            continue
        day = _as_date(day_entry.get("date"))
        if day is None:
            continue
        days_checked += 1

        placed = _placed_blocks(day_entry.get("blocks") or [])
        blocks_checked += len(placed)

        violations.extend(_check_overlaps(placed, day))
        violations.extend(_check_durations(placed, day))
        violations.extend(_check_deadlines(placed, day))
        violations.extend(_check_day_matches_block_dates(placed, day))
        violations.extend(_check_long_runs(placed, day))
        violations.extend(_check_capacity(day_entry, placed, day))
        violations.extend(_check_busy(placed, day, busy_by_date))
        violations.extend(_check_windows(placed, day, windows_for))
        if now is not None:
            violations.extend(_check_past(placed, day, now))

    hard = any(v.severity == Severity.HARD for v in violations)
    return FeasibilityReport(
        feasible=not hard,
        violations=tuple(violations),
        blocks_checked=blocks_checked,
        days_checked=days_checked,
    )


def _check_overlaps(placed: Sequence[_Placed], day: date) -> list[Violation]:
    out: list[Violation] = []
    for i in range(len(placed) - 1):
        a, b = placed[i], placed[i + 1]
        if a.end > b.start:
            out.append(
                Violation(
                    key="overlap",
                    severity=Severity.HARD,
                    day=day,
                    detail=(
                        f"{a.title} runs until {a.end.strftime('%I:%M %p').lstrip('0')} "
                        f"but {b.title} starts before it ends."
                    ),
                    block_ids=tuple(x for x in (a.block_id, b.block_id) if x),
                )
            )
    return out


def _check_durations(placed: Sequence[_Placed], day: date) -> list[Violation]:
    out: list[Violation] = []
    for p in placed:
        if p.is_break:
            continue
        if p.minutes <= 0:
            out.append(
                Violation(
                    key="zero_duration",
                    severity=Severity.HARD,
                    day=day,
                    detail=f"{p.title} has no duration.",
                    block_ids=(p.block_id,) if p.block_id else (),
                )
            )
        elif p.minutes < MIN_BLOCK_MINUTES:
            out.append(
                Violation(
                    key="too_short",
                    severity=Severity.SOFT,
                    day=day,
                    detail=(
                        f"{p.title} is only {p.minutes} minutes — too short to be "
                        f"worth starting."
                    ),
                    block_ids=(p.block_id,) if p.block_id else (),
                )
            )
    return out


def _check_deadlines(placed: Sequence[_Placed], day: date) -> list[Violation]:
    """Work scheduled after the thing it is for was due.

    This is a hard violation, not a warning. A plan that tells a student to
    study on Friday for a quiz they sat on Thursday is not a plan.
    """
    out: list[Violation] = []
    for p in placed:
        if p.is_break or p.due_date is None:
            continue
        if p.start.date() > p.due_date:
            out.append(
                Violation(
                    key="after_deadline",
                    severity=Severity.HARD,
                    day=day,
                    detail=(
                        f"{p.title} is scheduled for {p.start.date().isoformat()}, "
                        f"after its {p.due_date.isoformat()} deadline."
                    ),
                    block_ids=(p.block_id,) if p.block_id else (),
                )
            )
    return out


def _check_day_matches_block_dates(
    placed: Sequence[_Placed], day: date
) -> list[Violation]:
    """A block filed under one date but timestamped on another.

    Cheap to check and worth checking: it is the signature of a timezone bug
    or a hand-edited plan, and every other check on this day is reasoning
    about the wrong calendar day when it happens.
    """
    out: list[Violation] = []
    for p in placed:
        if p.start.date() != day:
            out.append(
                Violation(
                    key="date_mismatch",
                    severity=Severity.HARD,
                    day=day,
                    detail=(
                        f"{p.title} is listed under {day.isoformat()} but starts "
                        f"on {p.start.date().isoformat()}."
                    ),
                    block_ids=(p.block_id,) if p.block_id else (),
                )
            )
    return out


def _check_long_runs(placed: Sequence[_Placed], day: date) -> list[Violation]:
    """Continuous work past the break threshold, with no break in it."""
    run = 0
    run_ids: list[str] = []
    out: list[Violation] = []
    for p in placed:
        if p.is_break:
            run = 0
            run_ids = []
            continue
        run += p.minutes
        if p.block_id:
            run_ids.append(p.block_id)
        if run > LONG_RUN_MINUTES * 2:
            out.append(
                Violation(
                    key="no_break",
                    severity=Severity.SOFT,
                    day=day,
                    detail=(
                        f"{run} minutes of unbroken work on {day.isoformat()} with "
                        f"no break scheduled."
                    ),
                    block_ids=tuple(run_ids),
                )
            )
            run = 0
            run_ids = []
    return out


def _check_capacity(
    day_entry: Mapping[str, Any], placed: Sequence[_Placed], day: date
) -> list[Violation]:
    try:
        capacity = int(day_entry.get("capacity_minutes") or 0)
    except (TypeError, ValueError):
        return []
    if capacity <= 0:
        return []
    scheduled = sum(p.minutes for p in placed if not p.is_break)
    if scheduled > capacity + CAPACITY_SLACK_MINUTES:
        return [
            Violation(
                key="over_capacity",
                severity=Severity.SOFT,
                day=day,
                detail=(
                    f"{scheduled} minutes of work scheduled into "
                    f"{capacity} minutes of free time."
                ),
            )
        ]
    return []


def _check_busy(
    placed: Sequence[_Placed],
    day: date,
    busy_by_date: Mapping[date, Sequence[tuple[int, int]]] | None,
) -> list[Violation]:
    """Study booked on top of a real calendar commitment."""
    if not busy_by_date:
        return []
    busy = busy_by_date.get(day) or ()
    if not busy:
        return []
    out: list[Violation] = []
    for p in placed:
        if p.is_break:
            continue
        for interval in busy:
            try:
                b0, b1 = int(interval[0]), int(interval[1])
            except (TypeError, ValueError, IndexError):
                continue
            if _overlaps(p.span, (b0, b1)):
                out.append(
                    Violation(
                        key="calendar_conflict",
                        severity=Severity.HARD,
                        day=day,
                        detail=(
                            f"{p.title} overlaps a calendar commitment on "
                            f"{day.isoformat()}."
                        ),
                        block_ids=(p.block_id,) if p.block_id else (),
                    )
                )
                break
    return out


def _check_windows(
    placed: Sequence[_Placed], day: date, windows_for: Any
) -> list[Violation]:
    """Work placed outside the hours the student said they are free."""
    if windows_for is None:
        return []
    try:
        windows = list(windows_for(day) or [])
    except Exception:
        # A failing availability provider must not turn into a false
        # "infeasible" verdict — that would block a plan that is fine.
        return []
    if not windows:
        return []
    spans: list[tuple[int, int]] = []
    for w in windows:
        try:
            spans.append((_minutes_of_day(w.start), _minutes_of_day(w.end)))
        except AttributeError:
            continue
    if not spans:
        return []

    out: list[Violation] = []
    for p in placed:
        if p.is_break:
            continue
        s, e = p.span
        if not any(s >= lo and e <= hi for lo, hi in spans):
            out.append(
                Violation(
                    key="outside_availability",
                    severity=Severity.HARD,
                    day=day,
                    detail=(
                        f"{p.title} is scheduled outside the hours you said you "
                        f"are free on {day.strftime('%A')}."
                    ),
                    block_ids=(p.block_id,) if p.block_id else (),
                )
            )
    return out


def _check_past(
    placed: Sequence[_Placed], day: date, now: datetime
) -> list[Violation]:
    """Blocks whose whole window has already gone by.

    Soft, not hard: a plan generated this morning legitimately contains this
    morning's finished work, and calling that infeasible would make every
    mid-day page load look broken.
    """
    out: list[Violation] = []
    for p in placed:
        if p.is_break:
            continue
        if p.end < now:
            out.append(
                Violation(
                    key="in_the_past",
                    severity=Severity.SOFT,
                    day=day,
                    detail=f"{p.title} was scheduled for a time that has passed.",
                    block_ids=(p.block_id,) if p.block_id else (),
                )
            )
    return out
