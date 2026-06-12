"""Boot-time, idempotent DDL for Command Center tables.

Mirrors the existing ``apply_study_schema_migrations`` pattern used by
``App.py``. This will be replaced by Alembic before the next destructive
schema change — tracked in ``docs/command-center/06-implementation-roadmap.md``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect


def apply_command_center_migrations(db: Any) -> list[str]:
    """Ensure Command Center tables exist.

    Relies on ``db.create_all()`` to do the actual work — every
    SQLAlchemy backend we ship to (SQLite locally, Postgres on Railway)
    supports the "create if absent" path without DDL races we own.

    Returns the list of tables that were already present, which is useful
    for boot-log telemetry but not load-bearing.
    """

    inspector = inspect(db.engine)
    existing = set(inspector.get_table_names())
    target = {"briefing_cache", "health_snapshots", "student_signals"}
    db.create_all()
    return sorted(target & existing)
