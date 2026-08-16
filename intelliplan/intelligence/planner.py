"""The scheduling model.

This is the part that decides *what work happens on what day*, and it is a
different job from the one ``scheduler_engine.place_day_blocks`` does.
Placement takes an ordered list of blocks and lays them into clock windows.
This module decides what that list should be in the first place: how much
work exists, how long each piece really takes, how it should be broken into
sittings, which days those sittings land on, and how much room sits between
the last sitting and the deadline.

Why an optimizer and not rules
------------------------------
The constraints genuinely conflict. Finishing everything early maximises
buffer but creates a brutal Monday. Balancing days perfectly ignores which
deadline is closest. Spacing exam revision across a week is right until the
week does not exist. There is no ordering of ``if`` statements that resolves
these — you have to write down what you value, price each violation, and
search. So this module defines a cost function over (session → day)
assignments and minimises it with a greedy seed plus local search.

The cost function is the product's opinion, stated in one place and
tunable: see :class:`PlannerWeights`.

What it produces
----------------
A :class:`Plan` — sessions with dates, minutes, and *reasons*. Every session
can explain why it is where it is, because a schedule the student does not
believe is a schedule the student ignores.

Purity
------
No Flask, no ORM, no clock reads. ``today`` is always passed in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Sequence

from intelliplan.intelligence.estimation import (
    Estimate,
    EstimationModel,
    baseline_minutes,
)

__all__ = [
    "PlannerTask",
    "DayCapacity",
    "PlannerWeights",
    "PlannerConfig",
    "Session",
    "DayPlan",
    "Plan",
    "Deferral",
    "build_plan",
    "reschedule",
    "Reality",
    "task_from_mapping",
    "capacities_from_minutes",
    "session_cap_for",
    "split_into_sessions",
    "buffer_days_for",
]


# ── Inputs ────────────────────────────────────────────────────────────

#: Kinds where repeated, spaced exposure beats one long sitting. Revision
#: and recall decay fast within a session and consolidate between them.
MEMORY_KINDS = frozenset({"exam", "test"})
#: Kinds with a high cost to reload context — you lose 15 minutes finding
#: your place in an essay, so fewer, longer sittings win.
DEEP_KINDS = frozenset({"project", "lab"})

_DIFFICULTY_FACTOR = {"easy": 1.15, "medium": 1.0, "hard": 0.85}
_DIFFICULTY_LOAD = {"easy": 0.85, "medium": 1.0, "hard": 1.25}

MIN_SESSION_MINUTES = 15
SESSION_CAP_BOUNDS = (20, 110)


@dataclass(frozen=True, slots=True)
class PlannerTask:
    """One assignment as the planner sees it.

    ``est_minutes`` is the *raw* estimate from upstream (the LMS, the LLM,
    the student). The planner never uses it directly — it runs it through
    the :class:`EstimationModel` first.
    """

    id: str
    title: str
    course: str = ""
    kind: str = "homework"
    due_date: date | None = None
    est_minutes: int | None = None
    difficulty: str = "medium"
    #: 0..100 from ``intelliplan.intelligence.priority``. Drives how early
    #: work is pulled and who wins when the week is over capacity.
    priority: int = 50
    points_possible: float | None = None
    #: Known subtasks. More parts means the work chunks naturally, so the
    #: planner is freer to split it across sittings.
    subtask_count: int = 0
    #: Ids of tasks that must be finished before this one starts.
    depends_on: tuple[str, ...] = ()
    #: Minutes already spent (in-progress work carries over on reschedule).
    done_minutes: int = 0
    #: Concepts this task exercises. Used to detect a week that stacks
    #: several unfamiliar ideas at once.
    concepts: tuple[str, ...] = ()
    #: Concepts the student has previously struggled with, 0..1 mastery.
    #: Supplied by the caller from the concept-mastery repository.
    weak_concept_penalty: float = 0.0


@dataclass(frozen=True, slots=True)
class DayCapacity:
    """How much study time a single day actually holds."""

    day: date
    minutes: int
    #: Multiplier for how well this student historically works this day.
    #: 1.0 is neutral; below 1 means "measured low output, keep it light".
    quality: float = 1.0
    #: How much the student *wants* to do on this day, when they have said.
    #: ``minutes`` is what the calendar allows; this is what they signed up
    #: for. Above it, work is priced, not forbidden — a deadline still wins.
    #: ``None`` falls back to ``config.target_utilisation`` of ``minutes``.
    comfort_minutes: int | None = None

    @property
    def usable(self) -> int:
        return max(0, self.minutes)

    def soft_limit(self, target_utilisation: float) -> int:
        """The fill level above which the convex load penalty starts biting."""
        default = int(round(self.usable * target_utilisation))
        if self.comfort_minutes is not None:
            default = min(default, max(0, int(self.comfort_minutes)))
        return max(1, default)


@dataclass(frozen=True, slots=True)
class PlannerWeights:
    """The prices the optimizer pays for each kind of compromise.

    These are the product's values in numeric form. Raising ``load`` makes
    plans flatter; raising ``urgency`` makes them front-loaded; raising
    ``spacing`` spreads revision further.
    """

    load: float = 10.0            # convex penalty for filling a day
    urgency: float = 6.0          # pulls high-priority work earlier
    buffer: float = 14.0          # cost of eating into pre-deadline slack
    spacing: float = 12.0         # cost of stacking one task's sittings
    switching: float = 1.5        # cost of many courses in one day
    day_quality: float = 3.0      # cost of using a historically weak day
    fragmentation: float = 2.0    # cost of tiny leftover sittings
    concept_stack: float = 2.5    # cost of piling unfamiliar concepts up


@dataclass(frozen=True, slots=True)
class PlannerConfig:
    """Knobs that are policy rather than preference."""

    horizon_days: int = 14
    #: Fraction of a day's capacity the planner will fill before the convex
    #: penalty makes further work expensive. Not a hard cap — a deadline
    #: can and should override it.
    target_utilisation: float = 0.8
    #: Never schedule a task's last sitting later than this many days before
    #: the deadline, when the calendar allows it.
    min_buffer_days: int = 1
    max_buffer_days: int = 3
    #: Local-search effort. Each pass tries to relocate every session.
    optimise_passes: int = 6
    weights: PlannerWeights = field(default_factory=PlannerWeights)


# ── Outputs ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Session:
    """One sitting of work on one day."""

    task_id: str
    title: str
    course: str
    kind: str
    day: date
    minutes: int
    part_index: int
    part_total: int
    difficulty: str
    priority: int
    due_date: date | None
    #: Why this session is on this day, in the student's language.
    reasons: tuple[str, ...] = ()
    #: Model bookkeeping — surfaced in the "how this was planned" panel.
    estimate_ratio: float = 1.0
    estimate_confidence: float = 0.0

    @property
    def label(self) -> str:
        if self.part_total <= 1:
            return self.title
        return f"{self.title} (part {self.part_index} of {self.part_total})"


@dataclass(frozen=True, slots=True)
class DayPlan:
    """Everything scheduled on one day, plus how loaded that leaves it."""

    day: date
    sessions: tuple[Session, ...]
    capacity_minutes: int
    scheduled_minutes: int

    @property
    def utilisation(self) -> float:
        if self.capacity_minutes <= 0:
            return 1.0 if self.scheduled_minutes else 0.0
        return round(self.scheduled_minutes / self.capacity_minutes, 3)


@dataclass(frozen=True, slots=True)
class Deferral:
    """Work that would not fit, and what that means."""

    task_id: str
    title: str
    minutes: int
    due_date: date | None
    reason: str


@dataclass(frozen=True, slots=True)
class Plan:
    """The scheduler's answer, with its own diagnosis attached."""

    days: tuple[DayPlan, ...]
    deferred: tuple[Deferral, ...] = ()
    #: True when total work exceeded total capacity in the horizon. The UI
    #: must say so out loud rather than quietly shipping an impossible plan.
    overloaded: bool = False
    total_minutes: int = 0
    capacity_minutes: int = 0
    notes: tuple[str, ...] = ()

    @property
    def sessions(self) -> tuple[Session, ...]:
        return tuple(s for d in self.days for s in d.sessions)

    def sessions_on(self, day: date) -> tuple[Session, ...]:
        for d in self.days:
            if d.day == day:
                return d.sessions
        return ()


# ── Session sizing ────────────────────────────────────────────────────


def session_cap_for(task: PlannerTask, stamina: int) -> int:
    """The longest single sitting this student should do on this task.

    Starts from their measured focus length, then adjusts for the shape of
    the work: recall practice degrades within a session, deep work pays a
    context-reload tax that rewards longer ones, and hard material burns
    attention faster than easy material.
    """
    cap = float(stamina)
    if task.kind in MEMORY_KINDS:
        cap *= 0.8
    elif task.kind in DEEP_KINDS:
        cap *= 1.25
    cap *= _DIFFICULTY_FACTOR.get(task.difficulty, 1.0)
    lo, hi = SESSION_CAP_BOUNDS
    return int(max(lo, min(hi, round(cap))))


def split_into_sessions(total_minutes: int, cap: int, subtask_count: int = 0) -> list[int]:
    """Divide total work into equal sittings no longer than ``cap``.

    Equal, not greedy. Greedy filling turns 100 minutes into 45 + 45 + 10,
    and nobody opens a textbook for ten minutes. Three sittings of 34/33/33
    is the same work and every one of them is worth sitting down for.

    When the task has known subtasks and they divide more cleanly than the
    cap does, we follow the subtasks — a five-question problem set wants to
    break at question boundaries, not at minute 34.
    """
    total = max(0, int(total_minutes))
    if total <= 0:
        return []
    if total <= cap:
        return [total]

    parts = -(-total // cap)  # ceil without importing math for one call
    if subtask_count >= 2:
        # Prefer a part count that divides the subtasks evenly, if it is
        # close to what the cap already implied.
        for candidate in (subtask_count, subtask_count // 2, subtask_count // 3):
            if candidate >= 2 and abs(candidate - parts) <= 1 and total / candidate <= cap:
                parts = candidate
                break

    base, extra = divmod(total, parts)
    if base < MIN_SESSION_MINUTES and parts > 1:
        parts = max(1, total // MIN_SESSION_MINUTES)
        base, extra = divmod(total, parts)
    return [base + (1 if i < extra else 0) for i in range(parts)]


def buffer_days_for(task: PlannerTask, estimate: Estimate, config: PlannerConfig) -> int:
    """How many days before the deadline this task should finish.

    Buffer is bought with the two things that predict a missed deadline:
    how badly we understand the task's length, and how much it costs to
    miss. An exam we cannot size gets three days; a confidently-estimated
    homework gets one.
    """
    span = config.max_buffer_days - config.min_buffer_days
    if span <= 0:
        return config.min_buffer_days
    uncertainty = 1.0 - estimate.confidence
    if estimate.minutes > 0:
        uncertainty = max(uncertainty, min(1.0, estimate.buffer_minutes / estimate.minutes))
    importance = task.priority / 100.0
    pressure = 0.6 * uncertainty + 0.4 * importance
    return config.min_buffer_days + int(round(span * min(1.0, max(0.0, pressure))))


# ── Internal working state ────────────────────────────────────────────


@dataclass
class _Item:
    """A session before it has a day. Mutable inside the optimizer only."""

    task: PlannerTask
    minutes: int
    part_index: int
    part_total: int
    estimate: Estimate
    earliest: date
    latest: date            # the buffered target, not the raw deadline
    hard_latest: date       # the actual deadline
    day: date | None = None
    reasons: tuple[str, ...] = ()
    #: Set when the item could not be placed. Explains what blocked it.
    defer_reason: str = ""

    @property
    def load(self) -> float:
        """Minutes weighted by how tiring the work is."""
        return self.minutes * _DIFFICULTY_LOAD.get(self.task.difficulty, 1.0)


@dataclass
class _DayState:
    #: Real free minutes. A hard ceiling — the planner cannot spend time the
    #: student does not have, and pretending otherwise is exactly the
    #: behaviour that teaches people to ignore a planner.
    capacity: int
    #: The comfortable fill level. Work above this is allowed but priced.
    soft_capacity: int
    quality: float
    minutes: int = 0
    load: float = 0.0
    courses: dict[str, int] = field(default_factory=dict)
    task_sessions: dict[str, int] = field(default_factory=dict)
    concepts: dict[str, float] = field(default_factory=dict)

    def add(self, item: _Item) -> None:
        self.minutes += item.minutes
        self.load += item.load
        self.courses[item.task.course] = self.courses.get(item.task.course, 0) + 1
        self.task_sessions[item.task.id] = self.task_sessions.get(item.task.id, 0) + 1
        for c in item.task.concepts:
            self.concepts[c] = self.concepts.get(c, 0.0) + item.task.weak_concept_penalty

    def remove(self, item: _Item) -> None:
        self.minutes -= item.minutes
        self.load -= item.load
        if self.courses.get(item.task.course):
            self.courses[item.task.course] -= 1
            if self.courses[item.task.course] <= 0:
                del self.courses[item.task.course]
        if self.task_sessions.get(item.task.id):
            self.task_sessions[item.task.id] -= 1
            if self.task_sessions[item.task.id] <= 0:
                del self.task_sessions[item.task.id]
        for c in item.task.concepts:
            self.concepts[c] = max(0.0, self.concepts.get(c, 0.0) - item.task.weak_concept_penalty)


# ── Cost model ────────────────────────────────────────────────────────


def _placement_cost(
    item: _Item,
    day: date,
    state: _DayState,
    horizon: int,
    weights: PlannerWeights,
) -> float:
    """Price of putting ``item`` on ``day`` given what is already there.

    Every term is a compromise the student would recognise if you described
    it to them, which is the test for whether a term belongs here.
    """
    if state.capacity <= 0:
        return math.inf

    # Hard feasibility first. Everything below this line is preference; this
    # line is physics. A day holds the minutes it holds.
    if state.minutes + item.minutes > state.capacity:
        return math.inf

    projected = state.load + item.load
    utilisation = projected / max(1.0, float(state.soft_capacity or state.capacity))
    # Convex in utilisation: the tenth minute of a light day is nearly free,
    # the ninetieth of a full one is not. This is what flattens the week
    # without ever hard-forbidding a heavy day the deadline demands.
    cost = weights.load * (utilisation ** 2)

    # Pull important work earlier. Scaled by priority so a critical exam
    # moves several days and a low-stakes worksheet barely moves at all.
    days_out = max(0, (day - item.earliest).days)
    cost += weights.urgency * (item.task.priority / 100.0) * (days_out / max(1, horizon))

    # Eating into the buffer is expensive; blowing the deadline is fatal.
    if day > item.hard_latest:
        return math.inf
    if day > item.latest:
        overrun = (day - item.latest).days
        cost += weights.buffer * (1 + overrun)

    # Stacking a task's own sittings on one day defeats the point of
    # splitting it — worse for memory work, where the spacing *is* the
    # mechanism.
    same_task = state.task_sessions.get(item.task.id, 0)
    if same_task:
        multiplier = 2.0 if item.task.kind in MEMORY_KINDS else 1.0
        cost += weights.spacing * multiplier * (same_task ** 2)

    # Context switching. Two courses in an evening is normal; five is a day
    # spent opening and closing books.
    distinct = len(state.courses) + (0 if item.task.course in state.courses else 1)
    cost += weights.switching * max(0, distinct - 2)

    # Historically weak day for this student.
    if state.quality < 1.0:
        cost += weights.day_quality * (1.0 - state.quality)

    # Piling several shaky concepts into one day.
    if item.task.weak_concept_penalty > 0:
        stacked = sum(state.concepts.values())
        cost += weights.concept_stack * item.task.weak_concept_penalty * (1.0 + stacked)

    # Prefer not to leave a sitting stranded alone on an otherwise empty day
    # when it is a fragment of something bigger.
    if item.part_total > 1 and item.minutes < MIN_SESSION_MINUTES * 1.5 and state.minutes == 0:
        cost += weights.fragmentation

    return cost


# ── Planning ──────────────────────────────────────────────────────────


def build_plan(
    tasks: Sequence[PlannerTask],
    capacities: Sequence[DayCapacity],
    model: EstimationModel | None = None,
    today: date | None = None,
    config: PlannerConfig | None = None,
) -> Plan:
    """Decide what work happens on what day.

    ``capacities`` is the student's real free time per day, already derived
    from availability minus commitments (see
    ``scheduler_engine.windows_for_date``). The planner never invents time
    the student does not have — when the work does not fit, it says so.
    """
    config = config or PlannerConfig()
    model = model or EstimationModel()
    today = today or (capacities[0].day if capacities else date.today())

    days = sorted({c.day for c in capacities})
    days = [d for d in days if d >= today][: config.horizon_days]
    if not days:
        return Plan(days=(), overloaded=bool(tasks), notes=("No available study time in the horizon.",))

    cap_by_day = {c.day: c for c in capacities}
    state: dict[date, _DayState] = {
        d: _DayState(
            capacity=cap_by_day[d].usable,
            soft_capacity=cap_by_day[d].soft_limit(config.target_utilisation),
            quality=cap_by_day[d].quality,
        )
        for d in days
    }

    items = _build_items(tasks, model, today, days, config, cap_by_day)
    if not items:
        return Plan(
            days=tuple(DayPlan(d, (), cap_by_day[d].usable, 0) for d in days),
            capacity_minutes=sum(cap_by_day[d].usable for d in days),
        )

    _assign(items, days, state, config)
    _optimise(items, days, state, config)
    # Local search frees space the greedy pass did not have. Retry anything
    # it gave up on before telling the student it will not fit.
    _retry_deferred(items, days, state, config)
    # When the week genuinely does not hold everything, decide *what* gets
    # dropped on value rather than on whatever the greedy order happened to
    # place first.
    items = _triage(items, days, state, config)
    # Two sittings of the same task on the same day is a split that bought
    # nothing; fold them back before anyone sees them.
    items = _coalesce(items)
    _renumber(items)

    return _materialise(items, days, cap_by_day, state, config)


def _build_items(
    tasks: Sequence[PlannerTask],
    model: EstimationModel,
    today: date,
    days: Sequence[date],
    config: PlannerConfig,
    cap_by_day: Mapping[date, DayCapacity],
) -> list[_Item]:
    """Estimate, size, and split every task into unplaced sittings."""
    horizon_end = days[-1]
    items: list[_Item] = []
    # A sitting longer than the student's biggest free day can never be
    # placed anywhere, so it would sit in the deferred pile forever while
    # the day it should have used stayed empty. Focus length is an upper
    # bound on a sitting; the calendar is a harder one.
    largest_day = max((cap_by_day[d].usable for d in days), default=0)

    for task in tasks:
        raw = task.est_minutes or baseline_minutes(task.kind, task.points_possible)
        estimate = model.predict(raw, task.course, task.kind, task.points_possible)
        remaining = max(0, estimate.minutes - max(0, task.done_minutes))
        if remaining <= 0:
            continue

        cap = session_cap_for(task, model.stamina_minutes)
        if largest_day > 0:
            cap = min(cap, largest_day)
        chunks = split_into_sessions(remaining, cap, task.subtask_count)
        if not chunks:
            continue

        buffer_days = buffer_days_for(task, estimate, config)
        if task.due_date is None:
            latest = hard_latest = horizon_end
        else:
            hard_latest = min(task.due_date, horizon_end)
            latest = task.due_date - timedelta(days=buffer_days)
            # An overdue or imminent task cannot have buffer it does not have.
            latest = max(today, min(latest, hard_latest))

        for i, minutes in enumerate(chunks, start=1):
            items.append(
                _Item(
                    task=task,
                    minutes=minutes,
                    part_index=i,
                    part_total=len(chunks),
                    estimate=estimate,
                    earliest=today,
                    latest=latest,
                    hard_latest=hard_latest,
                )
            )

    _apply_dependencies(items, days)
    return items


def _apply_dependencies(items: list[_Item], days: Sequence[date]) -> None:
    """Tighten windows so prerequisites cannot be scheduled after dependents.

    One pass of constraint propagation: a dependent's earliest day moves
    forward past its prerequisite's latest, and a prerequisite's latest
    moves back before its dependent's. Cycles are ignored rather than
    diagnosed — a cycle in student-entered dependencies is a data problem,
    not something to fail a whole plan over.
    """
    by_task: dict[str, list[_Item]] = {}
    for it in items:
        by_task.setdefault(it.task.id, []).append(it)

    for task_id, group in by_task.items():
        for dep_id in group[0].task.depends_on:
            prereq = by_task.get(dep_id)
            if not prereq:
                continue
            prereq_latest = min(i.latest for i in group)
            for p in prereq:
                p.latest = min(p.latest, max(p.earliest, prereq_latest - timedelta(days=1)))
                p.hard_latest = min(p.hard_latest, prereq_latest)
            prereq_earliest = min(p.earliest for p in prereq)
            for g in group:
                g.earliest = max(g.earliest, prereq_earliest)
                if g.earliest > g.latest:
                    g.latest = min(g.hard_latest, g.earliest)


def _assign(
    items: list[_Item],
    days: Sequence[date],
    state: dict[date, _DayState],
    config: PlannerConfig,
) -> None:
    """Greedy seed: place the most constrained work first.

    Ordering matters more than the cost function here. Sorting by deadline
    then priority means the work with the least freedom claims its days
    before the work that can go anywhere, which is what stops a low-stakes
    task from occupying the only slot an exam had.

    Items that find no feasible day are left unplaced with a reason rather
    than forced somewhere — the caller retries them once local search has
    rearranged what is already down.
    """
    ordered = sorted(
        items,
        key=lambda it: (
            it.hard_latest,
            -it.task.priority,
            it.task.id,
            it.part_index,
        ),
    )
    for item in ordered:
        _place_best(item, days, state, config)


def _place_best(
    item: _Item,
    days: Sequence[date],
    state: dict[date, _DayState],
    config: PlannerConfig,
) -> bool:
    """Put ``item`` on its cheapest feasible day. False when none exists."""
    candidates = [d for d in days if item.earliest <= d <= item.hard_latest]
    if not candidates:
        item.defer_reason = "No day left before the deadline."
        return False
    best_day, best_cost = None, math.inf
    for d in candidates:
        cost = _placement_cost(item, d, state[d], len(days), config.weights)
        if cost < best_cost:
            best_day, best_cost = d, cost
    if best_day is None or best_cost == math.inf:
        item.defer_reason = "Every day before the deadline is already full."
        return False
    item.day = best_day
    item.defer_reason = ""
    state[best_day].add(item)
    return True


def _retry_deferred(
    items: list[_Item],
    days: Sequence[date],
    state: dict[date, _DayState],
    config: PlannerConfig,
) -> None:
    """Second chance for unplaced work, hardest-constrained first."""
    unplaced = sorted(
        (it for it in items if it.day is None),
        key=lambda it: (it.hard_latest, -it.task.priority, it.task.id, it.part_index),
    )
    for item in unplaced:
        _place_best(item, days, state, config)


def _optimise(
    items: list[_Item],
    days: Sequence[date],
    state: dict[date, _DayState],
    config: PlannerConfig,
) -> None:
    """Local search: relocate sessions while it lowers total cost.

    The greedy seed is myopic — it prices each session against the days as
    they looked when it was placed, and the last placements change what the
    first ones should have been. A few relocation passes recover most of
    that. It converges quickly because each pass only accepts strict
    improvements.
    """
    horizon = len(days)
    # Each pass costs O(items × days), so a student with hundreds of open
    # assignments pays quadratically for refinement that has long since
    # stopped changing anything. Spend the full budget where it matters and
    # taper it on very large workloads — the greedy seed is already sorted
    # by deadline, so the marginal pass is worth least exactly when it costs
    # most.
    passes = max(0, config.optimise_passes)
    if len(items) > 150:
        passes = min(passes, 2)
    elif len(items) > 80:
        passes = min(passes, 4)

    for _ in range(passes):
        improved = False
        for item in items:
            if item.day is None:
                continue
            current = state[item.day]
            current.remove(item)
            base_cost = _placement_cost(item, item.day, current, horizon, config.weights)
            best_day, best_cost = item.day, base_cost
            for d in days:
                if d == item.day or not (item.earliest <= d <= item.hard_latest):
                    continue
                cost = _placement_cost(item, d, state[d], horizon, config.weights)
                if cost < best_cost - 1e-9:
                    best_day, best_cost = d, cost
            if best_day != item.day:
                item.day = best_day
                improved = True
            state[best_day].add(item)
        if not improved:
            break


#: How fast a task's marginal sitting loses value. Higher = the planner
#: spreads scarce time across more tasks instead of finishing a few.
_MARGINAL_DECAY = 0.55


def _value(item: _Item) -> float:
    """How much this sitting is worth keeping when something must go.

    Two ideas, and the second is the one that matters. First, importance
    and deadline proximity both count — dropping a low-stakes worksheet due
    next month is cheaper than dropping one due tomorrow at equal priority.

    Second, and less obvious: a task's sittings have **diminishing value**.
    Straight priority ordering says every sitting of a 95-priority midterm
    outranks every other assignment, so a tight week gets spent entirely on
    one exam while five other deadlines pass untouched. But the first thirty
    minutes of revision are worth far more than the eighth thirty, and the
    first thirty minutes of every *other* subject are worth more still.
    Decaying by position within the task is what makes scarce time spread
    across the workload instead of pooling on the loudest item.
    """
    days_out = max(0, (item.hard_latest - item.earliest).days)
    urgency = max(0.0, 30.0 - min(days_out, 30)) / 30.0
    base = 0.6 * (item.task.priority / 100.0) + 0.4 * urgency
    return round(1000.0 * base / (1.0 + _MARGINAL_DECAY * (item.part_index - 1)), 4)


def _triage(
    items: list[_Item],
    days: Sequence[date],
    state: dict[date, _DayState],
    config: PlannerConfig,
) -> list[_Item]:
    """Trade low-value placed work for high-value work that would not fit.

    Without this, "what gets dropped" is decided by the greedy pass's
    ordering, which sorts by deadline — so a 30-minute vocabulary set due
    tomorrow survives and half a midterm does not. When capacity is the
    binding constraint the student needs the *important* work protected,
    and that is a different question from which deadline is nearest.

    Each swap is checked for feasibility, and evicted work is put back on
    the unplaced pile so it is reported honestly rather than deleted.
    """
    unplaced = [it for it in items if it.day is None]
    if not unplaced:
        return items

    # Index placed work by day once. Rebuilding this by scanning every item
    # for every candidate day made triage quadratic in the workload, which a
    # student with a few hundred open assignments felt as a multi-second
    # wait — and they are exactly the student who most needs this pass.
    by_day: dict[date, list[_Item]] = {d: [] for d in days}
    for it in items:
        if it.day is not None:
            by_day.setdefault(it.day, []).append(it)

    # Values are stable during triage and each is computed many times.
    values = {id(it): _value(it) for it in items}

    unplaced.sort(key=lambda it: values[id(it)], reverse=True)
    for item in unplaced:
        if item.day is not None:
            continue
        item_value = values[id(item)]
        candidates = [d for d in days if item.earliest <= d <= item.hard_latest]
        best: tuple[float, date, list[_Item]] | None = None
        for d in candidates:
            st = state[d]
            need = item.minutes - (st.capacity - st.minutes)
            if need <= 0:
                continue  # _place_best already had its chance; it fits nowhere else
            # Cheapest set of strictly-less-valuable sittings that frees room.
            evictable = sorted(
                (o for o in by_day.get(d, ()) if values[id(o)] < item_value),
                key=lambda o: values[id(o)],
            )
            freed, chosen = 0, []
            for victim in evictable:
                chosen.append(victim)
                freed += victim.minutes
                if freed >= need:
                    break
            if freed < need:
                continue
            sacrifice = sum(values[id(v)] for v in chosen)
            if best is None or sacrifice < best[0]:
                best = (sacrifice, d, chosen)

        if best is None:
            continue
        _, day, victims = best
        for victim in victims:
            state[day].remove(victim)
            by_day[day].remove(victim)
            victim.day = None
            victim.defer_reason = (
                f"Displaced by higher-priority work ({item.task.title})."
            )
        if not _place_best(item, days, state, config):
            # Undo — never leave the plan worse than we found it.
            for victim in victims:
                victim.day = day
                victim.defer_reason = ""
                state[day].add(victim)
                by_day[day].append(victim)
        elif item.day is not None:
            by_day.setdefault(item.day, []).append(item)

    # Evicted work may fit elsewhere. This runs once, after every swap has
    # been decided — doing it inside the loop above re-scanned the whole
    # unplaced pile on every single swap for no additional placements.
    _retry_deferred(items, days, state, config)
    return items


def _coalesce(items: list[_Item]) -> list[_Item]:
    """Merge a task's sittings that ended up on the same day.

    Splitting exists to respect focus length and to space repetition across
    days. Two parts back-to-back on one afternoon deliver neither — they are
    one block of work wearing two labels, and the clock-level placer already
    inserts breaks inside long blocks based on measured stamina.
    """
    merged: list[_Item] = []
    seen: dict[tuple[str, date], _Item] = {}
    for item in items:
        if item.day is None:
            merged.append(item)
            continue
        key = (item.task.id, item.day)
        existing = seen.get(key)
        if existing is None:
            seen[key] = item
            merged.append(item)
            continue
        existing.minutes += item.minutes
    return merged


def _renumber(items: Sequence[_Item]) -> None:
    """Number a task's sittings in the order the student will meet them.

    The optimizer places parts in cost order, not chronological order, so
    without this a plan cheerfully shows "part 7 of 8" on Thursday above
    "part 1 of 8" on Friday.
    """
    by_task: dict[str, list[_Item]] = {}
    for item in items:
        if item.day is not None:
            by_task.setdefault(item.task.id, []).append(item)
    for group in by_task.values():
        group.sort(key=lambda it: it.day or date.max)
        for index, item in enumerate(group, start=1):
            item.part_index = index
            item.part_total = len(group)


def _materialise(
    items: list[_Item],
    days: Sequence[date],
    cap_by_day: Mapping[date, DayCapacity],
    state: Mapping[date, _DayState],
    config: PlannerConfig,
) -> Plan:
    """Turn the solved assignment into the frozen output shape."""
    by_day: dict[date, list[Session]] = {d: [] for d in days}
    deferred: list[Deferral] = []
    total = 0

    # One row per task, not one per unplaced sitting. "APUSH essay: 112 min
    # short" is a fact a student can act on; ten identical lines is noise.
    unplaced: dict[str, list[_Item]] = {}
    for item in items:
        if item.day is None:
            unplaced.setdefault(item.task.id, []).append(item)
            continue
        total += item.minutes

    for group in unplaced.values():
        head = group[0]
        deferred.append(
            Deferral(
                task_id=head.task.id,
                title=head.task.title,
                minutes=sum(i.minutes for i in group),
                due_date=head.task.due_date,
                reason=head.defer_reason or "Did not fit in the available time.",
            )
        )

    for item in items:
        if item.day is None:
            continue
        by_day[item.day].append(
            Session(
                task_id=item.task.id,
                title=item.task.title,
                course=item.task.course,
                kind=item.task.kind,
                day=item.day,
                minutes=item.minutes,
                part_index=item.part_index,
                part_total=item.part_total,
                difficulty=item.task.difficulty,
                priority=item.task.priority,
                due_date=item.task.due_date,
                reasons=_reasons_for(item, state[item.day]),
                estimate_ratio=item.estimate.ratio,
                estimate_confidence=item.estimate.confidence,
            )
        )

    day_plans: list[DayPlan] = []
    for d in days:
        sessions = sorted(
            by_day[d],
            key=lambda s: (
                s.due_date or date.max,
                -s.priority,
                s.task_id,
                s.part_index,
            ),
        )
        day_plans.append(
            DayPlan(
                day=d,
                sessions=tuple(sessions),
                capacity_minutes=cap_by_day[d].usable,
                scheduled_minutes=sum(s.minutes for s in sessions),
            )
        )

    capacity = sum(cap_by_day[d].usable for d in days)
    notes = _notes(day_plans, deferred, total, capacity, config)
    # Any single day past its real ceiling is an overloaded plan even when
    # the fortnight's totals balance — the student lives in days, not totals.
    over_a_day = any(d.scheduled_minutes > d.capacity_minutes for d in day_plans)

    return Plan(
        days=tuple(day_plans),
        deferred=tuple(deferred),
        overloaded=bool(deferred) or over_a_day or total > capacity,
        total_minutes=total,
        capacity_minutes=capacity,
        notes=tuple(notes),
    )


def _reasons_for(item: _Item, state: _DayState) -> tuple[str, ...]:
    """The explanation the student sees next to a session."""
    out: list[str] = []
    task = item.task
    is_last = item.part_index >= item.part_total

    if task.due_date is not None:
        gap = (task.due_date - item.day).days
        if gap <= 0:
            out.append("Due today — this is the last chance to work on it.")
        elif gap == 1:
            out.append("Due tomorrow.")
        elif is_last:
            # Only the final sitting "finishes" anything. Saying it on every
            # part told the student four different completion dates.
            out.append(f"Finishes {gap} days before it's due.")
        else:
            out.append(f"Due in {gap} days.")

    if item.part_total > 1:
        why = (
            "spaced out so it sticks"
            if task.kind in MEMORY_KINDS
            else "split to match your focus length"
        )
        out.append(f"Sitting {item.part_index} of {item.part_total} — {why}.")

    if item.estimate.confidence >= 0.3 and abs(item.estimate.ratio - 1.0) >= 0.12:
        pct = int(round(abs(item.estimate.ratio - 1.0) * 100))
        word = "longer" if item.estimate.ratio > 1 else "less time"
        out.append(f"Sized from your history: you take ~{pct}% {word} on this kind of work.")

    if task.priority >= 80:
        out.append("High priority — placed early on purpose.")

    if state.capacity and state.minutes <= state.capacity * 0.5:
        out.append("Landed here because this day was your lightest option.")

    if task.weak_concept_penalty >= 0.4:
        out.append("Covers a concept you've struggled with before — extra time reserved.")

    return tuple(out)


def _notes(
    day_plans: Sequence[DayPlan],
    deferred: Sequence[Deferral],
    total: int,
    capacity: int,
    config: PlannerConfig,
) -> list[str]:
    notes: list[str] = []
    if deferred:
        minutes = sum(d.minutes for d in deferred)
        notes.append(
            f"{minutes} minutes of work could not fit before its deadlines. "
            f"You are over capacity — drop, shorten, or find more time."
        )
    if capacity and total > capacity * 0.9 and not deferred:
        notes.append("This is a full two weeks. There is very little slack left.")
    heavy = [d for d in day_plans if d.capacity_minutes and d.utilisation > 1.0]
    if heavy:
        names = ", ".join(d.day.strftime("%a %d") for d in heavy[:3])
        notes.append(f"Heavier than usual: {names}.")
    return notes


# ── Adaptive rescheduling ─────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Reality:
    """What actually happened, against what was planned.

    ``completed`` maps ``task_id → minutes actually spent and finished``.
    ``missed`` are sessions the student did not do at all. ``abandoned``
    maps ``task_id → minutes spent before quitting`` — partial credit, which
    is the difference between "you did nothing" and "you did 20 minutes".
    """

    completed: Mapping[str, int] = field(default_factory=dict)
    abandoned: Mapping[str, int] = field(default_factory=dict)
    missed_task_ids: frozenset[str] = frozenset()
    finished_task_ids: frozenset[str] = frozenset()


def reschedule(
    tasks: Sequence[PlannerTask],
    capacities: Sequence[DayCapacity],
    reality: Reality,
    model: EstimationModel | None = None,
    today: date | None = None,
    config: PlannerConfig | None = None,
) -> Plan:
    """Rebuild the plan from where the student actually is.

    The naive recovery — push everything that slipped onto tomorrow — is
    worse than no recovery, because it produces a day nobody can do and
    then a second missed day. Instead we credit the work that got done,
    drop what is finished, and re-solve the whole remaining horizon with
    the same cost function. Balancing, spacing, and buffer all still apply;
    the student simply has less room, and the optimizer reallocates
    accordingly.

    When the remaining work genuinely does not fit, the plan says so
    (``overloaded``) rather than inventing hours.
    """
    config = config or PlannerConfig()
    remaining: list[PlannerTask] = []

    for task in tasks:
        if task.id in reality.finished_task_ids:
            continue
        spent = int(reality.completed.get(task.id, 0)) + int(reality.abandoned.get(task.id, 0))
        if spent:
            task = replace(task, done_minutes=task.done_minutes + spent)
        if task.id in reality.missed_task_ids:
            # Missing a session is evidence about this task, not a reason to
            # panic-schedule it: bump its priority so the optimizer pulls it
            # earlier, and let the cost function decide where earlier is.
            task = replace(task, priority=min(100, task.priority + 12))
        remaining.append(task)

    return build_plan(remaining, capacities, model=model, today=today, config=config)


# ── Adapters ──────────────────────────────────────────────────────────


def task_from_mapping(row: Mapping[str, Any]) -> PlannerTask:
    """Build a :class:`PlannerTask` from a loose dict.

    The API and the LLM both hand us dicts with drifting key names; this is
    the one place that reconciles them.
    """
    due = row.get("due_date") or row.get("due")
    if isinstance(due, str):
        try:
            due = date.fromisoformat(due[:10])
        except ValueError:
            due = None
    if not isinstance(due, date):
        due = None

    depends = row.get("depends_on") or ()
    if isinstance(depends, str):
        depends = tuple(d.strip() for d in depends.split(",") if d.strip())

    concepts = row.get("concepts") or ()
    if isinstance(concepts, str):
        concepts = tuple(c.strip() for c in concepts.split(",") if c.strip())

    return PlannerTask(
        id=str(row.get("id") or row.get("task_id") or row.get("title") or "task"),
        title=str(row.get("title") or row.get("assignment") or "Task"),
        course=str(row.get("course") or ""),
        kind=str(row.get("kind") or row.get("type") or "homework").strip().lower(),
        due_date=due,
        est_minutes=_int_or_none(row.get("est_minutes") or row.get("duration_minutes")),
        difficulty=str(row.get("difficulty") or "medium").strip().lower(),
        priority=_clamp_int(row.get("priority"), 50, 0, 100),
        points_possible=_float_or_none(row.get("points_possible")),
        subtask_count=_clamp_int(row.get("subtask_count"), 0, 0, 50),
        depends_on=tuple(str(d) for d in depends),
        done_minutes=_clamp_int(row.get("done_minutes"), 0, 0, 10_000),
        concepts=tuple(str(c) for c in concepts),
        weak_concept_penalty=_clamp_float(row.get("weak_concept_penalty"), 0.0, 0.0, 1.0),
    )


def _int_or_none(value: Any) -> int | None:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _clamp_float(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


def capacities_from_minutes(
    start: date,
    per_day: Iterable[int],
    quality: Mapping[date, float] | None = None,
) -> list[DayCapacity]:
    """Convenience for tests and for callers that already know free minutes."""
    out: list[DayCapacity] = []
    for offset, minutes in enumerate(per_day):
        day = start + timedelta(days=offset)
        out.append(DayCapacity(day=day, minutes=int(minutes), quality=(quality or {}).get(day, 1.0)))
    return out
