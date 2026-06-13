"""Workload forecaster — 7-day projection of estimated work load.

``forecast(assignments, availability, today) → WorkloadForecast``

For each of the next 7 days we compute:

* ``committed_minutes`` — estimated minutes for tasks *due* that day and
  not yet complete.
* ``prework_minutes`` — minutes of upcoming-but-not-due-yet tasks the
  student should land on that day, distributed backwards from each task's
  due date using a greedy fill against availability.
* ``available_minutes`` — from the student's weekly availability profile.
* ``stress`` — ``max(0, (committed + prework - available) / max(available, 1))``,
  clamped to ``[0, 1]``.

Deterministic and pure. Never reads the clock; ``today`` is supplied.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Iterable

from intelliplan.domain.assignment import Assignment, AssignmentStatus
from intelliplan.domain.plan import DayLoad, WorkloadForecast


FORECAST_DAYS = 7
DEFAULT_DAILY_AVAILABLE_MIN = 120  # 2h fallback when the student hasn't set one
_WEEKDAY_KEYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _normalise_availability(availability: dict[str, int] | None) -> dict[str, int]:
    """Lower-case weekday keys → minutes.

    Accepts ``None`` for "we don't know, assume the default."
    """
    if not availability:
        return {day: DEFAULT_DAILY_AVAILABLE_MIN for day in _WEEKDAY_KEYS}
    normalised = {day: DEFAULT_DAILY_AVAILABLE_MIN for day in _WEEKDAY_KEYS}
    for key, value in availability.items():
        k = (key or "").strip().lower()
        if k in normalised:
            try:
                normalised[k] = max(0, int(value))
            except (TypeError, ValueError):
                continue
    return normalised


def _weekday_key(d: date) -> str:
    return _WEEKDAY_KEYS[d.weekday()]


def _is_active(assignment: Assignment) -> bool:
    return assignment.status not in (
        AssignmentStatus.COMPLETED,
        AssignmentStatus.DISMISSED,
    )


def _distribute_prework(
    assignments: Iterable[Assignment],
    today: date,
    horizon: list[date],
    available_remaining: dict[date, int],
) -> dict[date, int]:
    """Greedy backwards-fill of pre-work.

    For each upcoming assignment within the horizon, allocate its
    ``est_minutes`` into the days *before* its due date, starting from
    ``due_date - 1`` and walking backwards. Caps at the day's remaining
    availability — overflow stays unallocated (which is what causes
    ``stress`` to rise on dense days).

    Tasks due *today* or due *after* the horizon contribute nothing to
    pre-work — those that fall in the horizon are caught by the
    committed bucket, and out-of-horizon tasks are too far to plan
    around yet.
    """
    prework: dict[date, int] = defaultdict(int)
    # Order shortest-due-first so urgent prep gets first dibs on free time.
    ordered = sorted(
        (a for a in assignments if _is_active(a) and a.due_date is not None),
        key=lambda a: a.due_date or today,
    )
    horizon_set = set(horizon)
    for a in ordered:
        due = a.due_date
        if due is None or due <= today or due not in horizon_set:
            continue
        remaining = max(0, int(a.est_minutes or 0))
        if remaining == 0:
            continue
        # Walk backwards from due-1 down to today, capped by available.
        cursor = due - timedelta(days=1)
        while remaining > 0 and cursor >= today:
            if cursor in available_remaining:
                slot = available_remaining[cursor]
                if slot > 0:
                    take = min(slot, remaining)
                    prework[cursor] += take
                    available_remaining[cursor] = slot - take
                    remaining -= take
            cursor -= timedelta(days=1)
    return prework


def _committed_per_day(
    assignments: Iterable[Assignment], horizon_set: set[date]
) -> dict[date, int]:
    out: dict[date, int] = defaultdict(int)
    for a in assignments:
        if not _is_active(a) or a.due_date is None:
            continue
        if a.due_date in horizon_set:
            out[a.due_date] += max(0, int(a.est_minutes or 0))
    return out


def _summary(days: tuple[DayLoad, ...], heaviest: date | None) -> str:
    if not days:
        return "No data available yet."
    if heaviest is None:
        return "Your week looks clear."
    heaviest_load = next((d for d in days if d.day == heaviest), days[0])
    today_load = days[0]
    name = heaviest.strftime("%A")
    if heaviest == today_load.day:
        return f"Today is your heaviest day — {heaviest_load.committed_minutes + heaviest_load.prework_minutes} min of work."
    if today_load.stress < 0.25 and heaviest_load.stress > 0.5:
        return f"{name} is your heaviest day. Today is light — use it to get ahead."
    if heaviest_load.stress > 0.75:
        return f"{name} is going to be tight. Worth shifting work earlier."
    return f"{name} is the heaviest day this week."


def forecast(
    assignments: Iterable[Assignment],
    availability: dict[str, int] | None,
    today: date,
) -> WorkloadForecast:
    """Compute the 7-day workload forecast.

    ``availability`` maps weekday name (lower-case) → minutes/day. Pass
    ``None`` to use the default profile (120 min/day).
    """
    assignments_list = list(assignments)
    horizon = [today + timedelta(days=i) for i in range(FORECAST_DAYS)]
    horizon_set = set(horizon)
    profile = _normalise_availability(availability)

    available_per_day: dict[date, int] = {d: profile[_weekday_key(d)] for d in horizon}
    committed = _committed_per_day(assignments_list, horizon_set)

    # The greedy fill consumes ``available_remaining`` in place; start
    # from the available pool with committed already subtracted so we
    # don't double-book the same minute.
    available_remaining: dict[date, int] = {
        d: max(0, available_per_day[d] - committed.get(d, 0)) for d in horizon
    }
    prework = _distribute_prework(assignments_list, today, horizon, available_remaining)

    days = tuple(
        DayLoad(
            day=d,
            committed_minutes=int(committed.get(d, 0)),
            prework_minutes=int(prework.get(d, 0)),
            available_minutes=int(available_per_day[d]),
            stress=_stress(committed.get(d, 0), prework.get(d, 0), available_per_day[d]),
        )
        for d in horizon
    )

    heaviest = max(
        days, key=lambda x: (x.committed_minutes + x.prework_minutes, -x.day.toordinal())
    )
    heaviest_day = heaviest.day if (heaviest.committed_minutes + heaviest.prework_minutes) > 0 else None
    return WorkloadForecast(
        days=days,
        heaviest_day=heaviest_day,
        summary=_summary(days, heaviest_day),
    )


def _stress(committed: int, prework: int, available: int) -> float:
    """0..1 ratio of load vs available time.

    ``0.0`` = nothing scheduled, ``1.0`` = fully booked or overbooked.
    Anything above 1.0 clamps — the bar UI only renders up to "full,"
    and the heaviest-day callout already conveys the overflow.
    """
    load = max(0, committed + prework)
    if available <= 0:
        return 1.0 if load > 0 else 0.0
    return min(1.0, load / available)
