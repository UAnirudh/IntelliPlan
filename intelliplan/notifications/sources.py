"""Turning plan and session state into notification events.

This is the module that makes notifications *real* rather than decorative:
every event here is derived from the same ``schedule_data`` the student is
looking at and the same session rows the estimator learns from. Nothing is
invented, and nothing fires for a plan that does not exist.

Pure by construction — the caller passes the plan, the sessions, and the
clock, so every rule below is testable without a database.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from intelliplan.notifications.events import EventKind, NotificationEvent

logger = logging.getLogger(__name__)

__all__ = [
    "session_reminders",
    "missed_sessions",
    "deadline_reminders",
    "overload_warning",
    "session_completed_event",
    "reschedule_event",
]

#: A session is "missed" only once its window has been over by this long.
#: Firing the moment a block's end time passes would alert a student who is
#: mid-sentence and three minutes over, which is the opposite of helpful.
MISSED_GRACE_MINUTES = 25

#: Deadlines worth a message. More than three days out is not news, and a
#: daily drumbeat for a fortnight teaches people to swipe notifications away.
DEADLINE_DAYS = (0, 1, 3)


def _parse_iso(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _blocks(plan: Mapping[str, Any] | None) -> list[tuple[dict, dict]]:
    """Flatten ``schedule_data`` to (day, block) pairs, skipping breaks."""
    out: list[tuple[dict, dict]] = []
    for day in (plan or {}).get("schedule", []) or []:
        if not isinstance(day, Mapping):
            continue
        for block in day.get("blocks", []) or []:
            if not isinstance(block, Mapping) or block.get("is_break"):
                continue
            out.append((dict(day), dict(block)))
    return out


def session_reminders(
    plan: Mapping[str, Any] | None,
    user_id: int,
    now: datetime,
    lead_minutes: int = 30,
) -> list[NotificationEvent]:
    """"Your session starts soon" for blocks inside the lead window.

    Only blocks that have not started yet and are not already done. The
    dedupe key is the block id plus its date, so a plan rebuilt between
    sweeps does not re-notify for the same sitting.
    """
    events: list[NotificationEvent] = []
    horizon = now + timedelta(minutes=max(1, lead_minutes))

    for day, block in _blocks(plan):
        if block.get("done"):
            continue
        start = _parse_iso(block.get("start_iso"))
        if start is None or not (now <= start <= horizon):
            continue
        minutes_until = max(0, int((start - now).total_seconds() // 60))
        events.append(
            NotificationEvent(
                kind=EventKind.SESSION_UPCOMING,
                user_id=user_id,
                dedupe_key=f"{block.get('id')}:{day.get('date')}",
                context={
                    "title": block.get("parent_title") or block.get("assignment"),
                    "course": block.get("course"),
                    "minutes_until": minutes_until,
                    "planned_minutes": block.get("duration_minutes"),
                },
                url="/active",
            )
        )
    return events


def missed_sessions(
    plan: Mapping[str, Any] | None,
    user_id: int,
    now: datetime,
    started_block_ids: Iterable[str] = (),
) -> list[NotificationEvent]:
    """Blocks whose window closed with nothing started against them.

    ``started_block_ids`` comes from ``active_sessions``: a student who
    started the work does not get told they missed it, even if they never
    pressed finish.
    """
    started = {str(b) for b in started_block_ids if b}
    cutoff = now - timedelta(minutes=MISSED_GRACE_MINUTES)
    events: list[NotificationEvent] = []

    for day, block in _blocks(plan):
        if block.get("done") or str(block.get("id")) in started:
            continue
        end = _parse_iso(block.get("end_iso"))
        if end is None or end > cutoff:
            continue
        # Only the day it happened. A block missed last Tuesday is history,
        # and the plan has already moved on without it.
        if (now.date() - end.date()).days > 0:
            continue
        events.append(
            NotificationEvent(
                kind=EventKind.SESSION_MISSED,
                user_id=user_id,
                dedupe_key=f"{block.get('id')}:{day.get('date')}",
                context={
                    "title": block.get("parent_title") or block.get("assignment"),
                    "course": block.get("course"),
                },
                url="/active",
            )
        )
    return events


def deadline_reminders(
    plan: Mapping[str, Any] | None,
    user_id: int,
    today: date,
) -> list[NotificationEvent]:
    """One message per assignment as its deadline crosses a threshold.

    Grouped by task rather than by block: a project split across six
    sittings has one deadline, and six identical warnings about it is how a
    student learns to ignore the seventh.
    """
    remaining: dict[str, dict[str, Any]] = {}

    for _day, block in _blocks(plan):
        due = block.get("due_date")
        if not due:
            continue
        try:
            due_date = date.fromisoformat(str(due)[:10])
        except ValueError:
            continue
        key = str(block.get("task_id") or block.get("parent_title") or block.get("assignment"))
        entry = remaining.setdefault(
            key,
            {
                "title": block.get("parent_title") or block.get("assignment"),
                "course": block.get("course"),
                "due": due_date,
                "minutes": 0,
            },
        )
        if not block.get("done"):
            entry["minutes"] += int(block.get("duration_minutes") or 0)

    events: list[NotificationEvent] = []
    for key, entry in remaining.items():
        days_until = (entry["due"] - today).days
        if days_until not in DEADLINE_DAYS or entry["minutes"] <= 0:
            continue
        events.append(
            NotificationEvent(
                kind=EventKind.DEADLINE_APPROACHING,
                user_id=user_id,
                # Keyed by the threshold as well as the task, so a student
                # hears at three days *and* again on the day, but never
                # twice for the same threshold.
                dedupe_key=f"{key}:{entry['due'].isoformat()}:d{days_until}",
                context={
                    "title": entry["title"],
                    "course": entry["course"],
                    "days_until": days_until,
                    "remaining_minutes": entry["minutes"],
                },
                url="/scheduler",
            )
        )
    return events


def overload_warning(
    plan: Mapping[str, Any] | None,
    user_id: int,
    today: date,
) -> list[NotificationEvent]:
    """One warning per day when the plan genuinely does not fit."""
    plan = plan or {}
    if not plan.get("overloaded"):
        return []
    deferred = plan.get("deferred") or []
    minutes = sum(int(d.get("minutes") or 0) for d in deferred if isinstance(d, Mapping))
    if minutes <= 0:
        return []
    return [
        NotificationEvent(
            kind=EventKind.PLAN_OVERLOADED,
            user_id=user_id,
            dedupe_key=f"overload:{today.isoformat()}",
            context={
                "minutes_short": minutes,
                "detail": "\n".join(
                    f"- {d.get('title')}: {d.get('minutes')} min ({d.get('reason')})"
                    for d in deferred[:8]
                    if isinstance(d, Mapping)
                ),
            },
            url="/scheduler",
        )
    ]


def session_completed_event(
    user_id: int,
    session_id: Any,
    title: str,
    actual_minutes: int,
) -> NotificationEvent:
    return NotificationEvent(
        kind=EventKind.SESSION_COMPLETED,
        user_id=user_id,
        dedupe_key=f"session:{session_id}",
        context={"title": title, "actual_minutes": actual_minutes},
        url="/active",
    )


def reschedule_event(
    user_id: int,
    moved_count: int,
    reason: str,
    when: date,
) -> NotificationEvent | None:
    """Only worth saying when something actually moved."""
    if moved_count <= 0:
        return None
    return NotificationEvent(
        kind=EventKind.SESSION_RESCHEDULED,
        user_id=user_id,
        dedupe_key=f"reschedule:{when.isoformat()}",
        context={"count": moved_count, "reason": reason},
        url="/scheduler",
    )


def events_for_plan(
    plan: Mapping[str, Any] | None,
    user_id: int,
    now: datetime,
    lead_minutes: int = 30,
    started_block_ids: Iterable[str] = (),
) -> list[NotificationEvent]:
    """Everything a single sweep of one student's plan should raise."""
    today = now.date()
    return [
        *session_reminders(plan, user_id, now, lead_minutes),
        *missed_sessions(plan, user_id, now, started_block_ids),
        *deadline_reminders(plan, user_id, today),
        *overload_warning(plan, user_id, today),
    ]
