"""Grade Pulse snapshot table. Registered from ``App.py`` after ``db``
exists, like the notification outbox, so ``create_all()`` builds it."""

from __future__ import annotations

from typing import Any

from time_utils import utcnow


def existing_models(db: Any) -> dict[str, type]:
    registry = getattr(db.Model, "registry", None)
    out: dict[str, type] = {}
    if registry is not None:
        for mapper in registry.mappers:
            cls = mapper.class_
            out[getattr(cls, "__tablename__", "")] = cls
    return out


def register(db: Any) -> type:
    """Define ``GradePulseSnapshot``. Idempotent."""
    existing = existing_models(db)
    if "grade_pulse_snapshots" in existing:
        return existing["grade_pulse_snapshots"]

    class GradePulseSnapshot(db.Model):
        """What Grade Pulse saw the last time it looked at one source.

        One row per (student, scope). ``scope`` is "gradebook:<lms>",
        "grades:<lms>" or "assignments". The payload is hashes plus numbers;
        see intelliplan/services/grade_pulse.py for why there are no titles.
        """

        __tablename__ = "grade_pulse_snapshots"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
        scope = db.Column(db.String(64), nullable=False)
        data_json = db.Column(db.Text, nullable=False, default="{}")
        updated_at = db.Column(db.DateTime, default=utcnow, nullable=False)
        #: When the background pull last fetched this student's LMS. Kept
        #: apart from updated_at so a student who opens the app every ten
        #: minutes is not also pulled by the cron.
        pulled_at = db.Column(db.DateTime, nullable=True)

        __table_args__ = (
            db.UniqueConstraint("user_id", "scope", name="uq_grade_pulse_user_scope"),
        )

    return GradePulseSnapshot
