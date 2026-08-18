"""ORM models for the Active study experience.

Two additive tables:

* ``active_sessions`` — one row per sitting the student actually starts.
  This is the table the estimation model learns from, so it records both
  what was *planned* and what *happened*, including the case that teaches
  the most: the student quit early.
* ``active_focus_samples`` — coarse, aggregated attention readings from the
  optional camera check-in. Deliberately not per-frame and deliberately not
  biometric: one row per minute-ish bucket holding counts, never images,
  never landmarks, never anything that identifies a person.

Privacy contract for ``active_focus_samples``
---------------------------------------------
The camera pipeline runs entirely in the browser. What crosses the network
is a small integer summary — how many samples in the bucket looked present,
looked away, or found nobody — plus a mean confidence. No frames, no
embeddings, no face geometry. A row cannot be inverted into an image and
cannot be used to recognise anyone. Rows are per-session and are deleted
with the session.

The module never imports the application or its ``db``. Call
:func:`register` from ``App.py`` after ``db = SQLAlchemy(app)`` exists.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from time_utils import utcnow


def _utcnow() -> datetime:
    return utcnow()


#: Terminal states a session can end in. ``abandoned`` is not a failure
#: state to hide — it is the single most informative signal the scheduler
#: gets, because it measures real focus length rather than intended length.
SESSION_STATES = ("running", "paused", "completed", "abandoned", "expired")


def register(db: Any) -> tuple[type, type]:
    """Define the Active-study models against ``db``. Idempotent."""

    registry = getattr(db.Model, "registry", None)
    existing: dict[str, type] = {}
    if registry is not None:
        for mapper in registry.mappers:
            cls = mapper.class_
            existing[getattr(cls, "__tablename__", "")] = cls

    if {"active_sessions", "active_focus_samples"} <= set(existing):
        return existing["active_sessions"], existing["active_focus_samples"]

    class ActiveSession(db.Model):
        """One sitting: what was planned, what happened, what it taught us."""

        __tablename__ = "active_sessions"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
        guest_session_id = db.Column(db.String(64), nullable=True, index=True)

        # ── What was planned ──────────────────────────────────────────
        #: Stable id of the scheduled block this session executes, when it
        #: came from a plan. Free-form sessions leave it null.
        block_id = db.Column(db.String(64), nullable=True, index=True)
        task_id = db.Column(db.String(128), nullable=True, index=True)
        title = db.Column(db.String(512), nullable=False, default="Study session")
        course = db.Column(db.String(256), default="")
        kind = db.Column(db.String(32), default="homework")
        difficulty = db.Column(db.String(16), default="medium")
        planned_minutes = db.Column(db.Integer, default=0)
        due_date = db.Column(db.Date, nullable=True)

        # ── What happened ─────────────────────────────────────────────
        state = db.Column(db.String(16), default="running", index=True)
        started_at = db.Column(db.DateTime, default=_utcnow, index=True)
        ended_at = db.Column(db.DateTime, nullable=True)
        #: Wall time spent with the timer running, excluding pauses. This is
        #: the number the estimator uses; elapsed clock time is not effort.
        active_seconds = db.Column(db.Integer, default=0)
        paused_seconds = db.Column(db.Integer, default=0)
        pause_count = db.Column(db.Integer, default=0)
        #: Student's own answer to "did you finish?" — the ground truth the
        #: timer cannot observe.
        completed_work = db.Column(db.Boolean, default=False)
        #: 0..100 self-reported progress for work spanning several sittings.
        progress_percent = db.Column(db.Integer, default=0)
        #: Optional 1..5 self-report. Feeds difficulty calibration.
        perceived_difficulty = db.Column(db.Integer, nullable=True)
        notes = db.Column(db.Text, nullable=True)

        # ── Focus summary (only when the camera check-in was enabled) ──
        focus_enabled = db.Column(db.Boolean, default=False)
        focus_samples = db.Column(db.Integer, default=0)
        focus_present_samples = db.Column(db.Integer, default=0)
        focus_away_samples = db.Column(db.Integer, default=0)
        focus_absent_samples = db.Column(db.Integer, default=0)
        #: Longest uninterrupted stretch that read as focused, in seconds.
        longest_focus_streak = db.Column(db.Integer, default=0)
        #: Number of times the system decided the student had genuinely
        #: drifted — after thresholding, not per-frame.
        distraction_events = db.Column(db.Integer, default=0)
        #: Sparks this session gave up to focus enforcement in "stakes"
        #: mode. Scoped to the session on purpose: enforcement can cost a
        #: student what they earned in this sitting and nothing more, so a
        #: bad afternoon can never reach a balance built up over weeks.
        sparks_forfeited = db.Column(db.Integer, default=0)

        created_at = db.Column(db.DateTime, default=_utcnow)
        updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

        __table_args__ = (
            db.Index("ix_active_sessions_user_started", "user_id", "started_at"),
        )

        # ── Derived ───────────────────────────────────────────────────

        @property
        def active_minutes(self) -> int:
            return int(round((self.active_seconds or 0) / 60.0))

        @property
        def is_terminal(self) -> bool:
            return self.state in ("completed", "abandoned", "expired")

        @property
        def focus_ratio(self) -> float | None:
            """Share of camera samples that read as present and engaged.

            ``None`` when the camera was off or the sample count is too low
            to mean anything — an honest absence beats a fabricated 100%.
            """
            total = self.focus_samples or 0
            if not self.focus_enabled or total < 10:
                return None
            return round((self.focus_present_samples or 0) / total, 3)

        def to_observation(self) -> dict[str, Any]:
            """The shape ``intelliplan.intelligence.estimation`` consumes."""
            return {
                "estimated": int(self.planned_minutes or 0),
                "actual": self.active_minutes,
                "course": self.course or "",
                "kind": self.kind or "",
                "at": self.ended_at or self.started_at,
                "completed": bool(self.completed_work),
            }

        def to_dict(self) -> dict[str, Any]:
            return {
                "id": self.id,
                "block_id": self.block_id,
                "task_id": self.task_id,
                "title": self.title,
                "course": self.course,
                "kind": self.kind,
                "difficulty": self.difficulty,
                "planned_minutes": self.planned_minutes,
                "due_date": self.due_date.isoformat() if self.due_date else None,
                "state": self.state,
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "ended_at": self.ended_at.isoformat() if self.ended_at else None,
                "active_seconds": self.active_seconds or 0,
                "paused_seconds": self.paused_seconds or 0,
                "pause_count": self.pause_count or 0,
                "completed_work": bool(self.completed_work),
                "progress_percent": self.progress_percent or 0,
                "perceived_difficulty": self.perceived_difficulty,
                "focus": {
                    "enabled": bool(self.focus_enabled),
                    "samples": self.focus_samples or 0,
                    "ratio": self.focus_ratio,
                    "distraction_events": self.distraction_events or 0,
                    "longest_streak_seconds": self.longest_focus_streak or 0,
                },
            }

    class ActiveFocusSample(db.Model):
        """One aggregated attention bucket. Never an image, never a face.

        See the module docstring for the privacy contract. If you are about
        to add a column here, ask whether it could identify a person or
        reconstruct a frame; if the answer is anything but a flat no, it
        does not belong in this table.
        """

        __tablename__ = "active_focus_samples"

        id = db.Column(db.Integer, primary_key=True)
        session_id = db.Column(
            db.Integer,
            db.ForeignKey("active_sessions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
        #: Seconds since the session started — relative, so the row carries
        #: no wall-clock information about the student's life.
        offset_seconds = db.Column(db.Integer, default=0)
        present = db.Column(db.Integer, default=0)
        away = db.Column(db.Integer, default=0)
        absent = db.Column(db.Integer, default=0)
        #: Mean detector confidence across the bucket, 0..1.
        confidence = db.Column(db.Float, default=0.0)

        __table_args__ = (
            db.Index("ix_focus_samples_session_offset", "session_id", "offset_seconds"),
        )

    return ActiveSession, ActiveFocusSample
