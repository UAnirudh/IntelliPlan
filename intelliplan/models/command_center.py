"""ORM models for the Command Center.

Three additive tables:

* ``briefing_cache`` — per-user cached AI briefing so AI is never on the
  request path.
* ``health_snapshots`` — daily Academic Health Score breakdown, used for
  day-over-day deltas without recomputing yesterday.
* ``student_signals`` — append-only event log; foundation for the
  future derived-profile (Tier 2 memory) layer.

The module never imports the application or its ``db`` instance directly.
Call :func:`register` from ``App.py`` after ``db = SQLAlchemy(app)`` is
created. The function returns the three model classes so callers can
keep them on the application's module namespace.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from time_utils import utcnow
from typing import Any


def _utcnow() -> datetime:
    # Naive UTC matches the rest of the IntelliPlan ORM (``datetime.utcnow``).
    return utcnow()


def _default_expiry() -> datetime:
    return _utcnow() + timedelta(hours=6)


def register(db: Any) -> tuple[type, type, type]:
    """Define the three Command Center models against ``db``.

    Idempotent: if SQLAlchemy already knows a model with the same
    ``__tablename__`` (because ``App.py`` was reloaded under gunicorn's
    ``--max-requests``), reuse the existing class instead of redefining it.
    """

    registry = getattr(db.Model, "registry", None)
    existing: dict[str, type] = {}
    if registry is not None:
        for mapper in registry.mappers:
            cls = mapper.class_
            existing[getattr(cls, "__tablename__", "")] = cls

    if {"briefing_cache", "health_snapshots", "student_signals"} <= set(existing):
        return (
            existing["briefing_cache"],
            existing["health_snapshots"],
            existing["student_signals"],
        )

    class BriefingCache(db.Model):
        """Cached AI briefing per user.

        One row per user. Refreshed when ``payload_hash`` no longer
        matches a freshly built :class:`~intelliplan.domain.plan.TodayPayload`
        or when ``expires_at`` is in the past.
        """

        __tablename__ = "briefing_cache"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(
            db.Integer,
            db.ForeignKey("users.id"),
            unique=True,
            nullable=False,
            index=True,
        )
        payload_hash = db.Column(db.String(64), nullable=False, default="")
        text_json = db.Column(db.Text, nullable=False, default="{}")
        model = db.Column(db.String(64), nullable=True)
        generated_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
        expires_at = db.Column(db.DateTime, nullable=False, default=_default_expiry, index=True)

    class HealthSnapshot(db.Model):
        """Daily Academic Health Score record.

        ``(user_id, snapshot_date)`` is unique — the snapshot for a given
        day is upserted by the daily refresh cron.
        """

        __tablename__ = "health_snapshots"
        __table_args__ = (
            db.UniqueConstraint("user_id", "snapshot_date", name="uq_health_user_date"),
            db.Index("ix_health_user_date_desc", "user_id", "snapshot_date"),
        )

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
        snapshot_date = db.Column(db.Date, nullable=False)
        score = db.Column(db.Integer, nullable=False, default=100)
        overdue_count = db.Column(db.Integer, nullable=False, default=0)
        high_stakes_soon_count = db.Column(db.Integer, nullable=False, default=0)
        failing_courses_count = db.Column(db.Integer, nullable=False, default=0)
        declining_courses_count = db.Column(db.Integer, nullable=False, default=0)
        completion_rate_7d = db.Column(db.Float, nullable=False, default=0.0)
        schedule_balance_index = db.Column(db.Float, nullable=False, default=0.0)
        components_json = db.Column(db.Text, nullable=False, default="[]")
        created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    class StudentSignal(db.Model):
        """Append-only event log.

        Feeds the future derived-profile aggregator. Never updated —
        every meaningful Command Center interaction inserts a row.
        """

        __tablename__ = "student_signals"
        __table_args__ = (
            db.Index("ix_signals_user_time", "user_id", "occurred_at"),
            db.Index("ix_signals_user_kind_time", "user_id", "kind", "occurred_at"),
        )

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
        kind = db.Column(db.String(48), nullable=False)
        subject_type = db.Column(db.String(32), nullable=True)
        subject_ref = db.Column(db.String(255), nullable=True)
        value_json = db.Column(db.Text, nullable=False, default="{}")
        occurred_at = db.Column(db.DateTime, nullable=False, default=_utcnow, index=True)

    return BriefingCache, HealthSnapshot, StudentSignal
