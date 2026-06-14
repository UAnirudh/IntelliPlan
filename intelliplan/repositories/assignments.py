"""AssignmentRepository — unify upstream assignment shapes.

Sources combined into one canonical stream:
- ``ManualTask`` (DB) — tasks the student created themselves
- LMS connectors (Canvas, StudentVue, Schoology, Classroom, Blackboard,
  Moodle) — pulled via an injected fetcher so the repo stays decoupled
  from Flask request context.

The intelligence engine never sees a dict. The repository takes its
dependencies (model class, DB session, optional LMS fetcher) in the
constructor so unit tests can substitute fakes without spinning up Flask.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Protocol

from intelliplan.domain.assignment import (
    Assignment,
    AssignmentKind,
    AssignmentStatus,
)


_BIG_TITLE_RE = re.compile(
    r"\b(test|exam|midterm|final|project|essay|presentation|lab\s*report)\b",
    re.IGNORECASE,
)
_KIND_PATTERNS: tuple[tuple[re.Pattern[str], AssignmentKind], ...] = (
    (re.compile(r"\b(final|midterm|exam)\b", re.I), AssignmentKind.EXAM),
    (re.compile(r"\b(project|essay|presentation|paper)\b", re.I), AssignmentKind.PROJECT),
    (re.compile(r"\b(test|quiz)\b", re.I), AssignmentKind.TEST),
    (re.compile(r"\blab(\s*report)?\b", re.I), AssignmentKind.LAB),
    (re.compile(r"\b(homework|hw|practice|worksheet|packet)\b", re.I), AssignmentKind.HOMEWORK),
    (re.compile(r"\b(classwork|cw|class\s*work)\b", re.I), AssignmentKind.CLASSWORK),
)


def classify_kind(title: str) -> AssignmentKind:
    """Coarse-classify an assignment by title.

    Pure function — exposed so the future LMS-merging code can reuse the
    same classification without going through the repository.
    """
    if not title:
        return AssignmentKind.OTHER
    for pattern, kind in _KIND_PATTERNS:
        if pattern.search(title):
            return kind
    return AssignmentKind.OTHER


def parse_due_date(raw: Any) -> date | None:
    """Parse a ManualTask.due_date (string) into a :class:`date`.

    Accepts ``YYYY-MM-DD`` (the storage format), ISO 8601 with time, and
    ``date`` / ``datetime`` instances. Returns ``None`` for blanks or
    unrecognised input — the engine treats no-due-date as low-urgency.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    text = str(raw).strip()
    if not text:
        return None
    # Strip a trailing 'Z' so ``fromisoformat`` accepts UTC strings.
    if text.endswith("Z"):
        text = text[:-1]
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def stable_id(prefix: str, *parts: Any) -> str:
    """Produce a short, stable string id from a prefix + arbitrary parts."""
    digest = hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:12]}"


class _ManualTaskLike(Protocol):
    """Structural type for the ManualTask ORM row we read."""

    id: int
    title: str
    course: str
    due_date: Any
    estimated_time: Any
    done: bool


@dataclass(frozen=True)
class _RepoConfig:
    manual_task_model: type
    session: Any  # SQLAlchemy session
    lms_fetcher: Callable[[int], list[dict]] | None = None


class AssignmentRepository:
    """Read-only repository returning canonical :class:`Assignment` objects.

    Instantiated once per request (cheap — holds references only).
    ``lms_fetcher`` is an optional callable that takes a ``user_id`` and
    returns dicts in the same shape as ``/tasks/unified``. When provided,
    the repository merges those records into the assignment stream.
    """

    def __init__(
        self,
        manual_task_model: type,
        session: Any,
        lms_fetcher: Callable[[int], list[dict]] | None = None,
    ) -> None:
        self._cfg = _RepoConfig(
            manual_task_model=manual_task_model,
            session=session,
            lms_fetcher=lms_fetcher,
        )

    # ── public surface ───────────────────────────────────────────────

    def for_user(self, user_id: int, today: date) -> tuple[Assignment, ...]:
        """Return every active assignment for the user, ordered by due date.

        Completed and dismissed manual tasks are excluded — the Command
        Center surfaces work that still has to happen.
        """
        manual_rows = self._fetch_manual_tasks(user_id)
        manual = tuple(self._manual_to_assignment(r) for r in manual_rows)

        lms: tuple[Assignment, ...] = ()
        if self._cfg.lms_fetcher is not None:
            try:
                raw = self._cfg.lms_fetcher(user_id) or []
                lms = tuple(self._dict_to_assignment(r) for r in raw if isinstance(r, dict))
            except Exception:
                lms = ()

        merged = _dedupe_assignments(manual + lms)
        return tuple(sorted(merged, key=_assignment_sort_key))

    # ── internals ────────────────────────────────────────────────────

    def _fetch_manual_tasks(self, user_id: int) -> Iterable[_ManualTaskLike]:
        Model = self._cfg.manual_task_model
        query = (
            self._cfg.session.query(Model)
            .filter(Model.user_id == user_id)
            .filter(Model.done == False)  # noqa: E712 — SQLAlchemy idiom
        )
        return query.all()

    def _manual_to_assignment(self, row: _ManualTaskLike) -> Assignment:
        title = (row.title or "").strip() or "Untitled task"
        course = (getattr(row, "course", "") or "Personal").strip() or "Personal"
        due = parse_due_date(getattr(row, "due_date", None))
        kind = classify_kind(title)
        try:
            est_minutes = int(getattr(row, "estimated_time", 60) or 60)
        except (TypeError, ValueError):
            est_minutes = 60
        return Assignment(
            id=stable_id("manual", row.id),
            title=title,
            course=course,
            due_date=due,
            kind=kind,
            status=AssignmentStatus.NOT_STARTED,
            points_possible=0.0,
            est_minutes=est_minutes,
            source="manual",
            source_ref=str(row.id),
        )

    def _dict_to_assignment(self, raw: dict) -> Assignment:
        """Map an LMS task dict (Canvas/SV/Schoology/Classroom/etc.) to Assignment."""
        title = (raw.get("title") or "").strip() or "Untitled task"
        course = (raw.get("course") or "Class").strip() or "Class"
        due = parse_due_date(raw.get("due_date"))
        kind = classify_kind(title)
        try:
            est_minutes = int(raw.get("estimated_time") or 60)
        except (TypeError, ValueError):
            est_minutes = 60
        try:
            points = float(raw.get("points_possible") or 0.0)
        except (TypeError, ValueError):
            points = 0.0
        source = raw.get("source") or "lms"
        ref = str(raw.get("id") or raw.get("external_id") or raw.get("title") or "")
        return Assignment(
            id=stable_id(source, ref, course, title),
            title=title,
            course=course,
            due_date=due,
            kind=kind,
            status=AssignmentStatus.NOT_STARTED,
            points_possible=points,
            est_minutes=est_minutes,
            source=source,
            source_ref=ref,
        )


def _dedupe_assignments(items: tuple[Assignment, ...]) -> tuple[Assignment, ...]:
    """Drop duplicates by (lower(title), lower(course), due_date).

    A user might add the same task manually that also came through Canvas.
    Manual wins because it carries the user's own estimate.
    """
    seen: dict[tuple, Assignment] = {}
    for a in items:
        key = (a.title.lower().strip(), a.course.lower().strip(), a.due_date)
        existing = seen.get(key)
        if existing is None:
            seen[key] = a
        else:
            if existing.source != "manual" and a.source == "manual":
                seen[key] = a
    return tuple(seen.values())


def _assignment_sort_key(a: Assignment) -> tuple[int, date, str]:
    # No due date sorts last; otherwise chronological; title breaks ties.
    if a.due_date is None:
        return (1, date.max, a.title)
    return (0, a.due_date, a.title)
