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


def apply_active_session_migrations(db: Any) -> list[str]:
    """Ensure Active-study tables exist and carry their indexes.

    ``create_all`` handles the tables. The explicit index check exists
    because an instance that ran an earlier build of this feature has the
    tables but not the composite indexes, and the session-history read on
    the Active page is a per-user, time-ordered scan that is genuinely slow
    without them once a student has a few hundred sittings.
    """

    inspector = inspect(db.engine)
    existing = set(inspector.get_table_names())
    target = {"active_sessions", "active_focus_samples"}
    db.create_all()

    wanted = {
        "active_sessions": [
            ("ix_active_sessions_user_started", "active_sessions (user_id, started_at)"),
            ("ix_active_sessions_state", "active_sessions (state)"),
        ],
        "active_focus_samples": [
            (
                "ix_focus_samples_session_offset",
                "active_focus_samples (session_id, offset_seconds)",
            ),
        ],
    }
    for table, indexes in wanted.items():
        try:
            present = {ix["name"] for ix in inspect(db.engine).get_indexes(table)}
        except Exception:
            continue
        for name, definition in indexes:
            if name in present:
                continue
            try:
                db.session.execute(
                    text(f"CREATE INDEX IF NOT EXISTS {name} ON {definition}")
                )
            except Exception:
                # A backend that rejects IF NOT EXISTS, or a race with
                # another worker booting, is not worth failing startup over.
                db.session.rollback()
    db.session.commit()
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
