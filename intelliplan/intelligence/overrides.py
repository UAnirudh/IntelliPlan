"""Overriding the plan, and being told what it costs.

A student must be able to say "I am not doing Physics tonight". A scheduler
that cannot hear that is one they stop opening. But an override accepted
silently is just as bad in the other direction: the work does not evaporate,
it lands on tomorrow, and nobody said so.

So this module does exactly two things:

* applies an override to a plan **literally** — the move the student asked
  for, not a re-optimisation that quietly rearranges four other things;
* measures the difference and reports it as signed consequences.

The literal application matters. Re-solving the whole horizon after a single
"move this to tomorrow" produces a plan the student did not ask for and
cannot recognise, and it makes the consequence report a lie: it would be
describing the optimiser's changes, not theirs. Re-optimisation is available
separately (that is what recovery does) and it is the student's next choice,
not a side effect of this one.

Purity
------
No Flask, no ORM, no clock reads. ``today`` is passed in.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any, Callable, Sequence

from intelliplan.domain.student import Consequence, ConsequenceReport
from intelliplan.intelligence.counterfactual import measure
from intelliplan.intelligence.planner import DayPlan, Deferral, Plan, PlannerTask

__all__ = [
    "OverrideOutcome",
    "rebalance_worth_offering",
    "move_task",
    "skip_task",
    "shorten_task",
    "evaluate",
]


#: Deltas smaller than this are noise and are not reported. A consequence
#: list padded with "+0.4 minutes of workload" trains students to ignore it.
MINUTE_NOISE_FLOOR = 5
METRIC_NOISE_FLOOR = 0.02


@dataclass(frozen=True, slots=True)
class OverrideOutcome:
    """The plan after an override, plus what it cost."""

    plan: Plan
    report: ConsequenceReport
    moved_minutes: int = 0
    target_day: date | None = None
    #: Whether re-solving the week afterwards is worth offering, and why.
    rebalance_offer: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted_possible": self.report.accepted_possible,
            "rebalance_offer": self.rebalance_offer,
            "summary": self.report.summary,
            "moved_minutes": self.moved_minutes,
            "target_day": self.target_day.isoformat() if self.target_day else None,
            "infeasible_reason": self.report.infeasible_reason,
            "gains": [
                {"key": c.key, "label": c.label, "delta": round(c.delta, 3)}
                for c in self.report.gains
            ],
            "costs": [
                {"key": c.key, "label": c.label, "delta": round(c.delta, 3)}
                for c in self.report.costs
            ],
        }


# ── Plan surgery ─────────────────────────────────────────────────────


def _rebuild(days: Sequence[DayPlan], plan: Plan, deferred: Sequence[Deferral]) -> Plan:
    """Reassemble a Plan from mutated days, keeping its diagnosis honest.

    ``total_minutes`` and ``overloaded`` are recomputed rather than carried
    over. A plan that still claims it fits after work was pushed off the end
    of the horizon is a plan that lies at exactly the moment it matters.
    """
    rebuilt = tuple(
        DayPlan(
            day=d.day,
            sessions=d.sessions,
            capacity_minutes=d.capacity_minutes,
            scheduled_minutes=sum(s.minutes for s in d.sessions),
        )
        for d in days
    )
    scheduled = sum(d.scheduled_minutes for d in rebuilt)
    capacity = sum(d.capacity_minutes for d in rebuilt)
    return Plan(
        days=rebuilt,
        deferred=tuple(deferred),
        overloaded=bool(deferred) or scheduled > capacity,
        total_minutes=scheduled,
        capacity_minutes=capacity,
        notes=plan.notes,
    )


def _day_index(days: Sequence[DayPlan], day: date) -> int | None:
    for i, d in enumerate(days):
        if d.day == day:
            return i
    return None


def move_task(
    plan: Plan,
    *,
    task_id: str,
    from_day: date,
    to_day: date | None = None,
) -> tuple[Plan, int, date | None]:
    """Move one task's sittings off ``from_day``.

    ``to_day`` of ``None`` means "the next day in the horizon", which is what
    a student means by "not today". Returns ``(plan, minutes moved, target)``;
    a target of ``None`` means there was nowhere in the horizon to put it and
    the work became a deferral rather than being silently dropped.
    """
    src = _day_index(plan.days, from_day)
    if src is None:
        return plan, 0, None

    moving = [s for s in plan.days[src].sessions if s.task_id == task_id]
    if not moving:
        return plan, 0, None
    minutes = sum(s.minutes for s in moving)

    if to_day is None:
        later = [d.day for d in plan.days if d.day > from_day]
        to_day = later[0] if later else None

    days = list(plan.days)
    days[src] = replace(
        days[src],
        sessions=tuple(s for s in days[src].sessions if s.task_id != task_id),
    )

    deferred = list(plan.deferred)
    if to_day is None:
        for s in moving:
            deferred.append(
                Deferral(
                    task_id=s.task_id,
                    title=s.title,
                    minutes=s.minutes,
                    due_date=s.due_date,
                    reason="You moved this out of the last day in the plan.",
                )
            )
        return _rebuild(days, plan, deferred), minutes, None

    dst = _day_index(days, to_day)
    if dst is None:
        # The target day is inside the horizon but currently holds nothing,
        # so it has no DayPlan yet. Create it with zero capacity known — the
        # capacity check downstream will price it honestly rather than
        # pretending the day is empty and therefore free.
        days.append(DayPlan(day=to_day, sessions=(), capacity_minutes=0, scheduled_minutes=0))
        days.sort(key=lambda d: d.day)
        dst = _day_index(days, to_day)

    relocated = tuple(replace(s, day=to_day) for s in moving)
    days[dst] = replace(days[dst], sessions=days[dst].sessions + relocated)
    return _rebuild(days, plan, deferred), minutes, to_day


def skip_task(plan: Plan, *, task_id: str, day: date) -> tuple[Plan, int]:
    """Drop one task's sittings for a day entirely, as a stated deferral.

    Not the same as moving: the student is saying they will not do it, and
    the plan should say that out loud rather than quietly absorbing it.
    """
    src = _day_index(plan.days, day)
    if src is None:
        return plan, 0
    dropped = [s for s in plan.days[src].sessions if s.task_id == task_id]
    if not dropped:
        return plan, 0

    days = list(plan.days)
    days[src] = replace(
        days[src],
        sessions=tuple(s for s in days[src].sessions if s.task_id != task_id),
    )
    deferred = list(plan.deferred) + [
        Deferral(
            task_id=s.task_id,
            title=s.title,
            minutes=s.minutes,
            due_date=s.due_date,
            reason="You chose to skip this.",
        )
        for s in dropped
    ]
    return _rebuild(days, plan, deferred), sum(s.minutes for s in dropped)


def shorten_task(
    plan: Plan, *, task_id: str, day: date, minutes: int
) -> tuple[Plan, int]:
    """Cut a task's time on one day down to ``minutes``.

    The remainder is recorded as a deferral, because it is still work that
    has to happen somewhere and the honest place to say so is here.
    """
    src = _day_index(plan.days, day)
    if src is None or minutes <= 0:
        return plan, 0
    sessions = list(plan.days[src].sessions)
    target = [s for s in sessions if s.task_id == task_id]
    if not target:
        return plan, 0

    original = sum(s.minutes for s in target)
    if minutes >= original:
        return plan, 0

    # Shrink from the last sitting backwards: the first sitting of the day is
    # the one the student is about to start, and truncating that one is the
    # change they would notice most.
    remaining = minutes
    kept = []
    for s in target:
        take = max(0, min(s.minutes, remaining))
        remaining -= take
        if take > 0:
            kept.append(replace(s, minutes=take))

    rebuilt = [s for s in sessions if s.task_id != task_id] + kept
    days = list(plan.days)
    days[src] = replace(days[src], sessions=tuple(rebuilt))
    deferred = list(plan.deferred) + [
        Deferral(
            task_id=task_id,
            title=target[0].title,
            minutes=original - minutes,
            due_date=target[0].due_date,
            reason="You shortened this sitting.",
        )
    ]
    return _rebuild(days, plan, deferred), original - minutes


def rebalance_worth_offering(plan: Plan, touched: Sequence[date] = ()) -> tuple[bool, str]:
    """Would re-solving the week actually help after this override?

    An override is applied literally, which is right — the student sees the
    move they asked for and nothing else. But a literal move can leave a day
    that no longer works: 80 minutes pushed onto an already-full Wednesday is
    a plan nobody follows, and saying nothing about it is how a scheduler
    quietly becomes wrong.

    So the student is *offered* a rebalance rather than given one. This
    decides whether the offer is worth making, and returns the reason to show
    with it. Returning ``False`` matters as much as returning ``True``:
    prompting after every harmless move trains people to dismiss the prompt.
    """
    days = [d for d in plan.days if d.capacity_minutes > 0]
    if not days:
        return False, ""

    if plan.deferred:
        minutes = sum(d.minutes for d in plan.deferred)
        return True, (
            f"{minutes} minutes of work no longer has a place in your plan. "
            f"Rebalancing would try to fit it back in."
        )

    watched = [d for d in days if not touched or d.day in set(touched)]
    over = [d for d in watched if d.scheduled_minutes > d.capacity_minutes]
    if over:
        worst = max(over, key=lambda d: d.scheduled_minutes - d.capacity_minutes)
        excess = worst.scheduled_minutes - worst.capacity_minutes
        return True, (
            f"{worst.day.strftime('%A')} is now {excess} minutes over what you "
            f"have free. Rebalancing would spread it out."
        )

    # A day pushed far past the others is not infeasible, just unpleasant.
    # Worth mentioning, not worth alarming anyone about.
    loads = [d.utilisation for d in days]
    if loads and max(loads) - (sum(loads) / len(loads)) > 0.35:
        return True, "Your week is lopsided now. Rebalancing would even it out."

    return False, ""


# ── Consequences ─────────────────────────────────────────────────────


_METRIC_LABELS: dict[str, tuple[str, bool]] = {
    # key → (label, "higher is better")
    "academic_benefit": ("graded work covered", True),
    "learning_benefit": ("how well this sticks", True),
    "completion_odds": ("chance you actually finish it", True),
    "deadline_risk": ("deadline risk", False),
    "stress": ("workload pressure", False),
    "context_switching": ("subject switching", False),
}


def evaluate(
    before: Plan,
    after: Plan,
    tasks: Sequence[PlannerTask],
    today: date,
    *,
    completion_probability: Callable[[str, int, date], float] | None = None,
    affected_days: Sequence[date] = (),
) -> ConsequenceReport:
    """Measure what an override actually did, and say it in both directions.

    Both a gain list and a cost list, always. An override report that only
    lists costs reads as the app arguing with the student, and one that only
    lists gains is not a report at all.
    """
    m_before = measure(before, tasks, today, completion_probability=completion_probability)
    m_after = measure(after, tasks, today, completion_probability=completion_probability)

    gains: list[Consequence] = []
    costs: list[Consequence] = []

    for key, (label, higher_better) in _METRIC_LABELS.items():
        b = getattr(m_before, key)
        a = getattr(m_after, key)
        delta = a - b
        if abs(delta) < METRIC_NOISE_FLOOR:
            continue
        improved = delta > 0 if higher_better else delta < 0
        entry = Consequence(key=key, label=label, delta=delta, better=improved)
        (gains if improved else costs).append(entry)

    # Concrete minute deltas per touched day. Percentages are abstract; "45
    # more minutes tomorrow" is the thing a student can actually picture.
    for day in _days_to_report(before, after, affected_days):
        b = _minutes_on(before, day)
        a = _minutes_on(after, day)
        delta = a - b
        if abs(delta) < MINUTE_NOISE_FLOOR:
            continue
        entry = Consequence(
            key=f"minutes:{day.isoformat()}",
            label=f"{_day_label(day, today)} workload",
            delta=float(delta),
            better=delta < 0,
        )
        (gains if delta < 0 else costs).append(entry)

    newly_deferred = _new_deferrals(before, after)
    if newly_deferred:
        costs.append(
            Consequence(
                key="deferred",
                label="work with nowhere left to go",
                delta=float(sum(d.minutes for d in newly_deferred)),
                better=False,
            )
        )

    # The student is never blocked. ``accepted_possible`` stays True by
    # design; what changes is how loudly the report says what it costs.
    infeasible = ""
    if newly_deferred and any(
        d.due_date is not None and d.due_date <= today + timedelta(days=1)
        for d in newly_deferred
    ):
        # Still allowed — the student decides. But it is not the same kind of
        # override as moving an essay a week out, and the UI needs to know.
        infeasible = (
            "This pushes work that is due within a day out of the plan entirely."
        )

    return ConsequenceReport(
        accepted_possible=True,
        gains=tuple(gains),
        costs=tuple(costs),
        summary=_summarise(gains, costs),
        infeasible_reason=infeasible,
    )


def _days_to_report(
    before: Plan, after: Plan, affected: Sequence[date]
) -> list[date]:
    if affected:
        return sorted(set(affected))
    days = {d.day for d in before.days} | {d.day for d in after.days}
    return sorted(days)


def _minutes_on(plan: Plan, day: date) -> int:
    for d in plan.days:
        if d.day == day:
            return d.scheduled_minutes
    return 0


def _day_label(day: date, today: date) -> str:
    delta = (day - today).days
    if delta == 0:
        return "Today's"
    if delta == 1:
        return "Tomorrow's"
    return f"{day.strftime('%A')}'s"


def _new_deferrals(before: Plan, after: Plan) -> list[Deferral]:
    known = {(d.task_id, d.minutes) for d in before.deferred}
    return [d for d in after.deferred if (d.task_id, d.minutes) not in known]


def _summarise(gains: Sequence[Consequence], costs: Sequence[Consequence]) -> str:
    """One sentence, in the student's terms, never scolding.

    Deliberately plain: the point of the summary is that someone reads it in
    two seconds and taps yes or no, not that they are persuaded.
    """
    if not gains and not costs:
        return "This changes almost nothing about the rest of your plan."
    if costs and not gains:
        return f"This mainly costs you: {costs[0].label} gets worse."
    if gains and not costs:
        return f"This is a clean win: {gains[0].label} improves."
    return (
        f"You gain on {gains[0].label}, and pay for it in {costs[0].label}."
    )
