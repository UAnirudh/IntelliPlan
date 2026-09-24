"""Deadline risk — the probability each piece of work actually lands on time.

A plan that fits is not a plan that works. Every number the planner used is a
guess with an error bar: the essay the model sized at 180 minutes might take
240, the Thursday block might not happen, the Sunday that looked free might
fill up. The question a student has is not "does this fit" but "am I going to
make it", and that is a question about *distributions*, not point estimates.

So this module simulates the plan. Each run draws:

* **How long each task really takes** — log-normal around the estimate, with
  the spread the estimation model measured for this student and this kind of
  work. A shared "this week" factor correlates the draws, because a student
  who is running slow on one assignment usually is on the others too.
* **Which sittings actually happen** — Bernoulli per sitting, with the
  probability the Follow-Through model predicts for that sitting on that day.
  Failures cluster: a small per-day chance of a *lost day* (ill, a game ran
  late, a family thing) takes every sitting on it at once. Independent
  failures would badly understate risk, because what sinks a deadline is
  rarely one skipped block — it is the Tuesday where nothing happened.
* **Whether the slack gets used** — free time left in the plan is where a
  student catches up, and whether they do on a given day is itself uncertain.

Then work is settled earliest-deadline-first against the slack that remains
before each due date. Repeating that a few hundred times gives, per
assignment, the fraction of futures in which it was finished on time.

Common random numbers
---------------------
Comparisons ("what if I skip tonight?" vs "what if I skip it and we
rebalance?") reuse the same seed, so both plans face the *same* sampled
futures. The difference between them is then the effect of the plan, not the
noise of two independent simulations — which at 300 samples would otherwise
be as large as the effect itself.

Purity
------
No Flask, no ORM, no clock. Seeded, so the same plan always reports the same
numbers.
"""

from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable, Mapping, Sequence

from intelliplan.intelligence.planner import Plan, PlannerTask, Session

__all__ = [
    "TaskRisk",
    "RiskReport",
    "RiskConfig",
    "simulate",
    "group_key",
]


@dataclass(frozen=True, slots=True)
class RiskConfig:
    samples: int = 300
    #: Chance any given day is lost outright. ~1 day in 16.
    lost_day_rate: float = 0.06
    #: Correlation of duration overruns across a student's tasks in one week.
    week_correlation: float = 0.35
    #: Duration draws are clipped to this band around the estimate.
    duration_band: tuple[float, float] = (0.4, 3.0)
    default_sigma: float = 0.35
    default_follow_through: float = 0.70
    #: Probability the student uses free time to catch up on a given day,
    #: when the caller supplies nothing better.
    default_recovery_rate: float = 0.50
    #: Students who are behind put in time they did not plan — the night
    #: before a deadline, mostly. Up to this many extra minutes per day,
    #: on the last ``crunch_days`` days before a due date, with the same
    #: probability as ordinary catch-up. Leaving this out made every dense
    #: week look hopeless, which is not what dense weeks are.
    crunch_minutes: int = 60
    crunch_days: int = 2
    on_track: float = 0.80
    watch: float = 0.60


@dataclass(frozen=True, slots=True)
class TaskRisk:
    """How one assignment fares across the simulated futures."""

    key: str
    title: str
    due_date: date | None
    on_time_probability: float
    #: Mean shortfall, in minutes, across the futures where it was late.
    expected_late_minutes: int
    planned_minutes: int
    unplanned_minutes: int
    priority: int
    status: str             # "on_track" | "watch" | "at_risk"
    #: What most often sank it: "not_planned", "follow_through", "overrun".
    main_risk: str

    @property
    def percent(self) -> int:
        return int(round(self.on_time_probability * 100))

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "on_time_probability": round(self.on_time_probability, 3),
            "percent": self.percent,
            "expected_late_minutes": self.expected_late_minutes,
            "planned_minutes": self.planned_minutes,
            "unplanned_minutes": self.unplanned_minutes,
            "priority": self.priority,
            "status": self.status,
            "main_risk": self.main_risk,
        }


@dataclass(frozen=True, slots=True)
class RiskReport:
    tasks: tuple[TaskRisk, ...]
    #: Sum over assignments of P(late) — the number of deadlines the student
    #: should expect to miss if nothing changes.
    expected_missed: float
    #: Priority-weighted on-time probability across everything with a due date.
    weighted_on_time: float
    samples: int
    seed: int

    @property
    def at_risk(self) -> tuple[TaskRisk, ...]:
        return tuple(t for t in self.tasks if t.status == "at_risk")

    def by_key(self) -> dict[str, TaskRisk]:
        return {t.key: t for t in self.tasks}

    def to_dict(self) -> dict[str, Any]:
        return {
            "tasks": [t.to_dict() for t in self.tasks],
            "expected_missed": round(self.expected_missed, 3),
            "weighted_on_time": round(self.weighted_on_time, 3),
            "at_risk": len(self.at_risk),
            "samples": self.samples,
        }


def group_key(task_id: str) -> str:
    """Stages of one assignment roll up to the assignment.

    A student cares whether the essay is on time, not whether "outline the
    argument" is. Stage ids are ``"<assignment>::<stage>"``.
    """
    return str(task_id).split("::", 1)[0]


def _stable_seed(parts: Sequence[str]) -> int:
    return zlib.crc32("|".join(sorted(parts)).encode("utf-8")) & 0x7FFFFFFF


@dataclass
class _Work:
    task_id: str
    group: str
    due: date | None
    priority: int
    sigma: float
    #: (day, minutes, p_done) per planned sitting, in day order.
    sittings: list[tuple[date, int, float]]
    unplanned: int

    @property
    def planned(self) -> int:
        return sum(m for _, m, _ in self.sittings)


def simulate(
    plan: Plan,
    tasks: Sequence[PlannerTask],
    *,
    today: date,
    follow_through: Callable[[Session, int], float] | None = None,
    sigma_for: Callable[[PlannerTask], float] | None = None,
    recovery_rate: float | None = None,
    capacity_by_day: Mapping[date, int] | None = None,
    config: RiskConfig | None = None,
    seed: int | None = None,
) -> RiskReport:
    """Simulate ``plan`` and report per-assignment on-time probability.

    ``follow_through(session, prior_load_minutes) -> P(done)`` is normally a
    closure over the student's :class:`FollowThroughModel`. ``sigma_for`` is
    the log-space duration spread for a task, normally from the estimation
    model. Both default to population-level constants so the simulator is
    never the reason a plan cannot be scored.
    """
    config = config or RiskConfig()
    tasks_by_id = {t.id: t for t in tasks}
    recovery = config.default_recovery_rate if recovery_rate is None else recovery_rate
    recovery = max(0.05, min(0.95, float(recovery)))

    # ── collect the work ────────────────────────────────────────────
    works: dict[str, _Work] = {}

    def work_for(task_id: str, due: date | None, priority: int) -> _Work:
        w = works.get(task_id)
        if w is None:
            task = tasks_by_id.get(task_id)
            sigma = config.default_sigma
            if task is not None and sigma_for is not None:
                try:
                    sigma = float(sigma_for(task))
                except Exception:
                    sigma = config.default_sigma
            w = _Work(
                task_id=task_id,
                group=group_key(task_id),
                due=task.due_date if task is not None else due,
                priority=task.priority if task is not None else priority,
                sigma=max(0.05, min(1.2, sigma)),
                sittings=[],
                unplanned=0,
            )
            works[task_id] = w
        return w

    capacity: dict[date, int] = {}
    used: dict[date, int] = {}
    for day_plan in plan.days:
        capacity[day_plan.day] = int(day_plan.capacity_minutes)
        used[day_plan.day] = int(day_plan.scheduled_minutes)
        load = 0
        for s in day_plan.sessions:
            p = config.default_follow_through
            if follow_through is not None:
                try:
                    p = float(follow_through(s, load))
                except Exception:
                    p = config.default_follow_through
            work_for(s.task_id, s.due_date, s.priority).sittings.append(
                (s.day, int(s.minutes), max(0.01, min(0.99, p)))
            )
            load += int(s.minutes)
    if capacity_by_day:
        for d, m in capacity_by_day.items():
            capacity[d] = int(m)
    for deferral in plan.deferred:
        work_for(deferral.task_id, deferral.due_date, 50).unplanned += int(deferral.minutes)

    graded = [w for w in works.values() if w.due is not None]
    if not graded:
        return RiskReport(tasks=(), expected_missed=0.0, weighted_on_time=1.0,
                          samples=0, seed=seed or 0)

    horizon = sorted(capacity)
    horizon_end = horizon[-1] if horizon else today
    slack_days = [d for d in horizon if d >= today]
    base_slack = {d: max(0, capacity.get(d, 0) - used.get(d, 0)) for d in slack_days}
    mean_capacity = (
        sum(capacity.get(d, 0) for d in slack_days) / len(slack_days) if slack_days else 0.0
    )

    all_days = sorted({d for w in graded for d, _, _ in w.sittings} | set(slack_days))
    graded.sort(key=lambda w: (w.due, -w.priority, w.task_id))
    groups: dict[str, list[_Work]] = {}
    for w in graded:
        groups.setdefault(w.group, []).append(w)

    if seed is None:
        seed = _stable_seed([w.task_id for w in graded] + [today.isoformat()])
    rng = random.Random(seed)

    samples = max(20, int(config.samples))
    rho = max(0.0, min(0.95, config.week_correlation))
    ind = math.sqrt(1.0 - rho * rho)
    lo_band, hi_band = config.duration_band
    q = max(0.0, min(0.5, config.lost_day_rate))

    on_time = {g: 0 for g in groups}
    shortfall = {g: 0.0 for g in groups}
    late_count = {g: 0 for g in groups}
    cause = {g: {"not_planned": 0, "follow_through": 0, "overrun": 0} for g in groups}

    for _ in range(samples):
        week = rng.gauss(0.0, 1.0)
        lost = {d: rng.random() < q for d in all_days}
        recovers = {d: rng.random() < recovery for d in slack_days}
        slack = {d: (base_slack[d] if recovers[d] and not lost[d] else 0) for d in slack_days}
        crunch = {
            d: (config.crunch_minutes if not lost[d] and rng.random() < recovery else 0)
            for d in slack_days
        }

        late_by_group: dict[str, float] = {}
        group_cause: dict[str, str] = {}
        for w in graded:
            z = rho * week + ind * rng.gauss(0.0, 1.0)
            factor = max(lo_band, min(hi_band, math.exp(w.sigma * z)))
            required = (w.planned + w.unplanned) * factor

            delivered = 0
            first_fail: date | None = None
            for day, minutes, p in w.sittings:
                # Marginal P(done) is p; conditioned on the day surviving it
                # is p / (1 − q), which keeps the per-sitting rate honest
                # while letting failures cluster on lost days.
                if lost.get(day):
                    ok = False
                else:
                    ok = rng.random() < min(1.0, p / (1.0 - q) if q < 1 else p)
                if ok:
                    delivered += minutes
                elif first_fail is None:
                    first_fail = day

            deficit = required - delivered
            if deficit <= 0.5:
                continue

            if first_fail is not None:
                # A skipped block can still be done later that evening —
                # unless the whole day was lost, in which case tomorrow is
                # the earliest chance.
                start = first_fail + timedelta(days=1) if lost.get(first_fail) else first_fail
                reason = "follow_through"
            elif w.sittings:
                start = w.sittings[-1][0]
                reason = "overrun"
            else:
                start = today
                reason = "not_planned"
            if w.unplanned and w.unplanned >= deficit * 0.5:
                reason = "not_planned"

            for d in slack_days:
                if deficit <= 0.5:
                    break
                if d < start or d > w.due:
                    continue
                take = min(slack[d], deficit)
                if take > 0:
                    slack[d] -= take
                    deficit -= take

            if deficit > 0.5 and config.crunch_minutes > 0:
                first_crunch = w.due - timedelta(days=max(1, config.crunch_days) - 1)
                for d in slack_days:
                    if deficit <= 0.5:
                        break
                    if d < max(start, first_crunch) or d > w.due:
                        continue
                    take = min(crunch[d], deficit)
                    if take > 0:
                        crunch[d] -= take
                        deficit -= take

            if deficit > 0.5 and w.due > horizon_end:
                # Time beyond the plan's horizon is real time. Credit half of
                # an average day for each day past the horizon, discounted by
                # how reliably the student catches up.
                extra = (w.due - horizon_end).days * mean_capacity * 0.5 * recovery
                deficit -= min(deficit, extra)

            if deficit > 0.5:
                late_by_group[w.group] = late_by_group.get(w.group, 0.0) + deficit
                group_cause.setdefault(w.group, reason)

        for g in groups:
            if g in late_by_group:
                late_count[g] += 1
                shortfall[g] += late_by_group[g]
                cause[g][group_cause[g]] += 1
            else:
                on_time[g] += 1

    # ── aggregate ───────────────────────────────────────────────────
    results: list[TaskRisk] = []
    for g, members in groups.items():
        head = members[0]
        task = tasks_by_id.get(head.task_id)
        title = ""
        if task is not None:
            title = task.parent_title or task.title
        p = on_time[g] / samples
        status = (
            "on_track" if p >= config.on_track
            else "watch" if p >= config.watch
            else "at_risk"
        )
        causes = cause[g]
        main = max(causes, key=lambda k: (causes[k], k)) if late_count[g] else ""
        results.append(
            TaskRisk(
                key=g,
                title=title or g,
                due_date=max(m.due for m in members),
                on_time_probability=round(p, 4),
                expected_late_minutes=int(round(shortfall[g] / late_count[g])) if late_count[g] else 0,
                planned_minutes=sum(m.planned for m in members),
                unplanned_minutes=sum(m.unplanned for m in members),
                priority=max(m.priority for m in members),
                status=status,
                main_risk=main,
            )
        )

    results.sort(key=lambda r: (r.on_time_probability, r.due_date or date.max, r.key))
    total_weight = sum(max(1, r.priority) for r in results) or 1
    weighted = sum(max(1, r.priority) * r.on_time_probability for r in results) / total_weight
    return RiskReport(
        tasks=tuple(results),
        expected_missed=round(sum(1.0 - r.on_time_probability for r in results), 4),
        weighted_on_time=round(weighted, 4),
        samples=samples,
        seed=seed,
    )
