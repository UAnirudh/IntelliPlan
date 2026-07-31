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

from datetime import datetime, timezone

__all__ = ["utcnow"]


def utcnow() -> datetime:
    """Current UTC time as a naive datetime, like the old datetime.utcnow()."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
