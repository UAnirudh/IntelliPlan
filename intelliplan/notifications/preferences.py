"""Per-student notification preferences, including quiet hours.

Timezone handling is the part that is easy to get wrong and impossible to
hide. Every timestamp in this codebase is naive UTC (see ``time_utils``),
so "is it currently quiet hours for this student?" cannot be answered from
a stored timestamp alone — it needs their timezone. Everything here
converts explicitly rather than assuming server-local time, which would
give a student in Perth the quiet hours of whichever region the app
happens to be deployed in.

Prefer ``tz_name`` over ``utc_offset_minutes``
----------------------------------------------
``utc_offset_minutes`` was the original mechanism and it has two problems.

The first is that nothing ever set it: no client posts the field, so it sat
at its column default of 0 for every student, and quiet hours were applied
to UTC. For a student in Los Angeles that is not a small drift, it is an
inversion — their 22:00–07:00 window landed on 15:00–00:00 local, so
notifications were suppressed through the whole after-school study block
and allowed at two in the morning. Fourteen of twenty-four hours behaved
the opposite of how they were configured.

The second is that a fixed offset cannot express DST, so even a correctly
populated one is wrong for half the year.

``tz_name`` is an IANA name — the same one the streak engine already stores
and resolves with ZoneInfo — and it is converted per moment, so a summer
reminder and a winter reminder both land at the hour the student chose.
The integer offset is kept as a fallback for a profile that has no
timezone recorded yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    #: Minutes east of UTC. The fallback, used only when ``tz_name`` is
    #: empty or unrecognised: it cannot express DST, so it is right for at
    #: most half the year in any region that observes it.
    utc_offset_minutes: int = 0
    #: IANA timezone name (e.g. "America/Los_Angeles"). Preferred over the
    #: fixed offset above, and resolved per moment so DST is handled.
    tz_name: str = ""
    channels: frozenset[Channel] = field(default_factory=frozenset)
    kinds: frozenset[EventKind] = field(default_factory=lambda: DEFAULT_ENABLED_KINDS)
    quiet_hours: QuietHours = field(default_factory=QuietHours)
    #: How far ahead a session reminder fires.
    lead_minutes: int = 30
    #: Hard ceiling per rolling day, per channel. Protects a student whose
    #: week genuinely is a disaster from being told about it forty times.
    daily_cap: int = 12
    #: Streak-at-risk by email even without the general reminder opt-in.
    #: See ``User.streak_emails_opt_in`` for why this one defaults on.
    streak_email: bool = False

    # ── Derived ───────────────────────────────────────────────────────

    def _zone(self) -> ZoneInfo | None:
        """The student's zone, or None to fall back to the fixed offset."""
        if not self.tz_name:
            return None
        try:
            return ZoneInfo(self.tz_name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            # A profile carrying a name this machine has no data for must
            # not stop the delivery sweep; the offset still gets them close.
            return None

    def to_local(self, moment: datetime) -> datetime:
        """Naive UTC in, naive local out."""
        zone = self._zone()
        if zone is None:
            return moment + timedelta(minutes=self.utc_offset_minutes)
        return moment.replace(tzinfo=ZoneInfo("UTC")).astimezone(zone).replace(tzinfo=None)

    def to_utc(self, local: datetime) -> datetime:
        """Naive local in, naive UTC out.

        ``fold=0`` picks the first of the two readings of an hour repeated
        by a DST fall-back, which is the earlier real instant -- so a
        message released at the end of quiet hours goes out at the first
        02:30 rather than being held an extra hour. A local time that does
        not exist at all (the spring-forward gap) is mapped by ZoneInfo to
        a real instant, which is what matters: something sendable.
        """
        zone = self._zone()
        if zone is None:
            return local - timedelta(minutes=self.utc_offset_minutes)
        return (
            local.replace(tzinfo=zone, fold=0)
            .astimezone(ZoneInfo("UTC"))
            .replace(tzinfo=None)
        )

    def wants(self, kind: EventKind, channel: Channel) -> bool:
        if kind not in self.kinds:
            return False
        if channel in self.channels:
            return True
        return (
            kind is EventKind.STREAK_AT_RISK
            and channel is Channel.EMAIL
            and self.streak_email
        )

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
    tz_name: str | None = None,
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
        # Passed in rather than read off the user row: the timezone lives on
        # the streak record, and this module deliberately does not query.
        tz_name=str(tz_name or getattr(user, "timezone", "") or ""),
        channels=frozenset(channels),
        kinds=kinds,
        quiet_hours=QuietHours(
            start_hour=_clamp_hour(getattr(user, "quiet_hours_start", 22), 22),
            end_hour=_clamp_hour(getattr(user, "quiet_hours_end", 7), 7),
            enabled=bool(getattr(user, "quiet_hours_enabled", True)),
        ),
        lead_minutes=_clamp_lead(getattr(user, "reminder_lead_minutes", 30)),
        streak_email=bool(getattr(user, "email", None))
        and getattr(user, "streak_emails_opt_in", True) is not False,
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
