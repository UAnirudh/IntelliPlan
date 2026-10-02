"""ORM model for "Break it down" steps.

One additive table, ``assignment_steps``: the ordered checklist a student
generated (or edited) for one assignment. Assignments arrive from half a dozen
sources — Canvas, Classroom, StudentVue, manual tasks, scrapers — and the only
key every one of them shares, and that the rest of the app already uses for
per-assignment notes (``custom_descriptions``), is the title. Steps key on the
same thing, normalised, so a breakdown made on the dashboard is the same one
the priority page shows.

The table is also a small training set. ``estimate_minutes`` is what the step
was predicted to take; ``actual_minutes`` is what it took, from the Active
timer or the student's own answer. :func:`breakdown.personal_ratio` reads the
pairs, so finishing steps sharpens the next breakdown's estimates.

The module never imports the application or its ``db``. Call
:func:`register` from ``App.py`` after ``db = SQLAlchemy(app)`` exists.
"""

from __future__ import annotations

import re
from typing import Any

from time_utils import utcnow


def assignment_key(title: Any) -> str:
    """The owner-scoped identity of an assignment: its title, normalised.

    Case and runs of whitespace do not make a different assignment;
    "Lab 3 " and "lab 3" are the same lab.
    """
    return re.sub(r"\s+", " ", str(title or "")).strip().lower()[:512]


def register(db: Any) -> type:
    """Define the model against ``db``. Idempotent."""

    registry = getattr(db.Model, "registry", None)
    if registry is not None:
        for mapper in registry.mappers:
            cls = mapper.class_
            if getattr(cls, "__tablename__", "") == "assignment_steps":
                return cls

    class AssignmentStep(db.Model):
        __tablename__ = "assignment_steps"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
        guest_session_id = db.Column(db.String(64), nullable=True, index=True)

        assignment_key = db.Column(db.String(512), nullable=False, index=True)
        assignment_title = db.Column(db.String(512), nullable=False, default="")
        course = db.Column(db.String(256), default="")
        due_date = db.Column(db.String(32), default="")

        position = db.Column(db.Integer, nullable=False, default=0)
        text = db.Column(db.String(512), nullable=False)
        estimate_minutes = db.Column(db.Integer, nullable=False, default=15)
        #: The exact source sentence this step came from, when it came from
        #: the assignment's directions. Empty for template steps.
        evidence = db.Column(db.Text, default="")
        #: directions | ai | template | generic | student
        source = db.Column(db.String(16), default="template")

        done = db.Column(db.Boolean, default=False, nullable=False)
        done_at = db.Column(db.DateTime, nullable=True)
        actual_minutes = db.Column(db.Integer, nullable=True)
        #: Set on finished steps when the assignment is broken down again.
        #: They leave the checklist but stay in the table, because their
        #: estimate/actual pair is calibration data worth keeping.
        archived = db.Column(db.Boolean, default=False, nullable=False)

        created_at = db.Column(db.DateTime, default=utcnow)
        updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

        __table_args__ = (
            db.Index("ix_assignment_steps_owner_key", "user_id", "assignment_key"),
        )

        @property
        def task_ref(self) -> str:
            """``task_id`` an Active session carries while working this step."""
            return f"step:{self.id}"

        def to_dict(self) -> dict[str, Any]:
            return {
                "id": self.id,
                "position": self.position,
                "text": self.text,
                "minutes": self.estimate_minutes,
                "evidence": self.evidence or "",
                "source": self.source or "",
                "done": bool(self.done),
                "actual_minutes": self.actual_minutes,
                "assignment_title": self.assignment_title,
                "course": self.course or "",
                "due_date": self.due_date or "",
            }

    return AssignmentStep
