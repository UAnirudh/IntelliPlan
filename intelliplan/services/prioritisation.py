"""One priority engine for the whole product.

IntelliPlan has had two answers to "how important is this?" living side by
side. The Command Center used
:mod:`intelliplan.intelligence.priority` — five capped components (urgency 40,
importance 25, grade impact 20, effort 10, dependency 5) with a reason chip
per component. The scheduler used a lookup in ``App.py``::

    {"high": 80, "medium": 50, "low": 30}  + a bump for big point values

Same product, same assignments, two different orderings, and the better engine
was wired only to the read-only view. So the thing that *acts* on priority —
which work gets pulled earlier, and which work gets dropped when the week is
over capacity — was running on the weaker of the two.

This module is the adapter that lets the scheduler use the real one. It
converts the loose assignment dicts the ingest paths produce into the typed
:class:`~intelliplan.domain.assignment.Assignment` the engine expects, scores
them, and hands back both the number and the reasons behind it.

The student's own label still counts
------------------------------------
Students can mark an assignment High, Medium, or Low. Throwing that away in
favour of a computed score would be a planner overruling the person using it,
so the label nudges the engine's score rather than replacing or being replaced
by it.

Purity
------
No Flask, no ORM, no clock. ``today`` is passed in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Sequence

from intelliplan.domain.assignment import (
    Assignment,
    AssignmentKind,
    AssignmentStatus,
)
from intelliplan.intelligence import priority as priority_engine
from intelliplan.repositories.assignments import classify_kind, parse_due_date

__all__ = [
    "ScoredRow",
    "score_rows",
    "assignment_from_row",
    "LABEL_NUDGE",
]

#: How far the student's own High/Medium/Low label moves the computed score.
#: Deliberately small: enough that marking something High visibly pulls it
#: earlier, not enough to override an overdue exam sitting at 95.
LABEL_NUDGE: Mapping[str, int] = {"high": 12, "medium": 0, "low": -12}

_KIND_ALIASES: Mapping[str, AssignmentKind] = {
    "exam": AssignmentKind.EXAM,
    "final": AssignmentKind.EXAM,
    "midterm": AssignmentKind.EXAM,
    "test": AssignmentKind.TEST,
    "quiz": AssignmentKind.TEST,
    "project": AssignmentKind.PROJECT,
    "essay": AssignmentKind.PROJECT,
    "paper": AssignmentKind.PROJECT,
    "lab": AssignmentKind.LAB,
    "homework": AssignmentKind.HOMEWORK,
    "classwork": AssignmentKind.CLASSWORK,
    "other": AssignmentKind.OTHER,
}


@dataclass(frozen=True, slots=True)
class ScoredRow:
    """A scored assignment, ready to attach to a planner row."""

    key: str
    score: int
    tier: str
    #: Component reasons, most significant first, for the "why" panel.
    reasons: tuple[str, ...] = ()


def assignment_from_row(row: Mapping[str, Any], today: date) -> Assignment:
    """Build a typed :class:`Assignment` from a loose ingest dict.

    Kind resolution prefers what the source *said* over what the title looks
    like. A Canvas assignment group or a StudentVue category is a teacher's
    own classification, and guessing from the title is what we do when we have
    nothing better.
    """
    title = str(row.get("title") or row.get("name") or "Untitled task").strip()
    declared = str(row.get("kind") or row.get("type") or row.get("category") or "").strip().lower()
    kind = _KIND_ALIASES.get(declared) or classify_kind(title)

    try:
        points = float(row.get("points_possible") or 0.0)
    except (TypeError, ValueError):
        points = 0.0
    try:
        est = int(row.get("est_minutes") or row.get("estimated_time") or 0)
    except (TypeError, ValueError):
        est = 0

    due = parse_due_date(row.get("due_date") or row.get("due"))
    done = bool(row.get("done") or row.get("completed"))
    started = bool(row.get("done_minutes") or row.get("in_progress"))
    status = (
        AssignmentStatus.COMPLETED if done
        else AssignmentStatus.IN_PROGRESS if started
        else AssignmentStatus.NOT_STARTED
    )

    return Assignment(
        id=str(row.get("id") or row.get("source_ref") or title),
        title=title,
        course=str(row.get("course") or "").strip(),
        due_date=due,
        kind=kind,
        status=status,
        points_possible=points,
        est_minutes=est,
        source=str(row.get("source") or "manual"),  # type: ignore[arg-type]
        source_ref=str(row.get("source_ref") or ""),
        course_max_points=_optional_float(row.get("course_max_points")),
    )


def score_rows(
    rows: Sequence[Mapping[str, Any]],
    today: date,
    course_max_points: Mapping[str, float] | None = None,
) -> dict[str, ScoredRow]:
    """Score every row, keyed by the same id the caller uses for its tasks.

    Scoring the whole list at once is not an optimisation — the engine's
    dependency component needs to know which exams are coming up in each
    course, which is a property of the set rather than of any one row.
    """
    if not rows:
        return {}

    # The row is carried through with its assignment rather than zipped back
    # on afterwards: any skipped row would shift every later pairing by one and
    # silently give assignments each other's priority labels.
    pairs: list[tuple[str, Assignment, Mapping[str, Any]]] = []
    for row in rows:
        try:
            assignment = assignment_from_row(row, today)
        except Exception:
            # A row we cannot type is a row that keeps its caller's default,
            # not a reason to fail scoring for everything else.
            continue
        key = str(row.get("id") or row.get("source_ref") or row.get("title") or "")
        if key:
            pairs.append((key, assignment, row))

    ctx = priority_engine.PriorityContext.from_assignments(
        today,
        [a for _, a, _ in pairs],
        dict(course_max_points or {}),
    )

    out: dict[str, ScoredRow] = {}
    for key, assignment, row in pairs:
        score = priority_engine.compute(assignment, ctx)
        label = str(row.get("priority") or "").strip().lower()
        nudge = LABEL_NUDGE.get(label, 0)
        final = max(0, min(100, score.score + nudge))
        reasons = tuple(
            chip.reason for chip in score.rationale if getattr(chip, "reason", "")
        )
        if nudge > 0:
            reasons = reasons + ("You marked this high priority.",)
        elif nudge < 0:
            reasons = reasons + ("You marked this low priority.",)
        out[key] = ScoredRow(
            key=key, score=final, tier=str(score.tier), reasons=reasons
        )
    return out


def _optional_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None
