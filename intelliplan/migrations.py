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


def apply_notification_migrations(db: Any) -> list[str]:
    """Create the outbox table and add the notification columns to users.

    The ``users`` columns are added with ALTER rather than left to
    ``create_all``, which only creates missing *tables* and silently leaves
    an existing table short of its new columns — the failure mode being a
    500 on the first SELECT after deploy.
    """

    inspector = inspect(db.engine)
    existing = set(inspector.get_table_names())
    target = {"notification_outbox"}
    db.create_all()

    if "users" in existing:
        columns = {c["name"] for c in inspect(db.engine).get_columns("users")}
        # FALSE/TRUE, not 0/1. SQLite accepts either, Postgres rejects the
        # integer form for a BOOLEAN column outright — so on production these
        # ALTERs threw, were swallowed by the except below, and the columns
        # were quietly never created. The retry path in App._migrate_user_columns
        # would eventually cover it, but a migration that silently does
        # nothing on the only backend that matters is not a migration.
        additions = {
            "email_reminders_opt_in": "BOOLEAN DEFAULT FALSE",
            "utc_offset_minutes": "INTEGER DEFAULT 0",
            "quiet_hours_enabled": "BOOLEAN DEFAULT TRUE",
            "quiet_hours_start": "INTEGER DEFAULT 22",
            "quiet_hours_end": "INTEGER DEFAULT 7",
            "notification_kinds": "VARCHAR(512)",
            # Per-account brute-force counters. Without these the lockout
            # silently never engages on an existing deployment.
            "failed_login_count": "INTEGER DEFAULT 0",
            "login_locked_until": "TIMESTAMP",
        }
        for name, ddl in additions.items():
            if name in columns:
                continue
            try:
                db.session.execute(text(f"ALTER TABLE users ADD COLUMN {name} {ddl}"))
                db.session.commit()
            except Exception:
                # Another worker booting concurrently, or a backend that
                # spells the default differently. Not worth failing startup.
                db.session.rollback()

    return sorted(target & existing)


def apply_sync_migrations(db: Any) -> list[str]:
    """Ensure the offline replay ledger exists.

    Same idempotent ``create_all`` pattern as the tables above. The unique
    index on ``(user_id, op_id)`` comes from the model's table args, so it
    is created with the table rather than bolted on after — a ledger
    without that constraint would look fine and silently permit the
    duplicate writes it exists to prevent.
    """

    inspector = inspect(db.engine)
    existing = set(inspector.get_table_names())
    target = {"sync_ops"}
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


def apply_email_migrations(db: Any) -> list[str]:
    """Ensure the lifecycle-email tables exist.

    Same idempotent ``create_all`` pattern as the tables above. The unique
    constraint on ``(user_id, email_key)`` and the unique index on
    ``email_suppressions.email`` both come from the models' table args, so
    they are created with the table rather than bolted on after — a
    deduplication ledger without its constraint looks fine right up until
    two cron fires race and a student gets the same email twice.
    """

    inspector = inspect(db.engine)
    existing = set(inspector.get_table_names())
    target = {"email_sends", "email_suppressions"}
    db.create_all()
    return sorted(target & existing)


def apply_scheduler_audit_migrations(db: Any) -> list[str]:
    """Ensure the adaptive scheduler's audit tables exist.

    Same idempotent ``create_all`` pattern as the tables above. The unique
    constraint on ``(user_id, version)`` comes from the model's table args and
    is load-bearing rather than decorative: it is what makes concurrent plan
    generations fail one insert instead of silently producing two rows both
    called v15.
    """

    inspector = inspect(db.engine)
    existing = set(inspector.get_table_names())
    target = {"schedule_versions", "schedule_decisions"}
    db.create_all()

    # create_all() builds new tables but never alters existing ones, so a
    # column added after the first deploy needs saying out loud.
    if "schedule_decisions" in existing:
        columns = {c["name"] for c in inspector.get_columns("schedule_decisions")}
        if "identity_key" not in columns:
            db.session.execute(text(
                "ALTER TABLE schedule_decisions "
                "ADD COLUMN identity_key VARCHAR(64) DEFAULT ''"
            ))
            db.session.commit()

    return sorted(target & existing)

#: Columns holding third-party credentials, now encrypted at rest. Ciphertext
#: runs roughly 1.4x the plaintext plus a version prefix, so a token that fit
#: in VARCHAR(2048) does not fit once encrypted. Widening to TEXT has to land
#: before anything writes, or the first encrypted write fails on Postgres.
_ENCRYPTED_COLUMNS = [
    ("google_integrations", "token_data"),
    ("notion_integrations", "token"),
    ("canvas_integrations", "access_token"),
    ("canvas_integrations", "refresh_token"),
    ("classroom_integrations", "access_token"),
    ("classroom_integrations", "refresh_token"),
    ("blackboard_integrations", "access_token"),
    ("blackboard_integrations", "refresh_token"),
    ("moodle_integrations", "ws_token"),
]


def widen_encrypted_columns(db: Any) -> list[str]:
    """Convert credential columns to TEXT so ciphertext fits.

    SQLite ignores column widths entirely, so this is a no-op there and the
    statement is skipped rather than failed on.
    """
    widened: list[str] = []
    if db.engine.dialect.name == "sqlite":
        return widened

    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    for table, column in _ENCRYPTED_COLUMNS:
        if table not in tables:
            continue
        try:
            db.session.execute(
                text(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE TEXT")
            )
            db.session.commit()
            widened.append(f"{table}.{column}")
        except Exception:
            # Already TEXT, or another worker got there first. Neither is
            # worth failing a boot over.
            db.session.rollback()
    return widened
