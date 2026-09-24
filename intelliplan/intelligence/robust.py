"""Risk-controlled planning — size the deadline buffer from simulation.

:func:`intelliplan.intelligence.planner.buffer_days_for` decides how early each
task should finish from two heuristics: how uncertain its estimate is and how
important it is. That is a reasonable first guess and it is blind to the plan
it lives in. An essay with a two-day buffer is safe in an empty week and not
in a week where both of those days are already half-full of a lab report.

This module closes the loop:

    plan → simulate (risk.py) → at-risk work gets one more buffer day → re-plan

and keeps the new plan only if it is *measurably* better on the same sampled
futures (common random numbers — see :mod:`intelliplan.intelligence.risk`).
It never keeps a change that trades one assignment's safety for more expected
misses elsewhere, and it stops as soon as a round fails to help.

What it does not do: invent capacity. Work that is at risk because it does
not fit at all (``main_risk == "not_planned"``) is not something more buffer
can fix, so it is left for the student to see and decide on.

Priorities are deliberately untouched — only buffer moves. A priority bump
would be saved into the plan, read back on the next replan, and bumped
again: a slow drift nobody chose.

Purity
------
No Flask, no ORM, no clock.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Callable, Iterable, Mapping, Sequence

from intelliplan.intelligence.estimation import EstimationModel
from intelliplan.intelligence.planner import (
    CompletionFn,
    DayCapacity,
    Plan,
    PlannerConfig,
    PlannerTask,
    build_plan,
)
from intelliplan.intelligence.risk import RiskReport, group_key

__all__ = ["RobustResult", "plan_with_risk_control"]

#: Assignments below this on-time probability get hardened.
DEFAULT_TARGET = 0.80
#: Low-stakes work is not worth reshuffling a week over.
MIN_PRIORITY = 35
#: A round has to improve weighted on-time by at least this much to count —
#: below it, the "improvement" is within simulation noise.
MIN_GAIN = 0.005


@dataclass(frozen=True)
class RobustResult:
    plan: Plan
    report: RiskReport | None
    #: The task list the winning plan was built from (with buffer changes).
    tasks: tuple[PlannerTask, ...]
    rounds: int
    #: Assignment keys that were given extra buffer.
    hardened: tuple[str, ...]
    #: The first plan's report, for "we made this safer" messaging.
    initial_report: RiskReport | None = None


def plan_with_risk_control(
    tasks: Sequence[PlannerTask],
    capacities: Sequence[DayCapacity],
    *,
    assess: Callable[[Plan, Sequence[PlannerTask]], RiskReport],
    model: EstimationModel | None = None,
    today: date | None = None,
    config: PlannerConfig | None = None,
    completion: CompletionFn | None = None,
    anchors: Mapping[str, Iterable[date]] | None = None,
    target: float = DEFAULT_TARGET,
    rounds: int = 2,
) -> RobustResult:
    """Build a plan, then spend up to ``rounds`` re-solves making it safer.

    ``assess(plan, tasks)`` must be deterministic for a given task set — the
    caller's simulator seeds from task ids, and every round is scored
    against the *original* tasks so a buffer change cannot move the
    weighting it is judged by.
    """
    original = tuple(tasks)

    def build(task_list: Sequence[PlannerTask]) -> Plan:
        return build_plan(
            task_list, capacities, model=model, today=today, config=config,
            completion=completion, anchors=anchors,
        )

    plan = build(original)
    try:
        report = assess(plan, original)
    except Exception:
        return RobustResult(plan=plan, report=None, tasks=original, rounds=0, hardened=())

    best_plan, best_report, best_tasks = plan, report, original
    hardened: set[str] = set()
    used = 0
    for _ in range(max(0, rounds)):
        weak = {
            r.key for r in best_report.tasks
            if r.on_time_probability < target
            and r.priority >= MIN_PRIORITY
            and r.main_risk in ("follow_through", "overrun")
        }
        if not weak:
            break
        candidate_tasks = tuple(
            replace(t, extra_buffer_days=min(3, t.extra_buffer_days + 1))
            if group_key(t.id) in weak and t.due_date is not None
            else t
            for t in best_tasks
        )
        if candidate_tasks == best_tasks:
            break
        used += 1
        candidate = build(candidate_tasks)
        try:
            cand_report = assess(candidate, original)
        except Exception:
            break
        better = (
            cand_report.weighted_on_time >= best_report.weighted_on_time + MIN_GAIN
            and cand_report.expected_missed <= best_report.expected_missed + 0.02
        )
        if not better:
            break
        best_plan, best_report, best_tasks = candidate, cand_report, candidate_tasks
        hardened |= weak

    return RobustResult(
        plan=best_plan,
        report=best_report,
        tasks=best_tasks,
        rounds=used,
        hardened=tuple(sorted(hardened)),
        initial_report=report,
    )
