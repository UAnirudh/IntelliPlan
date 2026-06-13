"""Canonical Assignment domain type.

Eight upstream sources (StudentVue, Canvas, Schoology, Google Classroom,
Blackboard, Moodle, Notion, manual + CSV) currently land in IntelliPlan as
untyped ``dict`` objects with subtly different keys. Every helper in
``App.py`` re-parses those dicts and re-derives ``due_date`` /
``points_possible`` / ``course``.

This module defines a single canonical shape — :class:`Assignment` — that
the Command Center reasons over. Conversion from upstream dicts lives in
the repository layer (``intelliplan.repositories.assignments``), not here.

Design notes
------------
* Frozen dataclass. Immutable by contract — keeps the priority engine
  trivially memoisable and makes the future memory-diff layer safe.
* No ORM, no Flask, no I/O. Pure data.
* ``due_date`` is a ``datetime.date``, never a string. Repositories parse.
* ``est_minutes`` is an estimate; calibration belongs in the intelligence
  layer (multiplied by the student's ``avg_estimate_ratio`` when known).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Literal


class AssignmentStatus(str, Enum):
    """Lifecycle of an assignment as seen by IntelliPlan."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DISMISSED = "dismissed"


class AssignmentKind(str, Enum):
    """Coarse type used by the priority engine's importance component.

    Order matters for the importance weighting in
    :mod:`intelliplan.intelligence.priority`. Detection from a free-text
    title happens at the repository boundary, never inside the engine.
    """

    EXAM = "exam"          # exam, final, midterm
    PROJECT = "project"    # project, essay, presentation
    TEST = "test"          # test, quiz
    LAB = "lab"            # lab report, lab assignment
    HOMEWORK = "homework"  # default for daily work
    CLASSWORK = "classwork"
    OTHER = "other"


# Sources we currently know how to ingest. Used for telemetry and for
# making per-source UI hints (e.g. "from Canvas") cheap.
AssignmentSource = Literal[
    "manual",
    "imported_grade",
    "studentvue",
    "canvas",
    "schoology",
    "google_classroom",
    "blackboard",
    "moodle",
    "notion",
    "csv",
]


@dataclass(frozen=True, slots=True)
class Assignment:
    """One unit of work, unified across upstream sources.

    The ``id`` is a stable string suitable for use as a DOM/React key. It
    is the natural identifier from the upstream source when available,
    otherwise a hash composed by the repository.
    """

    id: str
    title: str
    course: str
    due_date: date | None
    kind: AssignmentKind
    status: AssignmentStatus
    points_possible: float
    est_minutes: int
    source: AssignmentSource
    source_ref: str = ""
    # ``course_max_points`` is the total points across all graded items in
    # the course. When known, it lets the priority engine compute a real
    # grade-impact percentage instead of falling back to brackets.
    course_max_points: float | None = None
    # Free-form metadata the repository may attach (teacher, category,
    # weight). Intentionally opaque — never read by the engine.
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def is_overdue(self) -> bool:
        """``True`` when the assignment has a due date in the past and is
        not yet completed or dismissed."""
        if self.due_date is None:
            return False
        if self.status in (AssignmentStatus.COMPLETED, AssignmentStatus.DISMISSED):
            return False
        return self.due_date < date.today()

    def days_until_due(self, today: date) -> int | None:
        """Days from ``today`` until ``due_date`` (negative when overdue).

        ``today`` is passed explicitly so callers in test code stay
        deterministic and so the function never reads the wall clock.
        """
        if self.due_date is None:
            return None
        return (self.due_date - today).days
