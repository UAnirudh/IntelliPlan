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

from datetime import datetime, timezone

__all__ = ["utcnow", "local_now", "valid_timezone"]


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
