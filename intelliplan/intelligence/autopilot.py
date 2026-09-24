"""Autopilot — the plan re-plans itself, and says so.

Everything in :mod:`intelliplan.intelligence.rescheduling` waits for the
student to say what happened. Most weeks nobody says anything: a session
just doesn't happen, a teacher posts a new assignment, a deadline quietly
becomes unreachable. A planner that only reacts when asked is a planner that
is wrong by Wednesday.

Autopilot watches for three facts and acts on them without being asked:

* **Missed work** — a block on a past day that was never ticked off. It
  gets a new day. (Leaving it on a day that has passed is the plan lying.)
* **New work** — an assignment the LMS knows about that the plan does not.
  It is placed into the existing week without reshuffling the rest.
* **A deadline at risk** that rebalancing would measurably protect.

It acts through the same machinery as a student's own intent — minimal
change first, rebalancing only when simulation says it is safer — so it
never makes a plan worse to look busy. Three rules keep autonomy from
becoming something done *to* the student:

1. **Every autonomous change is explained and undoable in one tap.** The
   previous plan is kept until the student changes something themselves.
2. **An undo is respected.** Autopilot stands down for the rest of the day
   and records the undo as a signal about how much this student wants moved.
3. **It can be switched off**, per plan, and then does nothing at all.

Purity: no Flask, no ORM, no clock. The service composes it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from intelliplan.intelligence.planner import PlannerTask
from intelliplan.intelligence.risk import RiskReport, group_key

__all__ = [
    "AutopilotState",
    "Trigger",
    "detect_new_work",
    "triggers_for",
    "should_run",
    "explain",
    "MAX_NEW_TASKS",
]

#: New assignments absorbed in one run. A first sync can surface forty rows;
#: pouring them all into a week at once is a regeneration, not a repair.
MAX_NEW_TASKS = 15
#: Only a missed session or new work may re-trigger inside this window; a
#: risk-only rebalance waits, so the plan is not nudged on every page load.
RISK_COOLDOWN = timedelta(hours=6)
#: How much history is kept on the plan.
LOG_LIMIT = 12
#: Assignments further out than this are left for a later run.
NEW_WORK_HORIZON_DAYS = 14

_DONE_STATUSES = frozenset({"submitted", "graded", "completed", "complete", "done", "excused"})


@dataclass(frozen=True)
class AutopilotState:
    """What lives on the plan under ``schedule_data["autopilot"]``."""

    enabled: bool = True
    last_run: datetime | None = None
    suppressed_until: date | None = None
    log: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_json(cls, raw: Any) -> "AutopilotState":
        if not isinstance(raw, Mapping):
            return cls()
        last = None
        try:
            if raw.get("last_run"):
                last = datetime.fromisoformat(str(raw["last_run"]))
        except ValueError:
            last = None
        suppressed = None
        try:
            if raw.get("suppressed_until"):
                suppressed = date.fromisoformat(str(raw["suppressed_until"])[:10])
        except ValueError:
            suppressed = None
        log = tuple(e for e in (raw.get("log") or []) if isinstance(e, Mapping))
        return cls(
            enabled=bool(raw.get("enabled", True)),
            last_run=last,
            suppressed_until=suppressed,
            log=tuple(dict(e) for e in log)[-LOG_LIMIT:],
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "last_run": self.last_run.isoformat(timespec="seconds") if self.last_run else None,
            "suppressed_until": self.suppressed_until.isoformat() if self.suppressed_until else None,
            "log": list(self.log)[-LOG_LIMIT:],
        }

    def with_entry(self, entry: Mapping[str, Any], *, now: datetime | None = None) -> "AutopilotState":
        return replace(
            self,
            last_run=now or self.last_run,
            log=(tuple(self.log) + (dict(entry),))[-LOG_LIMIT:],
        )


@dataclass(frozen=True, slots=True)
class Trigger:
    key: str            # "missed" | "new_work" | "at_risk"
    text: str


def _norm(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


def _known_identities(schedule_data: Mapping[str, Any] | None) -> tuple[set[str], set[str]]:
    """Every task id and title the plan has ever carried, done or not."""
    ids: set[str] = set()
    titles: set[str] = set()
    for day in (schedule_data or {}).get("schedule") or []:
        if not isinstance(day, Mapping):
            continue
        for b in day.get("blocks") or []:
            if not isinstance(b, Mapping) or b.get("is_break"):
                continue
            if b.get("task_id"):
                ids.add(str(b["task_id"]))
                ids.add(group_key(str(b["task_id"])))
            for key in ("parent_title", "assignment", "stage_title"):
                if b.get(key):
                    titles.add(_norm(b[key]))
    for d in (schedule_data or {}).get("deferred") or []:
        if isinstance(d, Mapping):
            if d.get("task_id"):
                ids.add(str(d["task_id"]))
                ids.add(group_key(str(d["task_id"])))
            if d.get("title"):
                titles.add(_norm(d["title"]))
    return ids, titles


def detect_new_work(
    schedule_data: Mapping[str, Any] | None,
    candidates: Iterable[PlannerTask],
    today: date,
    *,
    finished_titles: Iterable[str] = (),
) -> tuple[PlannerTask, ...]:
    """Assignments the student has that their plan does not.

    Matched on id *and* on title, because the same assignment reaches the
    app through several ingest paths with different ids. Overdue work is
    left alone — scheduling it into a day it cannot fit only produces a
    deferral — and so is work more than two weeks out, which a later run
    will pick up when it matters.
    """
    ids, titles = _known_identities(schedule_data)
    titles |= {_norm(t) for t in finished_titles}
    out: list[PlannerTask] = []
    seen: set[str] = set()
    for task in candidates:
        key = group_key(task.id)
        # Deduplicate by task, not by assignment: an essay's stages are
        # separate tasks, and keeping only the first dropped the rest of the
        # work on the floor.
        if task.id in ids or key in ids or task.id in seen:
            continue
        if _norm(task.parent_title or task.title) in titles or _norm(task.title) in titles:
            continue
        if task.due_date is None:
            continue
        if task.due_date < today or (task.due_date - today).days > NEW_WORK_HORIZON_DAYS:
            continue
        out.append(task)
        seen.add(task.id)
    # Stages of one assignment travel together; count assignments, not stages.
    groups: list[str] = []
    for t in out:
        if group_key(t.id) not in groups:
            groups.append(group_key(t.id))
    keep = set(groups[:MAX_NEW_TASKS])
    return tuple(t for t in out if group_key(t.id) in keep)


def is_finished_status(status: Any) -> bool:
    return str(status or "").strip().lower() in _DONE_STATUSES


def triggers_for(
    *,
    missed_minutes: int,
    missed_titles: Sequence[str],
    new_tasks: Sequence[PlannerTask],
    report: RiskReport | None,
) -> list[Trigger]:
    out: list[Trigger] = []
    if missed_minutes > 0:
        names = ", ".join(list(dict.fromkeys(missed_titles))[:2])
        out.append(Trigger("missed", f"{missed_minutes} min you didn't get to ({names}) got new days"))
    if new_tasks:
        titles = list(dict.fromkeys(t.parent_title or t.title for t in new_tasks))
        shown = ", ".join(titles[:2]) + (f" and {len(titles) - 2} more" if len(titles) > 2 else "")
        noun = "assignment" if len(titles) == 1 else "assignments"
        out.append(Trigger("new_work", f"Added {len(titles)} new {noun}: {shown}"))
    if report is not None:
        risky = [t for t in report.at_risk if t.main_risk in ("follow_through", "overrun")]
        if risky:
            out.append(Trigger("at_risk", f"{risky[0].title} was at risk of missing its deadline"))
    return out


def should_run(state: AutopilotState, triggers: Sequence[Trigger], today: date, now: datetime) -> bool:
    """Whether autopilot may act right now."""
    if not state.enabled or not triggers:
        return False
    if state.suppressed_until is not None and today <= state.suppressed_until:
        return False
    facts = {t.key for t in triggers} & {"missed", "new_work"}
    if facts:
        return True
    # Risk alone: not more than once per cooldown window.
    return state.last_run is None or now - state.last_run >= RISK_COOLDOWN


def explain(triggers: Sequence[Trigger], moved: int) -> tuple[str, list[str]]:
    """The one-line headline and the reasons, in the student's words."""
    reasons = [t.text + "." for t in triggers]
    if moved == 0:
        tail = "nothing else had to move"
    elif moved == 1:
        tail = "1 session moved"
    else:
        tail = f"{moved} sessions moved"
    return f"Autopilot updated your plan — {tail}.", reasons
