"""Boot-time, idempotent DDL for Command Center and Learning Graph tables.

Mirrors the existing ``apply_study_schema_migrations`` pattern used by
``App.py``. This will be replaced by Alembic before the next destructive
schema change — tracked in ``docs/command-center/06-implementation-roadmap.md``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect, text


def apply_media_balance_migrations(db: Any) -> None:
    """Add missing columns to media_balance_prefs if they were created
    before the model gained night-nudge fields.  Idempotent."""

    inspector = inspect(db.engine)
    if "media_balance_prefs" not in inspector.get_table_names():
        return

    existing = {col["name"] for col in inspector.get_columns("media_balance_prefs")}
    dialect = db.engine.dialect.name
    dt_type = "TIMESTAMP" if dialect != "sqlite" else "DATETIME"

    new_columns = {
        "night_nudges_enabled": "BOOLEAN DEFAULT TRUE",
        "night_start_hour": "INTEGER DEFAULT 22",
        "night_cadence_minutes": "INTEGER DEFAULT 10",
        "updated_at": dt_type,
    }
    for name, ddl in new_columns.items():
        if name not in existing:
            db.session.execute(
                text(f"ALTER TABLE media_balance_prefs ADD COLUMN {name} {ddl}")
            )
    db.session.commit()


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


def apply_learning_graph_migrations(db: Any) -> list[str]:
    """Ensure Learning Graph tables exist.

    Same idempotent pattern as ``apply_command_center_migrations``.
    """

    inspector = inspect(db.engine)
    existing = set(inspector.get_table_names())
    target = {"student_profiles", "concept_mastery", "learning_events"}
    db.create_all()
    return sorted(target & existing)
