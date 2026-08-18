"""Recovering a plan the student did not follow.

Nobody follows a schedule exactly, and what a planner does about that is most
of whether it survives contact with a real week. The naive recovery — push
what slipped onto tomorrow — is worse than no recovery at all, because it
produces a day nobody can do, and then a second missed day, and then the
student stops opening the app.

The planner already knows how to re-solve a horizon from where the student
actually is (:func:`intelliplan.intelligence.planner.reschedule`). What was
missing was anyone telling it what actually happened. This module is that:

* :func:`build_reality` reads the saved schedule and its progress blob and
  works out what got done, what got abandoned part-way, and what was never
  touched.
* :func:`summarise_changes` diffs the old plan against the new one so the
  student can be told what moved and why, rather than finding their week
  quietly rearranged.

Purity
------
No Flask, no ORM, no clock. ``today`` is passed in. Everything here takes the
plain dicts the app already stores and returns plain data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from intelliplan.intelligence.planner import Reality

__all__ = [
    "MissedWork",
    "RecoveryFacts",
    "Change",
    "build_reality",
    "summarise_changes",
]


@dataclass(frozen=True, slots=True)
class MissedWork:
    """One block that was scheduled in the past and never checked off."""

    task_id: str
    title: str
    day: date
    minutes: int


@dataclass(frozen=True, slots=True)
class RecoveryFacts:
    """What the student did, in a shape the UI can render directly."""

    reality: Reality
    missed: tuple[MissedWork, ...] = ()
    completed_minutes: int = 0
    missed_minutes: int = 0
    #: Days in the past that held work and saw none of it done.
    lost_days: tuple[date, ...] = ()

    @property
    def needs_replan(self) -> bool:
        """Whether anything actually slipped.

        Re-solving a plan that is on track is not free: it moves blocks the
        student has already made peace with, for no gain. So the caller asks
        this first.
        """
        return bool(self.missed)


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _is_done(entry: Any) -> bool:
    """Progress entries have had two shapes over the app's life."""
    if entry is True:
        return True
    if isinstance(entry, Mapping):
        return bool(entry.get("done"))
    return False


def _block_key(block: Mapping[str, Any]) -> str:
    """The id progress is keyed by.

    ``block_id`` is what the Interactive View writes; ``id`` is what the
    planner emits before enrichment backfills the other. Reading both means a
    schedule saved by either generation still recovers.
    """
    return str(block.get("block_id") or block.get("id") or "")


def build_reality(
    schedule_data: Mapping[str, Any] | None,
    progress: Mapping[str, Any] | None,
    today: date,
    abandoned_minutes: Mapping[str, int] | None = None,
) -> RecoveryFacts:
    """Work out what happened, from the plan and the checkboxes.

    Only days strictly before ``today`` count as missed. Today is still
    happening — treating an unchecked block at 9am as a failure would be a
    planner scolding a student for not having done the future yet.

    ``abandoned_minutes`` is ``{task_id: minutes}`` from Active-study sittings
    the student started and did not finish. Those minutes are real work and
    have to be credited, or the plan re-schedules time the student already
    spent.
    """
    schedule = (schedule_data or {}).get("schedule") or []
    progress = progress or {}

    completed: dict[str, int] = {}
    missed: list[MissedWork] = []
    per_task_total: dict[str, int] = {}
    per_task_done: dict[str, int] = {}
    lost_days: list[date] = []

    for day in schedule:
        if not isinstance(day, Mapping):
            continue
        day_date = _as_date(day.get("date"))
        blocks = [b for b in (day.get("blocks") or []) if isinstance(b, Mapping)]
        work = [b for b in blocks if not b.get("is_break")]

        day_had_work = False
        day_saw_progress = False

        for block in work:
            task_id = str(block.get("task_id") or block.get("parent_title") or "")
            if not task_id:
                continue
            try:
                minutes = int(block.get("duration_minutes") or 0)
            except (TypeError, ValueError):
                minutes = 0

            per_task_total[task_id] = per_task_total.get(task_id, 0) + 1
            done = _is_done(progress.get(_block_key(block)))

            if done:
                completed[task_id] = completed.get(task_id, 0) + minutes
                per_task_done[task_id] = per_task_done.get(task_id, 0) + 1
                day_saw_progress = True
                continue

            # Not done. Only a *past* day makes that a miss.
            if day_date is not None and day_date < today:
                day_had_work = True
                missed.append(
                    MissedWork(
                        task_id=task_id,
                        title=str(
                            block.get("parent_title")
                            or block.get("assignment")
                            or task_id
                        ),
                        day=day_date,
                        minutes=minutes,
                    )
                )

        if day_had_work and not day_saw_progress and day_date is not None:
            lost_days.append(day_date)

    abandoned = {
        str(k): int(v)
        for k, v in (abandoned_minutes or {}).items()
        if str(k) and int(v or 0) > 0
    }

    # A task is finished only when every sitting the plan gave it is checked
    # off. Marking it finished on partial progress is how work disappears from
    # a plan while the assignment is still outstanding.
    finished = frozenset(
        task_id
        for task_id, total in per_task_total.items()
        if total > 0 and per_task_done.get(task_id, 0) >= total
    )

    reality = Reality(
        completed=completed,
        abandoned=abandoned,
        missed_task_ids=frozenset(m.task_id for m in missed),
        finished_task_ids=finished,
    )
    return RecoveryFacts(
        reality=reality,
        missed=tuple(missed),
        completed_minutes=sum(completed.values()),
        missed_minutes=sum(m.minutes for m in missed),
        lost_days=tuple(sorted(set(lost_days))),
    )


@dataclass(frozen=True, slots=True)
class Change:
    """One difference between the old plan and the new one."""

    title: str
    kind: str          # "moved" | "added" | "dropped"
    from_day: date | None = None
    to_day: date | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "kind": self.kind,
            "from": self.from_day.isoformat() if self.from_day else None,
            "to": self.to_day.isoformat() if self.to_day else None,
            "reason": self.reason,
        }


def _first_days(schedule_data: Mapping[str, Any] | None) -> dict[str, date]:
    """``{title: earliest day it appears}`` for one plan.

    Keyed on the assignment rather than the block, because block ids are
    regenerated on every plan and comparing them would report that the entire
    week changed.
    """
    out: dict[str, date] = {}
    for day in (schedule_data or {}).get("schedule") or []:
        if not isinstance(day, Mapping):
            continue
        day_date = _as_date(day.get("date"))
        if day_date is None:
            continue
        for block in day.get("blocks") or []:
            if not isinstance(block, Mapping) or block.get("is_break"):
                continue
            title = str(block.get("parent_title") or block.get("assignment") or "")
            if not title:
                continue
            if title not in out or day_date < out[title]:
                out[title] = day_date
    return out


def summarise_changes(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    facts: RecoveryFacts | None = None,
    limit: int = 8,
) -> list[Change]:
    """Explain the difference between two plans in the student's terms.

    A plan that silently rearranges itself is a plan the student stops
    trusting, so this exists to be shown, not logged. Ordered by how much the
    student needs to know: work that vanished first, then work that moved
    earlier (which usually means a deadline got closer), then the rest.
    """
    old_days = _first_days(before)
    new_days = _first_days(after)
    missed_titles = {m.title for m in (facts.missed if facts else ())}

    changes: list[Change] = []

    for title, was in old_days.items():
        now = new_days.get(title)
        if now is None:
            changes.append(
                Change(
                    title=title,
                    kind="dropped",
                    from_day=was,
                    reason="Finished, or it no longer fits before its deadline.",
                )
            )
        elif now != was:
            if title in missed_titles:
                reason = "You didn't get to this, so it was re-fitted around the rest of your week."
            elif now < was:
                reason = "Moved earlier to leave room later in the week."
            else:
                reason = "Moved later to keep the earlier days doable."
            changes.append(
                Change(title=title, kind="moved", from_day=was, to_day=now, reason=reason)
            )

    for title, now in new_days.items():
        if title not in old_days:
            changes.append(
                Change(
                    title=title,
                    kind="added",
                    to_day=now,
                    reason="New work, or work that finally fits.",
                )
            )

    order = {"dropped": 0, "moved": 1, "added": 2}
    changes.sort(
        key=lambda c: (
            order.get(c.kind, 3),
            0 if c.title in missed_titles else 1,
            c.to_day or c.from_day or date.max,
            c.title,
        )
    )
    return changes[: max(0, limit)]
