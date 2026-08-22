"""ORM models for the adaptive scheduler's audit trail.

Two additive tables:

* ``schedule_versions`` — every plan the student has been shown, why it
  changed, and how the winning candidate measured. Plans were previously
  overwritten in place, so "why does my week look different?" had no answer
  and neither did "which change broke this?".
* ``schedule_decisions`` — every Next-Best-Action recommendation and every
  override, with the reason codes behind it and whether the student took it.
  This is the training signal for the behavioural model: a recommendation
  nobody accepted is the most useful row in the table.

Privacy
-------
Neither table stores assignment titles, descriptions, or grades. They store
task ids, course names (which the student already sees on every screen), and
numbers. The rule is that this audit trail must never become a second, less
guarded copy of a student's academic record — the plan itself already lives
in ``saved_schedules`` under the same access controls.

Registration follows the same ``register(db)`` pattern as
``intelliplan.models.command_center``. Call from ``App.py`` after
``db = SQLAlchemy(app)`` is created.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from time_utils import utcnow


def _utcnow() -> datetime:
    return utcnow()


def register(db: Any) -> tuple[type, type]:
    """Define the two audit models against ``db``.

    Idempotent: reuses existing classes if already registered (handles
    gunicorn ``--max-requests`` reloads).
    """

    registry = getattr(db.Model, "registry", None)
    existing: dict[str, type] = {}
    if registry is not None:
        for mapper in registry.mappers:
            cls = mapper.class_
            existing[getattr(cls, "__tablename__", "")] = cls

    if {"schedule_versions", "schedule_decisions"} <= set(existing):
        return existing["schedule_versions"], existing["schedule_decisions"]

    class ScheduleVersion(db.Model):
        """One generation of a student's plan.

        ``version`` is per-user and monotonic, so "Schedule v14 → v15" is a
        thing a support conversation can actually refer to. It is assigned by
        the repository under the same transaction as the insert rather than
        being derived at read time, because two cron fires and a page load can
        race and two rows called v15 are worse than a gap in the numbering.
        """

        __tablename__ = "schedule_versions"
        __table_args__ = (
            db.UniqueConstraint("user_id", "version", name="uq_schedver_user_version"),
            db.Index("ix_schedver_user_created", "user_id", "created_at"),
        )

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(
            db.Integer, db.ForeignKey("users.id"), nullable=False, index=True,
        )
        version = db.Column(db.Integer, nullable=False)
        #: The ``saved_schedules`` row this version produced, when there is
        #: one. Nullable because a previewed candidate is a real version that
        #: was never saved.
        saved_schedule_id = db.Column(db.Integer, nullable=True, index=True)
        #: What caused this version: "generated", "session_missed",
        #: "override", "new_assignment", "availability_changed", "recovery".
        trigger = db.Column(db.String(48), nullable=False, default="generated")
        #: Which objective profile won — see ``counterfactual.PROFILES``.
        objective_key = db.Column(db.String(32), nullable=False, default="balanced")
        candidate_count = db.Column(db.Integer, nullable=False, default=1)
        selected_score = db.Column(db.Float, nullable=False, default=0.0)
        deadline_risk = db.Column(db.Float, nullable=False, default=0.0)
        stress = db.Column(db.Float, nullable=False, default=0.0)
        completion_odds = db.Column(db.Float, nullable=False, default=0.0)
        feasible = db.Column(db.Boolean, nullable=False, default=True)
        #: Hard-violation keys from the feasibility check, comma-separated.
        #: Keys only — the human-readable detail contains assignment titles.
        violation_keys = db.Column(db.String(255), nullable=False, default="")
        optimisation_ms = db.Column(db.Integer, nullable=False, default=0)
        scheduled_minutes = db.Column(db.Integer, nullable=False, default=0)
        deferred_minutes = db.Column(db.Integer, nullable=False, default=0)
        #: What moved, as ``[{task_id, from, to, kind}]``. Task ids, not titles.
        changes_json = db.Column(db.Text, nullable=False, default="[]")
        created_at = db.Column(
            db.DateTime, nullable=False, default=_utcnow, index=True,
        )

    class ScheduleDecision(db.Model):
        """One recommendation or override, and what the student did with it.

        ``accepted`` is deliberately tri-state via nullability: ``None`` means
        "shown, not yet answered", which is different from "rejected". Folding
        the two together would teach the model that every recommendation a
        student has not got round to is one they disliked.
        """

        __tablename__ = "schedule_decisions"
        __table_args__ = (
            db.Index("ix_scheddec_user_time", "user_id", "created_at"),
            db.Index("ix_scheddec_user_type_time", "user_id", "decision_type", "created_at"),
        )

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(
            db.Integer, db.ForeignKey("users.id"), nullable=False, index=True,
        )
        #: "next_best_action" | "override" | "candidate_selected"
        decision_type = db.Column(db.String(32), nullable=False)
        #: For NBA rows, the ``ActionKind``. For overrides, "move"/"skip"/"shorten".
        action_kind = db.Column(db.String(32), nullable=False, default="")
        task_id = db.Column(db.String(128), nullable=False, default="")
        #: Hash of (title, course) — see ``domain.student.identity_key``.
        #: Lets a decision recorded against one ingest path's id be honoured
        #: when the same work reappears under another's, without storing a
        #: readable assignment title anywhere in this table.
        identity_key = db.Column(db.String(64), nullable=False, default="", index=True)
        course = db.Column(db.String(160), nullable=False, default="")
        score = db.Column(db.Float, nullable=False, default=0.0)
        confidence = db.Column(db.Float, nullable=False, default=0.0)
        completion_probability = db.Column(db.Float, nullable=False, default=0.0)
        #: Stable reason-code keys, comma-separated. Not sentences: the
        #: wording lives in one table in ``nba.REASON_LABELS`` and copying it
        #: into the database would freeze today's phrasing forever.
        reason_codes = db.Column(db.String(255), nullable=False, default="")
        #: Rank in the list the student was shown. 0 is the headline pick.
        rank = db.Column(db.Integer, nullable=False, default=0)
        accepted = db.Column(db.Boolean, nullable=True)
        schedule_version_id = db.Column(db.Integer, nullable=True, index=True)
        created_at = db.Column(
            db.DateTime, nullable=False, default=_utcnow, index=True,
        )
        resolved_at = db.Column(db.DateTime, nullable=True)

    return ScheduleVersion, ScheduleDecision
