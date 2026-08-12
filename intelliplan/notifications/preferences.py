"""Per-student notification preferences, including quiet hours.

Timezone handling is the part that is easy to get wrong and impossible to
hide. Every timestamp in this codebase is naive UTC (see ``time_utils``),
so "is it currently quiet hours for this student?" cannot be answered from
a stored timestamp alone — it needs their offset. The offset lives on the
student's profile and everything here converts explicitly rather than
assuming server-local time, which would give a student in Perth the quiet
hours of whichever region the app happens to be deployed in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any, Iterable

from intelliplan.notifications.events import (
    DEFAULT_ENABLED_KINDS,
    QUIET_HOURS_EXEMPT,
    Channel,
    EventKind,
)

__all__ = ["Preferences", "QuietHours"]


@dataclass(frozen=True, slots=True)
class QuietHours:
    """A nightly window during which non-urgent messages are held.

    Stored as local wall-clock hours because that is how people think about
    them: "don't text me after 10" means 10 where the student is.
    """

    start_hour: int = 22
    end_hour: int = 7
    enabled: bool = True

    def contains(self, local_dt: datetime) -> bool:
        if not self.enabled:
            return False
        hour = local_dt.hour
        if self.start_hour == self.end_hour:
            return False
        if self.start_hour < self.end_hour:
            return self.start_hour <= hour < self.end_hour
        # Window wraps past midnight — the normal case for 22:00–07:00.
        return hour >= self.start_hour or hour < self.end_hour

    def next_open(self, local_dt: datetime) -> datetime:
        """First moment after ``local_dt`` that is outside the window."""
        if not self.contains(local_dt):
            return local_dt
        candidate = local_dt.replace(minute=0, second=0, microsecond=0)
        # At most 24 hourly steps, so this always terminates even if the
        # window is configured strangely.
        for _ in range(25):
            candidate += timedelta(hours=1)
            if not self.contains(candidate):
                return candidate
        return local_dt


@dataclass(frozen=True, slots=True)
class Preferences:
    """Everything needed to decide whether, when, and how to notify."""

    user_id: int
    #: Minutes east of UTC. Naive-UTC storage means this is the only way to
    #: reach the student's wall clock.
    utc_offset_minutes: int = 0
    channels: frozenset[Channel] = field(default_factory=frozenset)
    kinds: frozenset[EventKind] = field(default_factory=lambda: DEFAULT_ENABLED_KINDS)
    quiet_hours: QuietHours = field(default_factory=QuietHours)
    #: How far ahead a session reminder fires.
    lead_minutes: int = 30
    #: Hard ceiling per rolling day, per channel. Protects a student whose
    #: week genuinely is a disaster from being told about it forty times.
    daily_cap: int = 12

    # ── Derived ───────────────────────────────────────────────────────

    def to_local(self, moment: datetime) -> datetime:
        return moment + timedelta(minutes=self.utc_offset_minutes)

    def to_utc(self, local: datetime) -> datetime:
        return local - timedelta(minutes=self.utc_offset_minutes)

    def wants(self, kind: EventKind, channel: Channel) -> bool:
        return kind in self.kinds and channel in self.channels

    def delivery_time(self, kind: EventKind, earliest_utc: datetime) -> datetime:
        """When this message may actually go out.

        Urgent kinds ignore quiet hours — a session starting in fifteen
        minutes is exactly the thing worth a late buzz, and holding it until
        morning delivers a reminder for something that already didn't
        happen. Everything else is pushed to the end of the window.
        """
        if kind in QUIET_HOURS_EXEMPT:
            return earliest_utc
        local = self.to_local(earliest_utc)
        if not self.quiet_hours.contains(local):
            return earliest_utc
        return self.to_utc(self.quiet_hours.next_open(local))


def preferences_from_user(
    user: Any,
    *,
    push_subscribed: bool = False,
) -> Preferences:
    """Read :class:`Preferences` off the ORM ``User`` row.

    Every field is defensive: this runs inside the delivery sweep, and a
    single malformed profile must not stop the whole batch. Unknown values
    fall back to the safe default, which is always "fewer notifications".
    """
    channels: set[Channel] = set()
    if getattr(user, "push_reminders_opt_in", False) and push_subscribed:
        channels.add(Channel.PUSH)
    if getattr(user, "sms_reminders_opt_in", False) and getattr(user, "phone", None):
        channels.add(Channel.SMS)
    if getattr(user, "email_reminders_opt_in", False) and getattr(user, "email", None):
        channels.add(Channel.EMAIL)

    kinds = DEFAULT_ENABLED_KINDS
    raw_kinds = getattr(user, "notification_kinds", None)
    if raw_kinds:
        parsed = _parse_kinds(raw_kinds)
        if parsed:
            kinds = parsed

    return Preferences(
        user_id=int(getattr(user, "id", 0) or 0),
        utc_offset_minutes=_clamp_offset(getattr(user, "utc_offset_minutes", 0)),
        channels=frozenset(channels),
        kinds=kinds,
        quiet_hours=QuietHours(
            start_hour=_clamp_hour(getattr(user, "quiet_hours_start", 22), 22),
            end_hour=_clamp_hour(getattr(user, "quiet_hours_end", 7), 7),
            enabled=bool(getattr(user, "quiet_hours_enabled", True)),
        ),
        lead_minutes=_clamp_lead(getattr(user, "reminder_lead_minutes", 30)),
    )


def _parse_kinds(raw: Any) -> frozenset[EventKind]:
    if isinstance(raw, str):
        parts: Iterable[str] = (p.strip() for p in raw.split(",") if p.strip())
    elif isinstance(raw, (list, tuple, set, frozenset)):
        parts = (str(p).strip() for p in raw)
    else:
        return frozenset()
    out: set[EventKind] = set()
    for part in parts:
        try:
            out.add(EventKind(part))
        except ValueError:
            continue  # a kind we retired, or a typo — never fatal
    return frozenset(out)


def _clamp_offset(value: Any) -> int:
    try:
        offset = int(value)
    except (TypeError, ValueError):
        return 0
    # Real offsets span UTC-12 to UTC+14.
    return max(-12 * 60, min(14 * 60, offset))


def _clamp_hour(value: Any, default: int) -> int:
    try:
        hour = int(value)
    except (TypeError, ValueError):
        return default
    return hour if 0 <= hour <= 23 else default


def _clamp_lead(value: Any) -> int:
    try:
        lead = int(value)
    except (TypeError, ValueError):
        return 30
    return max(5, min(24 * 60, lead))
