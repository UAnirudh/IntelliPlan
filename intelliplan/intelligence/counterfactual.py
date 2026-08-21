"""Counterfactual scheduling — build several plans, then choose one.

The planner minimises a cost function, which answers "what is the best plan
under *these* weights". It cannot answer the question a student actually has,
which is "what am I trading away". Front-loading buys deadline safety and
costs a brutal Monday. Spacing revision buys retention and costs buffer. Those
are not bugs in a single objective; they are genuinely different plans for
genuinely different weeks.

So this module runs the planner more than once — under a small set of named
objective profiles — and scores each resulting plan on the *same* metrics,
independently of the weights that produced it. The scores are computed from
the plan, never asked of a language model: a model asked to rate a schedule
out of 100 will produce a number, and the number will not mean anything.

    profiles → build_plan(each) → measure(each) → rank → pick

The measurement axes are the ones a student recognises:

===================  ==========================================================
academic_benefit     How much of the graded work actually gets done, weighted
                     by how much each piece is worth.
learning_benefit     How well the plan supports retention — spacing, not
                     cramming, and weak material before advanced practice.
deadline_risk        Probability mass sitting on work that finishes at or
                     after its deadline.
stress               Peak and variance of daily load against capacity.
completion_odds      Expected fraction of the plan the student, specifically,
                     will actually finish.
===================  ==========================================================

Each is 0..1 and each is signed the way it reads: higher benefit is better,
higher risk and stress are worse.

Purity
------
No Flask, no ORM, no clock reads. ``today`` is passed in.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from statistics import pstdev
from typing import Any, Callable, Mapping, Sequence

from intelliplan.intelligence.planner import (
    DayCapacity,
    Plan,
    PlannerConfig,
    PlannerTask,
    PlannerWeights,
    build_plan,
)

__all__ = [
    "ObjectiveWeights",
    "Profile",
    "PROFILES",
    "ScheduleMetrics",
    "Candidate",
    "measure",
    "generate_candidates",
    "choose",
]


# ── Objective weights ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ObjectiveWeights:
    """How much each measured axis counts when candidates are compared.

    These are deliberately separate from :class:`PlannerWeights`. Those are
    the *prices the optimizer pays while building*; these are *what the
    product values in a finished plan*. Conflating them would mean a plan
    could only ever be judged by the rules that produced it, which is how a
    scheduler ends up unable to notice that its own favourite plan is bad.

    Every weight is documented, none is magic, and the whole set is a
    constructor argument so it can be personalised later without touching
    this module.
    """

    academic: float = 1.0
    learning: float = 0.7
    completion: float = 0.9
    deadline_risk: float = 1.4     # subtracted — missed deadlines dominate
    stress: float = 0.8            # subtracted
    context_switching: float = 0.3  # subtracted

    def score(self, m: "ScheduleMetrics") -> float:
        """Collapse the metrics into one comparable number, 0..1-ish.

        Not clamped: the raw value is more useful for ranking, and the
        clamped version is what :attr:`Candidate.normalised_score` exposes.
        """
        return (
            self.academic * m.academic_benefit
            + self.learning * m.learning_benefit
            + self.completion * m.completion_odds
            - self.deadline_risk * m.deadline_risk
            - self.stress * m.stress
            - self.context_switching * m.context_switching
        )

    @property
    def positive_mass(self) -> float:
        return self.academic + self.learning + self.completion


@dataclass(frozen=True, slots=True)
class Profile:
    """One named way of building a plan."""

    key: str
    label: str
    description: str
    weights: PlannerWeights
    target_utilisation: float = 0.8


#: The candidate set. Small on purpose: each profile is a full planner run,
#: and four meaningfully different plans beat twelve near-identical ones.
#: Every profile must be a *defensible* plan on its own — this is a choice
#: between good options, not a search that includes deliberate straw men.
PROFILES: tuple[Profile, ...] = (
    Profile(
        key="balanced",
        label="Balanced",
        description="Even days, deadlines respected, revision spaced.",
        weights=PlannerWeights(),
    ),
    Profile(
        key="safety_first",
        label="Deadline-safe",
        description="Finish earlier, keep the most room before each deadline.",
        weights=PlannerWeights(load=7.0, urgency=11.0, buffer=22.0, spacing=8.0),
        target_utilisation=0.9,
    ),
    Profile(
        key="retention",
        label="Retention-first",
        description="Spread revision out so it sticks, at the cost of buffer.",
        weights=PlannerWeights(load=10.0, urgency=4.0, buffer=9.0, spacing=20.0),
    ),
    Profile(
        key="sustainable",
        label="Lighter days",
        description="Flatter workload, fewer subjects per day, less to carry.",
        weights=PlannerWeights(load=18.0, urgency=5.0, buffer=12.0, switching=4.0),
        target_utilisation=0.65,
    ),
)


# ── Measurement ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ScheduleMetrics:
    """What a finished plan is worth, on axes a student recognises."""

    academic_benefit: float
    learning_benefit: float
    deadline_risk: float
    stress: float
    completion_odds: float
    context_switching: float
    scheduled_minutes: int
    deferred_minutes: int
    peak_utilisation: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "academic_benefit": round(self.academic_benefit, 3),
            "learning_benefit": round(self.learning_benefit, 3),
            "deadline_risk": round(self.deadline_risk, 3),
            "stress": round(self.stress, 3),
            "completion_odds": round(self.completion_odds, 3),
            "context_switching": round(self.context_switching, 3),
            "scheduled_minutes": self.scheduled_minutes,
            "deferred_minutes": self.deferred_minutes,
            "peak_utilisation": round(self.peak_utilisation, 3),
        }


@dataclass(frozen=True, slots=True)
class Candidate:
    """One plan, its profile, and how it measured."""

    profile: Profile
    plan: Plan
    metrics: ScheduleMetrics
    score: float
    normalised_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.profile.key,
            "label": self.profile.label,
            "description": self.profile.description,
            "score": round(self.normalised_score, 3),
            "metrics": self.metrics.to_dict(),
        }


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _task_weight(task: PlannerTask) -> float:
    """How much this task matters academically, 0..1.

    Points are the honest signal when we have them; priority is the fallback.
    Both are already the output of an engine that explains itself, so nothing
    is invented here.
    """
    if task.points_possible and task.points_possible > 0:
        # 100 points is a full-weight item; beyond that the marginal
        # importance flattens rather than growing without bound.
        return _clamp01(min(1.0, float(task.points_possible) / 100.0) * 0.7
                        + (task.priority / 100.0) * 0.3)
    return _clamp01(task.priority / 100.0)


def measure(
    plan: Plan,
    tasks: Sequence[PlannerTask],
    today: date,
    *,
    completion_probability: Callable[[str, int, date], float] | None = None,
) -> ScheduleMetrics:
    """Score a finished plan. Deterministic, and independent of how it was built.

    ``completion_probability(course, minutes, day) -> float`` is injected so
    the behavioural model can supply real per-student odds. Without it, every
    session is treated as equally likely to happen, which makes
    ``completion_odds`` a pure coverage measure rather than a wrong one.
    """
    tasks_by_id = {t.id: t for t in tasks}
    sessions = plan.sessions

    # ── academic benefit ────────────────────────────────────────────
    # Fraction of the *value* in the horizon that the plan actually covers.
    total_value = sum(_task_weight(t) for t in tasks) or 1.0
    planned_ids = {s.task_id for s in sessions}
    covered_value = sum(
        _task_weight(t) for t in tasks if t.id in planned_ids
    )
    academic = _clamp01(covered_value / total_value)

    # ── deadline risk ───────────────────────────────────────────────
    # Deferred work with a deadline in the horizon is the dominant term:
    # work that is not in the plan at all will not be done. Work that is in
    # the plan but finishes on the due date itself carries residual risk,
    # because a plan with zero slack fails on the first bad evening.
    risk_mass = 0.0
    for d in plan.deferred:
        t = tasks_by_id.get(d.task_id)
        risk_mass += _task_weight(t) if t else 0.5
    last_day: dict[str, date] = {}
    for s in sessions:
        prev = last_day.get(s.task_id)
        if prev is None or s.day > prev:
            last_day[s.task_id] = s.day
    for task_id, day in last_day.items():
        t = tasks_by_id.get(task_id)
        if t is None or t.due_date is None:
            continue
        slack = (t.due_date - day).days
        if slack <= 0:
            risk_mass += _task_weight(t)
        elif slack == 1:
            risk_mass += _task_weight(t) * 0.35
    deadline_risk = _clamp01(risk_mass / total_value)

    # ── stress ──────────────────────────────────────────────────────
    # Peak load matters more than average load: students do not abandon a
    # plan because the week averaged 70% full, they abandon it on the day it
    # asked for four hours. Peak is weighted accordingly, with day-to-day
    # variance as the secondary term.
    utilisations = [d.utilisation for d in plan.days if d.capacity_minutes > 0]
    peak = max(utilisations) if utilisations else 0.0
    spread = pstdev(utilisations) if len(utilisations) > 1 else 0.0
    stress = _clamp01(0.7 * _clamp01(peak) + 0.3 * _clamp01(spread * 2.0))

    # ── learning benefit ────────────────────────────────────────────
    # Two things make a plan teach rather than merely cover: multi-session
    # work is spread across distinct days (distributed practice), and hard
    # or memory-type work is not left to the last day before it is needed.
    learning = _learning_benefit(sessions, tasks_by_id)

    # ── context switching ───────────────────────────────────────────
    switch = _context_switching(plan)

    # ── completion odds ─────────────────────────────────────────────
    if completion_probability is None:
        completion = academic
    else:
        total_minutes = sum(s.minutes for s in sessions) or 1
        expected = 0.0
        for s in sessions:
            try:
                p = float(completion_probability(s.course, s.minutes, s.day))
            except Exception:
                p = 0.62
            expected += _clamp01(p) * s.minutes
        completion = _clamp01(expected / total_minutes)

    return ScheduleMetrics(
        academic_benefit=academic,
        learning_benefit=learning,
        deadline_risk=deadline_risk,
        stress=stress,
        completion_odds=completion,
        context_switching=switch,
        scheduled_minutes=sum(s.minutes for s in sessions),
        deferred_minutes=sum(d.minutes for d in plan.deferred),
        peak_utilisation=peak,
    )


def _learning_benefit(sessions, tasks_by_id: Mapping[str, PlannerTask]) -> float:
    """0..1 measure of how well the plan supports retention."""
    by_task: dict[str, list[date]] = {}
    for s in sessions:
        by_task.setdefault(s.task_id, []).append(s.day)
    if not by_task:
        return 0.0

    scores: list[float] = []
    for task_id, days in by_task.items():
        task = tasks_by_id.get(task_id)
        unique_days = sorted(set(days))
        if len(days) == 1:
            # A single-sitting task is neither spaced nor crammed. Scoring it
            # 0 would punish plans for containing short homework.
            scores.append(0.6)
            continue
        # Fraction of sittings that landed on their own day.
        distribution = len(unique_days) / len(days)
        # Reward real gaps, not merely distinct days: three consecutive days
        # is better than three sittings on one day and worse than a spread.
        span = (unique_days[-1] - unique_days[0]).days
        spread = min(1.0, span / max(1, len(unique_days) - 1) / 2.0)
        weight = 1.2 if task is not None and task.kind in ("exam", "test") else 1.0
        scores.append(_clamp01((0.6 * distribution + 0.4 * spread) * weight))

    return _clamp01(sum(scores) / len(scores))


def _context_switching(plan: Plan) -> float:
    """0..1 cost of how many distinct courses each day demands.

    One course a day is free. The cost is the *excess* over that, normalised
    against a day that touches four different subjects — beyond which more
    switching does not make the day meaningfully worse, it is already bad.
    """
    days = [d for d in plan.days if d.sessions]
    if not days:
        return 0.0
    excess = []
    for d in days:
        courses = {s.course for s in d.sessions if s.course}
        excess.append(_clamp01((max(1, len(courses)) - 1) / 3.0))
    return _clamp01(sum(excess) / len(excess))


# ── Generation and choice ────────────────────────────────────────────


def generate_candidates(
    tasks: Sequence[PlannerTask],
    capacities: Sequence[DayCapacity],
    *,
    model: Any = None,
    today: date,
    base_config: PlannerConfig | None = None,
    objective: ObjectiveWeights | None = None,
    profiles: Sequence[Profile] = PROFILES,
    completion_probability: Callable[[str, int, date], float] | None = None,
) -> list[Candidate]:
    """Build one plan per profile and score them all.

    A profile whose planner run raises is dropped rather than allowed to
    abort the whole comparison — losing one candidate degrades the choice,
    losing the endpoint degrades the product.
    """
    if not tasks:
        # Four identical empty plans is not a choice, and returning them
        # would make "we compared four options" true and meaningless.
        return []

    base_config = base_config or PlannerConfig()
    objective = objective or ObjectiveWeights()

    scored: list[Candidate] = []
    for profile in profiles:
        config = replace(
            base_config,
            weights=profile.weights,
            target_utilisation=profile.target_utilisation,
        )
        try:
            plan = build_plan(tasks, capacities, model=model, today=today, config=config)
        except Exception:
            continue
        metrics = measure(
            plan, tasks, today, completion_probability=completion_probability
        )
        raw = objective.score(metrics)
        scored.append(
            Candidate(
                profile=profile,
                plan=plan,
                metrics=metrics,
                score=raw,
                normalised_score=_normalise(raw, objective),
            )
        )
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored


def _normalise(raw: float, objective: ObjectiveWeights) -> float:
    """Map the raw objective onto 0..1 for display.

    The raw score's range depends on the weights, so the only honest mapping
    is against those weights' own bounds: best possible is all positive terms
    maxed with no penalties, worst is the reverse.
    """
    hi = objective.positive_mass
    lo = -(objective.deadline_risk + objective.stress + objective.context_switching)
    if hi <= lo:
        return 0.0
    return _clamp01((raw - lo) / (hi - lo))


def choose(
    candidates: Sequence[Candidate],
    *,
    require_feasible: Callable[[Candidate], bool] | None = None,
) -> Candidate | None:
    """Pick the winning candidate.

    ``require_feasible`` lets the caller veto a candidate that fails the
    constraint check (see :mod:`intelliplan.intelligence.constraints`). A
    vetoed candidate is skipped, not repaired: if every candidate fails, the
    caller needs to know that rather than be handed a broken plan with the
    best score.
    """
    for c in candidates:
        if require_feasible is None or require_feasible(c):
            return c
    return None
