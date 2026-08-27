"""Build a study schedule without the AI.

When both AI providers are down, or return something unparseable twice, the
scheduler used to hand the student an error and nothing else. Generating a
plan is the core of the product, so an outage at Google or Groq meant the
product did not work — for a student who sat down at 9pm to plan their week,
"please try again" is the same as "no".

Nothing here is new planning logic. ``scheduler_engine`` already contains a
complete deterministic allocator that the manual and reflow paths use:
capacity from the student's real availability, urgency by slack rather than
raw due date, splitting oversized tasks into sittings, and placing them in
actual free windows with breaks. This assembles those pieces into the same
JSON shape the AI is asked to produce, so everything downstream —
``enrich_schedule_data``, ``humanize_schedule``, the Interactive View — works
on it unchanged.

What it does not do is write. The AI's overview and per-day tips are prose
about the specific week, and inventing flat substitutes would be worse than
saying plainly that this plan was built without them. The response is marked
``degraded`` so the UI can say so.
"""

from __future__ import annotations

import logging
from datetime import date as _date
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

import scheduler_engine

logger = logging.getLogger(__name__)

#: Matches the AI prompt's own horizon so a fallback plan covers the same
#: ground as the plan it stands in for.
DEFAULT_DAYS = 7


def _due_date(task: Mapping[str, Any]) -> _date | None:
    return scheduler_engine._parse_due(task)


def _day_name(value: _date) -> str:
    return value.strftime("%A")


#: Field names an estimate can arrive under, in order of preference. The
#: allocator reads ``est_minutes``/``duration_minutes``; callers upstream use
#: several other spellings, and a mismatch does not error — it silently falls
#: back to the 30-minute default, so a two-hour essay gets scheduled as half
#: an hour and the plan looks plausible while being wrong.
_ESTIMATE_FIELDS = (
    "est_minutes", "duration_minutes", "estimated_minutes",
    "estimate_minutes", "minutes", "estimated_time",
)


def _normalise(task: dict[str, Any]) -> dict[str, Any]:
    """Put the estimate where the allocator will find it."""
    if task.get("est_minutes"):
        return task
    for field in _ESTIMATE_FIELDS:
        raw = task.get(field)
        if raw in (None, ""):
            continue
        try:
            minutes = int(float(raw))
        except (TypeError, ValueError):
            continue
        if minutes > 0:
            task["est_minutes"] = minutes
            break
    return task


def build_schedule(
    assignments: Sequence[Mapping[str, Any]],
    *,
    hours_per_day: float = 2.0,
    preferred_time: str | None = None,
    availability: Mapping[str, Any] | None = None,
    commitments: str | None = None,
    dna: Any = None,
    days: int = DEFAULT_DAYS,
    today: _date | None = None,
) -> dict[str, Any]:
    """Return a schedule in the same shape the AI produces.

    Raises nothing the caller has to handle: an empty task list yields an
    empty-but-valid schedule rather than an exception, because the failure
    path this sits on is already an error path.
    """
    today = today or datetime.now().date()
    tasks = [_normalise(dict(a)) for a in (assignments or [])]

    capacity = scheduler_engine.plan_capacity(
        today, days, availability,
        preferred_time=preferred_time, commitments=commitments,
    )
    # A student with no availability recorded still needs a plan. Fall back
    # to the hours-per-day they asked for rather than returning nothing.
    if not capacity or not any(capacity.values()):
        minutes = max(30, int(round(float(hours_per_day or 2) * 60)))
        capacity = {
            (today + timedelta(days=offset)).isoformat(): minutes
            for offset in range(days)
        }

    by_day, unplaced = scheduler_engine.allocate_across_days(
        tasks, capacity, today, dna=dna)

    schedule: list[dict[str, Any]] = []
    total_minutes = 0

    for offset in range(days):
        current = today + timedelta(days=offset)
        iso = current.isoformat()
        blocks = by_day.get(iso) or []
        if not blocks:
            continue

        try:
            windows = scheduler_engine.windows_for_date(
                current, availability,
                preferred_time=preferred_time, commitments=commitments,
            )
        except Exception as exc:
            logger.warning("fallback: window lookup failed for %s: %s", iso, exc)
            windows = []

        if windows:
            placed, overflow = scheduler_engine.place_day_blocks(
                blocks, windows, dna=dna)
            if overflow:
                unplaced.extend(overflow)
        else:
            # No window model available. Keep the blocks in allocation order
            # without inventing clock times we cannot justify.
            placed = blocks

        for block in placed:
            total_minutes += int(block.get("duration_minutes") or 0)

        schedule.append({
            "date": iso,
            "day_name": _day_name(current),
            "total_hours": round(
                sum(int(b.get("duration_minutes") or 0) for b in placed) / 60, 2),
            "blocks": placed,
            # No invented tip. See the module docstring: a flat substitute
            # for writing that is specific to the week is worse than its
            # absence, and the banner already says the AI was unavailable.
            "daily_tip": "",
        })

    hours, minutes = divmod(total_minutes, 60)
    placed_count = sum(len(day["blocks"]) for day in schedule)

    overview = (
        f"Plan covering {placed_count} study block"
        f"{'' if placed_count == 1 else 's'} across "
        f"{len(schedule)} day{'' if len(schedule) == 1 else 's'}, "
        "ordered by what is most urgent."
    )
    if unplaced:
        overview += (f" {len(unplaced)} item"
                     f"{'' if len(unplaced) == 1 else 's'} did not fit in your "
                     "free time — they are listed below the plan.")

    return {
        "schedule": schedule,
        "overview": overview,
        "total_study_time": f"{hours} hours {minutes} minutes",
        # Consumed by the UI to explain why the writing is missing, and by
        # the tests to prove which path produced this.
        "degraded": True,
        "degraded_reason": "ai_unavailable",
        "unplaced": [
            {"assignment": u.get("assignment") or u.get("title") or "Task",
             "course": u.get("course") or ""}
            for u in unplaced
        ],
    }


def is_usable(assignments: Sequence[Mapping[str, Any]] | None) -> bool:
    """Whether a fallback plan is worth attempting at all."""
    return bool(assignments)
