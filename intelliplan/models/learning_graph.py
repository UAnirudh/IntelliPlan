"""ORM models for the Learning Intelligence Layer.

Three tables:

* ``student_profiles`` — per-user learning profile derived from all data
  sources. Recomputed periodically and on dashboard load.
* ``concept_mastery`` — per-concept mastery tracking. Aggregated from
  StudyMastery, tutor interactions, and grade signals.
* ``learning_events`` — append-only event log for every learning-relevant
  action. Foundation for profile recomputation and prediction engines.

Registration follows the same ``register(db)`` pattern as
``intelliplan.models.command_center``. Call from ``App.py`` after
``db = SQLAlchemy(app)`` is created.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _utcnow() -> datetime:
    return datetime.utcnow()


def register(db: Any) -> tuple[type, type, type]:
    """Define the three Learning Graph models against ``db``.

    Idempotent: reuses existing classes if already registered (handles
    gunicorn ``--max-requests`` reloads).
    """

    registry = getattr(db.Model, "registry", None)
    existing: dict[str, type] = {}
    if registry is not None:
        for mapper in registry.mappers:
            cls = mapper.class_
            existing[getattr(cls, "__tablename__", "")] = cls

    if {"student_profiles", "concept_mastery", "learning_events"} <= set(existing):
        return (
            existing["student_profiles"],
            existing["concept_mastery"],
            existing["learning_events"],
        )

    class StudentProfile(db.Model):
        """Per-user learning profile, recomputed from all data sources."""

        __tablename__ = "student_profiles"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(
            db.Integer,
            db.ForeignKey("users.id"),
            unique=True,
            nullable=False,
            index=True,
        )
        learning_pace = db.Column(db.String(16), nullable=False, default="unknown")
        strongest_subjects_json = db.Column(db.Text, nullable=False, default="[]")
        weakest_subjects_json = db.Column(db.Text, nullable=False, default="[]")
        preferred_methods_json = db.Column(db.Text, nullable=False, default="[]")
        retention_score = db.Column(db.Float, nullable=False, default=0.0)
        avg_estimate_ratio = db.Column(db.Float, nullable=False, default=1.0)
        engagement_score = db.Column(db.Float, nullable=False, default=0.0)
        gpa_snapshot = db.Column(db.Float, nullable=True)
        recomputed_at = db.Column(db.DateTime, nullable=True)
        created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    class ConceptMastery(db.Model):
        """Per-concept mastery tracking across subjects and topics."""

        __tablename__ = "concept_mastery"
        __table_args__ = (
            db.UniqueConstraint(
                "user_id", "subject", "topic", "concept",
                name="uq_concept_user_subj_topic_conc",
            ),
            db.Index("ix_concept_user_subject", "user_id", "subject"),
            db.Index("ix_concept_user_mastery", "user_id", "mastery_score"),
            db.Index("ix_concept_user_reviewed", "user_id", "last_reviewed"),
        )

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(
            db.Integer, db.ForeignKey("users.id"), nullable=False, index=True,
        )
        subject = db.Column(db.String(256), nullable=False)
        topic = db.Column(db.String(256), nullable=False)
        concept = db.Column(db.String(256), nullable=False)
        mastery_score = db.Column(db.Float, nullable=False, default=0.0)
        confidence_score = db.Column(db.Float, nullable=False, default=0.0)
        times_seen = db.Column(db.Integer, nullable=False, default=0)
        times_correct = db.Column(db.Integer, nullable=False, default=0)
        times_incorrect = db.Column(db.Integer, nullable=False, default=0)
        last_reviewed = db.Column(db.DateTime, nullable=True)
        decay_rate = db.Column(db.Float, nullable=False, default=0.05)
        created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
        updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    class LearningEvent(db.Model):
        """Append-only event log for learning-relevant actions."""

        __tablename__ = "learning_events"
        __table_args__ = (
            db.Index("ix_levent_user_time", "user_id", "occurred_at"),
            db.Index("ix_levent_user_type_time", "user_id", "event_type", "occurred_at"),
        )

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(
            db.Integer, db.ForeignKey("users.id"), nullable=False, index=True,
        )
        event_type = db.Column(db.String(48), nullable=False)
        source = db.Column(db.String(32), nullable=False)
        subject = db.Column(db.String(256), nullable=True)
        reference_id = db.Column(db.String(255), nullable=True)
        value_json = db.Column(db.Text, nullable=False, default="{}")
        occurred_at = db.Column(
            db.DateTime, nullable=False, default=_utcnow, index=True,
        )

    return StudentProfile, ConceptMastery, LearningEvent
