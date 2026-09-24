"""Rescheduling by intent — "I can't study tonight" in one tap.

Students do not think in blocks and days. They think "I'm sick tomorrow",
"I've only got half an hour today", "not this one, not today", "I've got the
whole of Saturday free". This module takes exactly those intents and turns
each into a plan the student can follow, with three properties the naive
approaches all lack:

1. **It repairs, it doesn't rebuild.** The plan the student is looking at is
   the starting point. Its sittings become *anchors* the optimizer pays to
   move (:func:`intelliplan.intelligence.planner._stability_cost`), so losing
   Thursday moves Thursday's work and leaves Monday alone. Rebuilding from
   scratch would be locally optimal and globally infuriating: the whole
   fortnight reshuffles over one lost evening.

2. **It shows the consequence before committing.** Every intent is solved
   twice: *literally* (the lost evening's work just doesn't happen) and
   *rebalanced*. Both are run through the deadline simulator on the same
   sampled futures, so "if you skip tonight and we rebalance, every deadline
   stays on track; if you just skip it, the lab report drops to 54%" is a
   measured statement, not a slogan.

3. **It never argues.** Every intent is accepted. Where one costs something,
   the result says what — it does not refuse.

The plan is reconstructed from the saved ``schedule_data`` itself — no
LMS round-trip — so the same call works from the scheduler, the Command
Center, a phone, or a notification action.

Purity
------
No Flask, no ORM, no clock. ``today`` is passed in; I/O lives in the glue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any, Callable, Iterable, Mapping, Sequence

from intelliplan.intelligence.estimation import EstimationModel
from intelliplan.intelligence.planner import (
    CompletionFn,
    DayCapacity,
    DayPlan,
    Deferral,
    Plan,
    PlannerConfig,
    PlannerTask,
    Session,
    build_plan,
)
from intelliplan.intelligence.risk import RiskReport, group_key
from intelliplan.intelligence.robust import plan_with_risk_control

__all__ = [
    "INTENTS",
    "Snapshot",
    "Disruption",
    "Adjusted",
    "PlanChange",
    "Replan",
    "snapshot_from_schedule",
    "parse_disruption",
    "apply_disruption",
    "plan_from_sittings",
    "diff_plans",
    "replan",
]

#: Every intent the engine understands, with the words the UI uses.
INTENTS: dict[str, str] = {
    "skip_day": "Can't study that day",
    "limit_day": "Less time that day",
    "add_time": "Extra time that day",
    "push": "Not today — push it",
    "pin": "Keep it where I put it",
    "done": "Already done",
    "progress": "Did some of it",
    "catch_up": "I'm behind — catch me up",
}

#: Furthest day an intent may name. Matches the override endpoint's bound.
MAX_HORIZON_DAYS = 30

#: Rebalancing must beat the minimal-change plan by at least this much
#: priority-weighted on-time probability, or save this many expected missed
#: deadlines, to be worth the moves it costs.
MIN_REBALANCE_GAIN = 0.01
MIN_REBALANCE_SAVED = 0.05

_PART_SUFFIX = re.compile(r"\s*\(part \d+ of \d+\)\s*$", re.IGNORECASE)
_PRIORITY_WORDS = {"critical": 90, "high": 80, "medium": 50, "low": 30}


# ── Snapshot of the current plan ─────────────────────────────────────


@dataclass(frozen=True)
class Snapshot:
    """The student's current plan, as planner inputs.

    ``tasks`` carry the *remaining* minutes (checked-off blocks are already
    subtracted) and are ``calibrated`` — those minutes already went through
    the estimation model once and must not be corrected a second time.
    """

    today: date
    tasks: tuple[PlannerTask, ...]
    #: ``{task_id: ((day, minutes), ...)}`` — future, not-yet-done sittings.
    sittings: Mapping[str, tuple[tuple[date, int], ...]]
    #: Minutes that were planned on a past day and not done.
    missed: Mapping[str, int] = field(default_factory=dict)
    #: Minutes already done, per task, from the checkboxes.
    done: Mapping[str, int] = field(default_factory=dict)

    @property
    def task_ids(self) -> frozenset[str]:
        return frozenset(t.id for t in self.tasks)

    def anchors(self) -> dict[str, dict[date, int]]:
        out: dict[str, dict[date, int]] = {}
        for k, v in self.sittings.items():
            for d, _ in v:
                out.setdefault(k, {})[d] = out.get(k, {}).get(d, 0) + 1
        return out

    def title_of(self, key: str) -> str:
        for t in self.tasks:
            if t.id == key or group_key(t.id) == key:
                return t.parent_title or t.title
        return key


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _priority(value: Any) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0, min(100, int(value)))
    word = str(value or "").strip().lower()
    if word.isdigit():
        return max(0, min(100, int(word)))
    return _PRIORITY_WORDS.get(word, 50)


def _is_done(entry: Any) -> bool:
    if entry is True:
        return True
    if isinstance(entry, Mapping):
        return bool(entry.get("done"))
    return False


def _block_key(block: Mapping[str, Any]) -> str:
    return str(block.get("block_id") or block.get("id") or "")


def _title_of(block: Mapping[str, Any], fallback: str) -> str:
    stage = str(block.get("stage_title") or "").strip()
    if stage:
        return stage
    label = _PART_SUFFIX.sub("", str(block.get("assignment") or "")).strip()
    return label or str(block.get("parent_title") or "").strip() or fallback


@dataclass
class _Acc:
    task_id: str
    title: str
    parent_title: str
    course: str
    kind: str
    due: date | None
    priority: int
    difficulty: str
    stage_index: int
    remaining: int = 0
    done: int = 0
    missed: int = 0
    future: list[tuple[date, int]] = field(default_factory=list)


def snapshot_from_schedule(
    schedule_data: Mapping[str, Any] | None,
    progress: Mapping[str, Any] | None,
    today: date,
    *,
    credit: Mapping[str, int] | None = None,
    finished: Iterable[str] = (),
) -> Snapshot:
    """Rebuild planner inputs from a saved plan and its checkboxes.

    ``credit`` is extra work done that the checkboxes do not show — minutes
    from Active-study sittings the student started and did not finish.
    ``finished`` are task ids known complete from elsewhere.
    """
    progress = progress or {}
    finished_ids = {str(f) for f in finished}
    accs: dict[str, _Acc] = {}

    def acc_for(task_id: str, block: Mapping[str, Any]) -> _Acc:
        a = accs.get(task_id)
        if a is None:
            parent = str(block.get("parent_title") or "").strip()
            title = _title_of(block, task_id)
            a = _Acc(
                task_id=task_id,
                title=title,
                parent_title=parent if parent and parent != title else "",
                course=str(block.get("course") or ""),
                kind=str(block.get("kind") or "homework").strip().lower() or "homework",
                due=_as_date(block.get("due_date")),
                # Enrichment rewrites ``priority`` into a High/Medium/Low
                # label and keeps the planner's number as ``priority_score``.
                priority=_priority(
                    block.get("priority_score")
                    if block.get("priority_score") is not None
                    else block.get("priority")
                ),
                difficulty=str(block.get("difficulty") or "medium").strip().lower(),
                stage_index=_int(block.get("stage_index")),
            )
            if a.kind in ("study", "break", "reading", "writing", "practice"):
                # Interactive-view "kind" labels are presentation, not the
                # planner's work taxonomy.
                a.kind = "homework"
            accs[task_id] = a
        return a

    for day in (schedule_data or {}).get("schedule") or []:
        if not isinstance(day, Mapping):
            continue
        day_date = _as_date(day.get("date"))
        for block in day.get("blocks") or []:
            if not isinstance(block, Mapping) or block.get("is_break"):
                continue
            minutes = _int(block.get("duration_minutes"))
            if minutes <= 0:
                continue
            task_id = str(
                block.get("task_id") or block.get("parent_title") or block.get("assignment") or ""
            ).strip()
            if not task_id:
                continue
            a = acc_for(task_id, block)
            if _is_done(progress.get(_block_key(block))):
                a.done += minutes
                continue
            a.remaining += minutes
            if block.get("unplaced") or day_date is None:
                continue
            if day_date < today:
                a.missed += minutes
            else:
                a.future.append((day_date, minutes))

    for d in (schedule_data or {}).get("deferred") or []:
        if not isinstance(d, Mapping):
            continue
        task_id = str(d.get("task_id") or d.get("title") or "").strip()
        minutes = _int(d.get("minutes"))
        if not task_id or minutes <= 0:
            continue
        a = acc_for(task_id, {
            "assignment": d.get("title"), "due_date": d.get("due_date"), "priority": 60,
        })
        a.remaining += minutes

    tasks: list[PlannerTask] = []
    sittings: dict[str, tuple[tuple[date, int], ...]] = {}
    missed: dict[str, int] = {}
    done: dict[str, int] = {}
    for a in accs.values():
        if a.task_id in finished_ids or group_key(a.task_id) in finished_ids:
            continue
        extra = max(0, _int((credit or {}).get(a.task_id)))
        if a.remaining - extra <= 0:
            continue
        tasks.append(
            PlannerTask(
                id=a.task_id,
                title=a.title,
                course=a.course,
                kind=a.kind,
                parent_title=a.parent_title,
                due_date=a.due,
                est_minutes=a.remaining,
                difficulty=a.difficulty if a.difficulty in ("easy", "medium", "hard") else "medium",
                priority=a.priority,
                stage_index=a.stage_index,
                done_minutes=extra,
                calibrated=True,
            )
        )
        sittings[a.task_id] = tuple(sorted(a.future))
        if a.missed:
            missed[a.task_id] = a.missed
        if a.done:
            done[a.task_id] = a.done

    tasks = _restore_stage_chain(tasks)
    return Snapshot(
        today=today,
        tasks=tuple(tasks),
        sittings=sittings,
        missed=missed,
        done=done,
    )


def _restore_stage_chain(tasks: list[PlannerTask]) -> list[PlannerTask]:
    """Stages of one assignment are a chain; rebuild the ``depends_on`` edges.

    The decomposition templates are all linear, so each remaining stage
    waits on the nearest earlier remaining stage. A stage whose predecessor
    is fully done waits on nothing.
    """
    groups: dict[str, list[PlannerTask]] = {}
    for t in tasks:
        if "::" in t.id:
            groups.setdefault(group_key(t.id), []).append(t)
    if not groups:
        return tasks
    deps: dict[str, tuple[str, ...]] = {}
    for members in groups.values():
        members.sort(key=lambda t: (t.stage_index, t.id))
        for prev, cur in zip(members, members[1:]):
            deps[cur.id] = (prev.id,)
    return [replace(t, depends_on=deps.get(t.id, t.depends_on)) for t in tasks]


# ── Intents ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Disruption:
    kind: str
    day: date | None = None
    minutes: int | None = None
    task_id: str | None = None
    #: For ``pin``: the day the sitting was dragged *from*.
    from_day: date | None = None
    #: For ``add_time``: the caller already put the extra time into the
    #: capacities it passes (e.g. through real clock windows), so the engine
    #: must not add it again.
    capacity_applied: bool = False

    def describe(self, snapshot: Snapshot) -> str:
        day = f"{self.day:%a %b} {self.day.day}" if self.day else ""
        title = snapshot.title_of(self.task_id) if self.task_id else ""
        if self.kind == "skip_day":
            return f"Cleared {day}"
        if self.kind == "limit_day":
            return f"{day} trimmed to {self.minutes} min"
        if self.kind == "add_time":
            return f"Added {self.minutes} min on {day}"
        if self.kind == "push":
            return f"{title} pushed to {day} or later"
        if self.kind == "pin":
            return f"{title} kept on {day}"
        if self.kind == "done":
            return f"{title} marked done"
        if self.kind == "progress":
            return f"Credited {self.minutes} min on {title}"
        return "Caught up"


def parse_disruption(payload: Mapping[str, Any], today: date) -> Disruption:
    """Validate an API payload into a :class:`Disruption`.

    Raises ``ValueError`` with a sentence a student can read.
    """
    kind = str(payload.get("action") or payload.get("kind") or "").strip().lower()
    if kind not in INTENTS:
        raise ValueError("Unknown adjustment.")
    day = _as_date(payload.get("day"))
    if day is None and kind in ("skip_day", "limit_day", "add_time"):
        day = today
    if day is not None and day < today:
        raise ValueError("That day has already passed.")
    if day is not None and (day - today).days > MAX_HORIZON_DAYS:
        # The plan covers two weeks. A date years out would be stored on the
        # plan and carried forever; refused rather than silently clamped.
        raise ValueError("That's too far ahead to plan around yet.")
    minutes = payload.get("minutes")
    minutes_i = _int(minutes, -1) if minutes is not None else None
    task_id = str(payload.get("task_id") or "").strip() or None

    if kind == "limit_day":
        if minutes_i is None or minutes_i < 0:
            raise ValueError("How many minutes do you have?")
        minutes_i = min(minutes_i, 24 * 60)
    elif kind in ("add_time", "progress"):
        if minutes_i is None or minutes_i <= 0:
            raise ValueError("How many minutes?")
        minutes_i = min(minutes_i, 16 * 60)
    if kind in ("push", "pin", "done", "progress") and not task_id:
        raise ValueError("Which assignment?")
    if kind == "push" and day is None:
        day = today + timedelta(days=1)
    if kind == "pin" and day is None:
        raise ValueError("Which day should it go on?")
    return Disruption(
        kind=kind,
        day=day,
        minutes=minutes_i,
        task_id=task_id,
        from_day=_as_date(payload.get("from_day")),
    )


# ── Applying an intent ───────────────────────────────────────────────


@dataclass(frozen=True)
class Adjusted:
    tasks: tuple[PlannerTask, ...]
    capacities: tuple[DayCapacity, ...]
    #: ``{task_id: {day: sittings}}`` — where work sits in the visible plan.
    anchors: Mapping[str, Mapping[date, int]]
    #: The intent applied with no replanning at all — the "do nothing" plan.
    literal: Plan
    sittings: Mapping[str, tuple[tuple[date, int], ...]]


def _targets(snapshot: Snapshot, task_id: str) -> list[str]:
    """A task id, or every stage of an assignment when given its group key."""
    ids = [t.id for t in snapshot.tasks]
    if task_id in ids:
        return [task_id]
    return [i for i in ids if group_key(i) == task_id]


def apply_disruption(
    snapshot: Snapshot,
    capacities: Sequence[DayCapacity],
    disruption: Disruption,
) -> Adjusted:
    """Turn an intent into planner inputs, plus the literal consequence."""
    tasks = {t.id: t for t in snapshot.tasks}
    order = [t.id for t in snapshot.tasks]
    sittings = {k: list(v) for k, v in snapshot.sittings.items()}
    caps = {c.day: c for c in capacities}
    kind, day = disruption.kind, disruption.day
    #: Sittings the intent knocked off their day. They keep their size in
    #: the replan — the student saw a 70-minute block, and quietly turning it
    #: into two 35-minute ones is a change nobody asked for.
    displaced: dict[str, list[int]] = {}

    def displace(tid: str, keep) -> None:
        kept = []
        for d, m in sittings.get(tid, []):
            if keep(d, m):
                kept.append((d, m))
            else:
                displaced.setdefault(tid, []).append(int(m))
        sittings[tid] = kept

    if kind == "skip_day" and day is not None:
        if day in caps:
            caps[day] = replace(caps[day], minutes=0)
        for k in list(sittings):
            displace(k, lambda d, m: d != day)

    elif kind == "limit_day" and day is not None:
        budget = max(0, int(disruption.minutes or 0))
        if day in caps:
            caps[day] = replace(caps[day], minutes=min(caps[day].minutes, budget))
        # Literally: the first sittings of the day that fit, in plan order.
        for k in order:
            def fits(d, m):
                nonlocal budget
                if d != day:
                    return True
                if m <= budget:
                    budget -= m
                    return True
                return False
            if k in sittings:
                displace(k, fits)

    elif kind == "add_time" and day is not None and not disruption.capacity_applied:
        extra = max(0, int(disruption.minutes or 0))
        if day in caps:
            caps[day] = replace(caps[day], minutes=caps[day].minutes + extra)
        else:
            caps[day] = DayCapacity(day=day, minutes=extra)

    elif kind == "push" and disruption.task_id:
        until = day or (snapshot.today + timedelta(days=1))
        for tid in _targets(snapshot, disruption.task_id):
            tasks[tid] = replace(tasks[tid], not_before=until)
            displace(tid, lambda d, m: d >= until)

    elif kind == "pin" and disruption.task_id and day is not None:
        targets = _targets(snapshot, disruption.task_id)
        chosen = None
        if disruption.from_day is not None:
            chosen = next(
                (t for t in targets if any(d == disruption.from_day for d, _ in sittings.get(t, []))),
                None,
            )
        chosen = chosen or (targets[0] if targets else None)
        if chosen is not None:
            moving = next(
                (m for d, m in sittings.get(chosen, []) if d == disruption.from_day),
                None,
            )
            minutes = int(disruption.minutes or moving or 0)
            if minutes <= 0:
                minutes = int(tasks[chosen].est_minutes or 30)
            tasks[chosen] = replace(
                tasks[chosen], pinned=tuple(tasks[chosen].pinned) + ((day, minutes),)
            )
            remaining = list(sittings.get(chosen, []))
            if disruption.from_day is not None:
                for i, (d, m) in enumerate(remaining):
                    if d == disruption.from_day:
                        remaining.pop(i)
                        break
            sittings[chosen] = sorted(remaining + [(day, minutes)])

    elif kind == "done" and disruption.task_id:
        for tid in _targets(snapshot, disruption.task_id):
            tasks.pop(tid, None)
            sittings.pop(tid, None)
        # A finished stage no longer blocks the next one.
        gone = set(order) - set(tasks)
        for tid, t in list(tasks.items()):
            if any(dep in gone for dep in t.depends_on):
                tasks[tid] = replace(t, depends_on=tuple(x for x in t.depends_on if x not in gone))

    elif kind == "progress" and disruption.task_id:
        credit = max(0, int(disruption.minutes or 0))
        for tid in _targets(snapshot, disruption.task_id):
            if credit <= 0:
                break
            t = tasks[tid]
            left = max(0, int(t.est_minutes or 0) - t.done_minutes)
            use = min(left, credit)
            credit -= use
            tasks[tid] = replace(t, done_minutes=t.done_minutes + use)
            trimmed, need = [], use
            for d, m in sittings.get(tid, []):
                if need >= m:
                    need -= m
                    continue
                trimmed.append((d, m - need))
                need = 0
            sittings[tid] = trimmed

    # catch_up: nothing to change. Missed work is already in the task
    # minutes with no anchor, so the optimizer places it fresh.

    # Each task's surviving sittings become its own anchored split, so the
    # replan can put every one back exactly where it was. Pinned sittings are
    # already carved out and must not be counted twice.
    anchors: dict[str, dict[date, int]] = {}
    for tid, t in list(tasks.items()):
        pinned = list(t.pinned)
        kept: list[tuple[date, int]] = []
        for d, m in sittings.get(tid, []):
            if (d, m) in pinned:
                pinned.remove((d, m))
                continue
            kept.append((d, int(m)))
        floating = [(None, m) for m in displaced.get(tid, [])]
        tasks[tid] = replace(t, sittings=tuple(kept) + tuple(floating))
        for d, _ in kept:
            anchors.setdefault(tid, {})[d] = anchors.get(tid, {}).get(d, 0) + 1

    new_tasks = tuple(tasks[t] for t in order if t in tasks)
    new_caps = tuple(caps[d] for d in sorted(caps))
    literal = plan_from_sittings(new_tasks, sittings, new_caps, snapshot.today)
    return Adjusted(
        tasks=new_tasks,
        capacities=new_caps,
        anchors=anchors,
        literal=literal,
        sittings={k: tuple(v) for k, v in sittings.items()},
    )


def plan_from_sittings(
    tasks: Sequence[PlannerTask],
    sittings: Mapping[str, Sequence[tuple[date, int]]],
    capacities: Sequence[DayCapacity],
    today: date,
) -> Plan:
    """Materialise a set of sittings as a :class:`Plan`, with no optimizing.

    Remaining work that has no sitting inside the horizon — missed work,
    work on a day that was cleared — is reported as deferred, which is what
    it literally is.
    """
    by_id = {t.id: t for t in tasks}
    days = sorted(c.day for c in capacities if c.day >= today)
    cap = {c.day: c.minutes for c in capacities}
    on_day: dict[date, list[Session]] = {d: [] for d in days}
    deferred: list[Deferral] = []
    total = 0
    for tid, t in by_id.items():
        rows = [(d, m) for d, m in sittings.get(tid, ()) if d in on_day and m > 0]
        placed = 0
        for index, (d, m) in enumerate(sorted(rows), start=1):
            on_day[d].append(
                Session(
                    task_id=tid, title=t.title, course=t.course, kind=t.kind, day=d,
                    minutes=int(m), part_index=index, part_total=len(rows),
                    difficulty=t.difficulty, priority=t.priority, due_date=t.due_date,
                    parent_title=t.parent_title, stage_index=t.stage_index,
                )
            )
            placed += int(m)
        total += placed
        need = max(0, int(t.est_minutes or 0) - t.done_minutes) - placed
        if need > 0:
            deferred.append(Deferral(
                task_id=tid, title=t.title, minutes=need, due_date=t.due_date,
                reason="Not in the plan as it stands.",
            ))
    day_plans = tuple(
        DayPlan(day=d, sessions=tuple(on_day[d]), capacity_minutes=max(0, cap.get(d, 0)),
                scheduled_minutes=sum(s.minutes for s in on_day[d]))
        for d in days
    )
    capacity = sum(max(0, cap.get(d, 0)) for d in days)
    return Plan(
        days=day_plans,
        deferred=tuple(deferred),
        overloaded=bool(deferred) or any(
            p.scheduled_minutes > p.capacity_minutes for p in day_plans
        ),
        total_minutes=total,
        capacity_minutes=capacity,
    )


# ── Diffing ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PlanChange:
    key: str
    title: str
    kind: str                       # "moved" | "added" | "removed" | "unfit"
    from_days: tuple[date, ...] = ()
    to_days: tuple[date, ...] = ()
    reason: str = ""
    before_percent: int | None = None
    after_percent: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "kind": self.kind,
            "from": [d.isoformat() for d in self.from_days],
            "to": [d.isoformat() for d in self.to_days],
            "reason": self.reason,
            "before_percent": self.before_percent,
            "after_percent": self.after_percent,
        }


def _days_by_group(plan: Plan) -> dict[str, list[date]]:
    out: dict[str, list[date]] = {}
    for s in plan.sessions:
        out.setdefault(group_key(s.task_id), []).append(s.day)
    return {k: sorted(v) for k, v in out.items()}


def _titles(plan: Plan, tasks: Sequence[PlannerTask]) -> dict[str, str]:
    out = {group_key(t.id): (t.parent_title or t.title) for t in tasks}
    for s in plan.sessions:
        out.setdefault(group_key(s.task_id), s.parent_title or s.title)
    return out


def diff_plans(
    before: Plan,
    after: Plan,
    tasks: Sequence[PlannerTask],
    *,
    report_before: RiskReport | None = None,
    report_after: RiskReport | None = None,
    limit: int = 12,
) -> list[PlanChange]:
    """What changed, per assignment, in the student's terms."""
    old = _days_by_group(before)
    new = _days_by_group(after)
    titles = _titles(before, tasks) | _titles(after, tasks)
    unfit_before: dict[str, int] = {}
    for d in before.deferred:
        unfit_before[group_key(d.task_id)] = unfit_before.get(group_key(d.task_id), 0) + d.minutes
    unfit_after: dict[str, int] = {}
    for d in after.deferred:
        unfit_after[group_key(d.task_id)] = unfit_after.get(group_key(d.task_id), 0) + d.minutes
    # Only a deferral that is new, or has grown, is news. Work that was
    # already missed and still has no room is reported by the risk panel.
    unfit = {k for k, m in unfit_after.items() if m > unfit_before.get(k, 0)}
    rb = report_before.by_key() if report_before else {}
    ra = report_after.by_key() if report_after else {}

    def pct(report: Mapping[str, Any], key: str) -> int | None:
        r = report.get(key)
        return r.percent if r is not None else None

    changes: list[PlanChange] = []
    for key in sorted(set(old) | set(new) | unfit):
        was, now = old.get(key, []), new.get(key, [])
        title = titles.get(key, key)
        common = dict(before_percent=pct(rb, key), after_percent=pct(ra, key))
        if key in unfit:
            changes.append(PlanChange(key, title, "unfit", tuple(was), tuple(now),
                                      "Some of this no longer fits before its deadline.", **common))
        elif was and not now:
            changes.append(PlanChange(key, title, "removed", tuple(was), (),
                                      "Done, or taken off the plan.", **common))
        elif now and not was:
            changes.append(PlanChange(key, title, "added", (), tuple(now),
                                      "Back in the plan.", **common))
        elif sorted(set(was)) != sorted(set(now)):
            earlier = now[0] < was[0]
            changes.append(PlanChange(
                key, title, "moved", tuple(was), tuple(now),
                "Moved earlier to keep a buffer before it's due." if earlier
                else "Moved to a day with more room.",
                **common,
            ))
    order = {"unfit": 0, "moved": 1, "added": 2, "removed": 3}
    changes.sort(key=lambda c: (order.get(c.kind, 9), (c.to_days or c.from_days or (date.max,))[0], c.title))
    return changes[: max(0, limit)]


def _sitting_churn(
    before: Plan, after: Plan, disruption: Disruption | None = None
) -> tuple[int, int]:
    """``(kept, moved)`` — measured against the plan the student was looking at.

    A sitting *moved* if its task is still planned but no longer has a
    sitting on that day. Work that had no day before (missed, or newly
    fitting) is placed, not moved, and a finished task is not a move. The
    sitting a student dragged themselves is their move, not ours, so it is
    not counted against the plan.
    """
    old = {(s.task_id, s.day) for s in before.sessions}
    new = {(s.task_id, s.day) for s in after.sessions}
    still_planned = {s.task_id for s in after.sessions} | {d.task_id for d in after.deferred}
    kept = len(old & new)
    moved = sum(1 for pair in old - new if pair[0] in still_planned)
    if disruption is not None and disruption.kind == "pin" and disruption.from_day is not None:
        if any(t == disruption.task_id or group_key(t) == disruption.task_id
               for t, d in old - new if d == disruption.from_day):
            moved = max(0, moved - 1)
    return kept, moved


# ── The replan ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Replan:
    disruption: Disruption
    #: "minimal" (only forced moves) or "rebalanced" (measurably safer).
    strategy: str
    plan: Plan
    before: Plan
    literal: Plan
    tasks: tuple[PlannerTask, ...]
    changes: tuple[PlanChange, ...]
    kept_sittings: int
    moved_sittings: int
    report_before: RiskReport | None = None
    report_literal: RiskReport | None = None
    report_after: RiskReport | None = None
    hardened: tuple[str, ...] = ()
    headline: str = ""
    detail: str = ""

    @property
    def stability(self) -> float:
        total = self.kept_sittings + self.moved_sittings
        return 1.0 if total == 0 else self.kept_sittings / total

    def summary(self) -> dict[str, Any]:
        def risk(r: RiskReport | None) -> dict[str, Any] | None:
            if r is None:
                return None
            return {
                "expected_missed": round(r.expected_missed, 2),
                "weighted_on_time": round(r.weighted_on_time, 3),
                "at_risk": [t.to_dict() for t in r.at_risk],
            }

        return {
            "intent": self.disruption.kind,
            "strategy": self.strategy,
            "headline": self.headline,
            "detail": self.detail,
            "changes": [c.to_dict() for c in self.changes],
            "kept_sittings": self.kept_sittings,
            "moved_sittings": self.moved_sittings,
            "stability": round(self.stability, 3),
            "deferred_minutes": sum(d.minutes for d in self.plan.deferred),
            "overloaded": self.plan.overloaded,
            "hardened": list(self.hardened),
            "risk": {
                "before": risk(self.report_before),
                "if_you_do_nothing": risk(self.report_literal),
                "after": risk(self.report_after),
            },
        }


def replan(
    snapshot: Snapshot,
    capacities: Sequence[DayCapacity],
    disruption: Disruption,
    *,
    model: EstimationModel | None = None,
    config: PlannerConfig | None = None,
    completion: CompletionFn | None = None,
    assess: Callable[[Plan, Sequence[PlannerTask]], RiskReport] | None = None,
    risk_rounds: int = 2,
) -> Replan:
    """Apply one intent and repair the plan around it.

    ``assess`` should be a fixed-seed simulator so the before / literal /
    after reports face the same sampled futures.
    """
    today = snapshot.today
    config = config or PlannerConfig()
    before = plan_from_sittings(snapshot.tasks, snapshot.sittings, capacities, today)
    adjusted = apply_disruption(snapshot, capacities, disruption)

    def build(cfg: PlannerConfig) -> Plan:
        return build_plan(
            adjusted.tasks, adjusted.capacities, model=model, today=today,
            config=cfg, completion=completion, anchors=adjusted.anchors,
        )

    hardened: tuple[str, ...] = ()
    report_after = None
    strategy = "rebalanced"
    if assess is not None:
        result = plan_with_risk_control(
            adjusted.tasks, adjusted.capacities, assess=assess, model=model,
            today=today, config=config, completion=completion,
            anchors=adjusted.anchors, rounds=risk_rounds,
        )
        after, report_after, hardened = result.plan, result.report, result.hardened
        # Do no harm. The minimal-change plan moves only what the intent
        # forces to move. Rebalancing wins only when it is *measurably*
        # safer on the same simulated futures; otherwise the student keeps
        # the week they already know.
        minimal_cfg = replace(
            config, weights=replace(config.weights, stability=config.weights.stability * 20)
        )
        minimal = build(minimal_cfg)
        try:
            report_minimal = assess(minimal, adjusted.tasks)
        except Exception:
            report_minimal = None
        if report_minimal is not None and report_after is not None:
            gain = report_after.weighted_on_time - report_minimal.weighted_on_time
            saved = report_minimal.expected_missed - report_after.expected_missed
            if gain < MIN_REBALANCE_GAIN and saved < MIN_REBALANCE_SAVED:
                after, report_after, hardened = minimal, report_minimal, ()
                strategy = "minimal"
    else:
        after = build(config)

    report_before = report_literal = None
    if assess is not None:
        try:
            report_before = assess(before, snapshot.tasks)
            report_literal = assess(adjusted.literal, adjusted.tasks)
        except Exception:
            report_before = report_literal = None

    # Per-assignment odds are shown "before this change → now": where the
    # student was, and where they are. That is the comparison they can act
    # on; "vs. doing nothing" is a strawman they never chose.
    changes = diff_plans(
        before, after, adjusted.tasks,
        report_before=report_before or report_literal, report_after=report_after,
    )
    kept, moved = _sitting_churn(before, after, disruption)
    headline, detail = _headline(
        disruption, snapshot, after, changes, moved,
        report_literal=report_literal, report_after=report_after,
    )
    return Replan(
        disruption=disruption,
        strategy=strategy,
        plan=after,
        before=before,
        literal=adjusted.literal,
        tasks=adjusted.tasks,
        changes=tuple(changes),
        kept_sittings=kept,
        moved_sittings=moved,
        report_before=report_before,
        report_literal=report_literal,
        report_after=report_after,
        hardened=hardened,
        headline=headline,
        detail=detail,
    )


def _headline(
    disruption: Disruption,
    snapshot: Snapshot,
    after: Plan,
    changes: Sequence[PlanChange],
    moved: int,
    *,
    report_literal: RiskReport | None,
    report_after: RiskReport | None,
) -> tuple[str, str]:
    """One sentence for the toast, one for the sheet. Deterministic.

    Built only from numbers the result already carries, so it can never
    claim something the plan does not show.
    """
    first = disruption.describe(snapshot) + "."
    if moved == 0:
        second = "Nothing else had to move."
    elif moved == 1:
        second = "1 session moved."
    else:
        second = f"{moved} sessions moved."

    unfit = sum(d.minutes for d in after.deferred)
    if unfit:
        detail = (
            f"{unfit} min of work has no room before its deadline — "
            "add time, or decide what to drop."
        )
    elif report_after is not None:
        at_risk = list(report_after.at_risk)
        watch = [t for t in report_after.tasks if t.status == "watch"]
        if at_risk:
            names = ", ".join(t.title for t in at_risk[:2])
            more = len(at_risk) - 2
            detail = f"Still at risk: {names}" + (f" and {more} more." if more > 0 else ".")
        elif watch:
            names = " and ".join(t.title for t in watch[:2])
            verb = "is" if len(watch[:2]) == 1 else "are"
            detail = f"{names} {verb} tight — worth keeping an eye on."
        else:
            detail = "Every deadline is still on track."
    else:
        detail = ""

    if report_literal is not None and report_after is not None:
        saved = report_literal.expected_missed - report_after.expected_missed
        if saved >= 0.15:
            amount = f"about {saved:.1f} deadline{'s' if round(saved, 1) != 1.0 else ''}"
            if disruption.kind in ("skip_day", "limit_day"):
                detail += f" Rebalancing protects {amount} compared with just skipping."
            elif disruption.kind == "push":
                detail += f" Rebalancing protects {amount} compared with just dropping those sessions."
            elif disruption.kind == "add_time":
                detail += f" Using the extra time protects {amount}."
            else:
                detail += f" The adjusted plan protects {amount}."
    return f"{first} {second}", detail.strip()
