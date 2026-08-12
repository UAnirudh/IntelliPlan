"""Notification events — the vocabulary, and how each one reads.

An event is something that *happened in the plan*, not a message. The
distinction matters: the rest of the system emits ``SESSION_MISSED``, and
this module is the only place that decides whether that becomes "You missed
Chemistry" in a push, an SMS, or an email — and what the dedupe key is.

Keeping that in one pure module means the wording, the throttling, and the
per-channel length limits are all reviewable in one file, and testable
without a database, a provider, or a clock.

Why events carry their own dedupe key
-------------------------------------
The dispatcher runs on a cron and may run twice, overlap itself, or replay
after a crash. Every event therefore declares a key that is stable for "the
same real-world fact" — ``session_missed:412:2026-08-12`` is the same fact
however many times the sweep notices it. Deduplication is then a database
uniqueness problem rather than a timing problem, which is the only kind of
solution that survives concurrent workers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Mapping

__all__ = [
    "EventKind",
    "Channel",
    "NotificationEvent",
    "RenderedMessage",
    "render",
    "SMS_MAX_CHARS",
]

#: One SMS segment. Longer messages split and bill per segment, and a
#: two-part reminder is not twice as useful.
SMS_MAX_CHARS = 160


class Channel(str, Enum):
    PUSH = "push"
    SMS = "sms"
    EMAIL = "email"


class EventKind(str, Enum):
    """Everything the scheduler can tell a student about.

    Ordered roughly by how much it interrupts. Adding a kind here means
    adding it to ``_TEMPLATES`` and to the preference matrix — a kind with
    no template raises at render time rather than silently sending an empty
    notification.
    """

    #: A study session starts soon.
    SESSION_UPCOMING = "session_upcoming"
    #: A scheduled session's window passed with no session started.
    SESSION_MISSED = "session_missed"
    #: The plan moved work after the student fell behind or finished early.
    SESSION_RESCHEDULED = "session_rescheduled"
    #: A sitting was finished — the encouraging one, and the only one that
    #: is off by default, because congratulating someone for work they know
    #: they did is the fastest way to get notifications muted.
    SESSION_COMPLETED = "session_completed"
    #: An assignment is due soon.
    DEADLINE_APPROACHING = "deadline_approaching"
    #: Work could not be fitted before its deadline.
    PLAN_OVERLOADED = "plan_overloaded"
    #: The plan changed materially (new work landed, capacity changed).
    PLAN_CHANGED = "plan_changed"


#: Kinds a student gets unless they turn them off. Completion pats and
#: plan-changed chatter are opt-in: they are the two that arrive most often
#: and carry the least new information.
DEFAULT_ENABLED_KINDS: frozenset[EventKind] = frozenset(
    {
        EventKind.SESSION_UPCOMING,
        EventKind.SESSION_MISSED,
        EventKind.SESSION_RESCHEDULED,
        EventKind.DEADLINE_APPROACHING,
        EventKind.PLAN_OVERLOADED,
    }
)

#: Kinds important enough to deliver outside quiet hours. Everything else
#: waits for morning — a planner that wakes someone at 2 AM to say their
#: Tuesday got busier has misunderstood its job.
QUIET_HOURS_EXEMPT: frozenset[EventKind] = frozenset(
    {EventKind.SESSION_UPCOMING, EventKind.DEADLINE_APPROACHING}
)


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    """One thing that happened, ready to be turned into messages."""

    kind: EventKind
    user_id: int
    #: Stable identifier for the underlying fact — see the module docstring.
    dedupe_key: str
    #: Template variables. Missing keys render as empty rather than raising,
    #: because a missing course name must not cost a student their reminder.
    context: Mapping[str, Any] = field(default_factory=dict)
    #: When this should be delivered. ``None`` means "as soon as possible".
    deliver_at: datetime | None = None
    #: Where tapping the notification should land.
    url: str = "/active"

    def key_for(self, channel: Channel) -> str:
        return f"{self.kind.value}:{self.dedupe_key}:{channel.value}"


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    title: str
    body: str
    url: str

    def as_sms(self) -> str:
        """SMS has no title field, so the title has to earn its place inline."""
        text = f"{self.title}: {self.body}" if self.title else self.body
        if len(text) <= SMS_MAX_CHARS:
            return text
        return text[: SMS_MAX_CHARS - 1].rstrip() + "…"


# ── Templates ─────────────────────────────────────────────────────────
# Written as functions rather than format strings because most of these
# need a conditional (one minute vs. many, one task vs. several), and a
# format string that needs an if is a function with extra steps.


def _plural(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def _minutes_phrase(minutes: Any) -> str:
    try:
        m = int(minutes)
    except (TypeError, ValueError):
        return "soon"
    if m <= 0:
        return "now"
    if m < 60:
        return f"in {m} min"
    hours = m // 60
    rest = m % 60
    if rest == 0:
        return f"in {hours}h"
    return f"in {hours}h {rest}m"


def _get(ctx: Mapping[str, Any], key: str, default: str = "") -> str:
    value = ctx.get(key)
    return "" if value is None else str(value)


def _session_upcoming(ctx: Mapping[str, Any]) -> tuple[str, str]:
    title = _get(ctx, "title", "Study session")
    course = _get(ctx, "course")
    when = _minutes_phrase(ctx.get("minutes_until"))
    duration = ctx.get("planned_minutes")
    tail = f" ({duration} min)" if duration else ""
    subject = f" · {course}" if course else ""
    return (f"Up next{subject}", f"{title} starts {when}{tail}.")


def _session_missed(ctx: Mapping[str, Any]) -> tuple[str, str]:
    title = _get(ctx, "title", "your session")
    # No scolding. The student knows they missed it; the useful half of the
    # message is what the plan did about it.
    moved = ctx.get("moved_to")
    if moved:
        return ("Session moved", f"You missed {title}. It's back on your plan for {moved}.")
    return ("Session missed", f"{title} didn't happen. Open Active to reschedule it.")


def _session_rescheduled(ctx: Mapping[str, Any]) -> tuple[str, str]:
    count = int(ctx.get("count") or 1)
    reason = _get(ctx, "reason")
    body = f"{count} {_plural(count, 'session', 'sessions')} moved"
    if reason:
        body += f" — {reason}"
    return ("Plan updated", body + ".")


def _session_completed(ctx: Mapping[str, Any]) -> tuple[str, str]:
    title = _get(ctx, "title", "that session")
    minutes = ctx.get("actual_minutes")
    if minutes:
        return ("Session logged", f"{title} — {minutes} min. Your estimates just got sharper.")
    return ("Session logged", f"{title} is done.")


def _deadline_approaching(ctx: Mapping[str, Any]) -> tuple[str, str]:
    title = _get(ctx, "title", "An assignment")
    course = _get(ctx, "course")
    days = ctx.get("days_until")
    remaining = ctx.get("remaining_minutes")
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = None
    when = "due today" if days == 0 else ("due tomorrow" if days == 1 else f"due in {days} days")
    subject = f"{title} ({course})" if course else title
    tail = f" — {remaining} min of work left on your plan." if remaining else "."
    return ("Deadline coming up", f"{subject} is {when}{tail}")


def _plan_overloaded(ctx: Mapping[str, Any]) -> tuple[str, str]:
    minutes = ctx.get("minutes_short")
    if minutes:
        return (
            "Your week doesn't fit",
            f"{minutes} min of work has no room before its deadlines. "
            f"Open the scheduler to cut, shorten, or add time.",
        )
    return ("Your week doesn't fit", "Some work has no room before its deadlines.")


def _plan_changed(ctx: Mapping[str, Any]) -> tuple[str, str]:
    count = int(ctx.get("new_tasks") or 0)
    if count:
        return (
            "Plan rebuilt",
            f"{count} new {_plural(count, 'assignment', 'assignments')} landed and your plan adjusted.",
        )
    return ("Plan rebuilt", "Your schedule changed. Take a look before you start.")


_TEMPLATES = {
    EventKind.SESSION_UPCOMING: _session_upcoming,
    EventKind.SESSION_MISSED: _session_missed,
    EventKind.SESSION_RESCHEDULED: _session_rescheduled,
    EventKind.SESSION_COMPLETED: _session_completed,
    EventKind.DEADLINE_APPROACHING: _deadline_approaching,
    EventKind.PLAN_OVERLOADED: _plan_overloaded,
    EventKind.PLAN_CHANGED: _plan_changed,
}


def render(event: NotificationEvent, channel: Channel) -> RenderedMessage:
    """Turn an event into the words one channel will carry.

    The same event reads differently per channel by length, not by content:
    an SMS costs money per segment and an email has room for the context a
    push notification has to drop. Saying *different things* per channel is
    how a student ends up with two contradictory versions of one fact.
    """
    template = _TEMPLATES.get(event.kind)
    if template is None:
        raise KeyError(f"No template for event kind {event.kind!r}")
    title, body = template(event.context)

    if channel is Channel.EMAIL:
        # Email is the one place there is room to say why.
        extra = _get(event.context, "detail")
        if extra:
            body = f"{body}\n\n{extra}"
    return RenderedMessage(title=title, body=body, url=event.url)


def event_for_session_upcoming(
    user_id: int,
    session_id: str,
    day: date,
    **context: Any,
) -> NotificationEvent:
    """Helper so callers cannot invent an inconsistent dedupe key."""
    return NotificationEvent(
        kind=EventKind.SESSION_UPCOMING,
        user_id=user_id,
        dedupe_key=f"{session_id}:{day.isoformat()}",
        context=context,
        url="/active",
    )
