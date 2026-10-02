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
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

__all__ = ["utcnow", "zone_for", "to_local", "to_utc"]


def utcnow() -> datetime:
    """Current UTC time as a naive datetime, like the old datetime.utcnow()."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
    if not tz_name:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(str(tz_name))
    except Exception:
        return None


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
