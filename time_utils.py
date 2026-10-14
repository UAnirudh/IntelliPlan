"""Shared time helpers.

Exists for one reason: ``datetime.utcnow()`` is deprecated and scheduled for
removal. Python's own guidance is to move to timezone-aware objects, but this
codebase stores naive UTC in every timestamp column and compares those columns
against ``utcnow()`` results throughout. Swapping in an aware datetime would
raise ``TypeError: can't compare offset-naive and offset-aware datetimes`` at
the first comparison, so the migration has to happen in two steps: first stop
calling the deprecated function, then move storage to aware datetimes.

This is step one. ``utcnow()`` here returns exactly what ``datetime.utcnow()``
returned — the current UTC time with no tzinfo — and will keep working after
the deprecated function is removed.

``local_now()`` is the other half: what the wall clock says *for the student*.
"Due tomorrow" typed at 11pm in Los Angeles is 6am UTC the next day, and a
server that resolves "tomorrow" against its own clock files the task a day
late. Anything that turns words like "tonight" or "fri" into a date has to ask
this, not ``utcnow()``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

__all__ = ["utcnow", "local_now", "valid_timezone", "zone_for", "to_local", "to_utc"]


def utcnow() -> datetime:
    """Current UTC time as a naive datetime, like the old datetime.utcnow()."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def valid_timezone(name: str | None) -> str | None:
    """``name`` when it is an IANA zone this machine knows, else ``None``."""
    text = str(name or "").strip()
    if not text or len(text) > 64:
        return None
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(text)
        return text
    except Exception:
        return None


def local_now(tz_name: str | None, *, utc: datetime | None = None) -> datetime:
    """The student's wall-clock time as a naive datetime.

    ``utc`` is a naive UTC instant (as :func:`utcnow` returns) and exists so
    tests can pin the clock. An unknown or empty zone falls back to UTC rather
    than raising: a missing timezone should cost precision, not the request.
    """
    instant = (utc or utcnow()).replace(tzinfo=timezone.utc)
    zone = valid_timezone(tz_name)
    if zone is None:
        return instant.replace(tzinfo=None)
    from zoneinfo import ZoneInfo

    return instant.astimezone(ZoneInfo(zone)).replace(tzinfo=None)


# ── Wall-clock conversion ────────────────────────────────────────────
#
# A study plan is written in the student's wall-clock time ("4:30 PM"),
# while every stored timestamp is naive UTC. Anything that asks "is this
# block happening right now?" has to cross that line, and doing it with the
# server's own clock (``datetime.now()``) answers for whichever region the
# app is deployed in. These take the IANA name first, because a fixed
# offset cannot follow DST, and fall back to the offset only when no usable
# name is known -- the same precedence ``notifications.preferences`` uses.


def zone_for(tz_name: str | None):
    """``ZoneInfo`` for an IANA name, or None when it is empty or unknown."""
    name = valid_timezone(tz_name)
    if name is None:
        return None
    from zoneinfo import ZoneInfo

    return ZoneInfo(name)


def to_local(moment: datetime, tz_name: str | None = "", utc_offset_minutes: int = 0) -> datetime:
    """Naive UTC in, naive wall-clock out."""
    zone = zone_for(tz_name)
    if zone is None:
        return moment + timedelta(minutes=int(utc_offset_minutes or 0))
    return moment.replace(tzinfo=timezone.utc).astimezone(zone).replace(tzinfo=None)


def to_utc(local: datetime, tz_name: str | None = "", utc_offset_minutes: int = 0) -> datetime:
    """Naive wall-clock in, naive UTC out.

    ``fold=0`` takes the earlier reading of an hour a DST fall-back repeats.
    """
    zone = zone_for(tz_name)
    if zone is None:
        return local - timedelta(minutes=int(utc_offset_minutes or 0))
    return local.replace(tzinfo=zone, fold=0).astimezone(timezone.utc).replace(tzinfo=None)


def busy_intervals(start: datetime, end: datetime, tz_name=None, utc_offset_minutes=0):
    """Conservative local minute ranges for a positive UTC calendar interval.

    Wall-clock plans cannot distinguish the two readings of a repeated hour.
    Include both sides of any offset transition so that hour is never offered
    as free time. Probe by UTC day, then locate each transition precisely.
    """
    if end <= start:
        return {}
    def local(instant):
        return to_local(instant, tz_name, utc_offset_minutes)
    points = [local(start), local(end)]
    cursor = start
    while cursor < end:
        probe = min(end, cursor + timedelta(days=1))
        offset = local(cursor) - cursor
        if local(probe) - probe != offset:
            lo, hi = cursor, probe
            while hi - lo > timedelta(microseconds=1):
                mid = lo + (hi - lo) / 2
                if local(mid) - mid == offset:
                    lo = mid
                else:
                    hi = mid
            points.extend((local(lo) + timedelta(microseconds=1), local(hi)))
        cursor = probe
    first, finish = min(points), max(points)
    out = {}
    cursor = first
    while cursor < finish:
        midnight = datetime.combine(cursor.date(), datetime.min.time())
        piece_end = min(finish, midnight + timedelta(days=1))
        a = int((cursor - midnight).total_seconds() // 60)
        seconds = (piece_end - midnight).total_seconds()
        b = min(1440, int(-(-seconds // 60)))
        if b > a:
            out[cursor.date()] = [(a, b)]
        cursor = piece_end
    return out
