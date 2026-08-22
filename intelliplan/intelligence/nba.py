"""Next-Best-Action — what should this student do right now?

Everything else in the scheduler answers a question about a *week*. This
module answers the question a student actually asks, at the moment they ask
it, with a phone in their hand and forty minutes before dinner:

    What should I work on right now, and why that?

The answer is not "the top row of the plan". The plan was built for the week;
the student has 40 minutes, not the 75 the next block wants, they have already
worked two hours today, and the thing the plan ranks second is the one whose
quiz is on Thursday. Reading the top row would be a lookup, not a decision.

How it works
------------
Two separable halves, so each can be tested and changed on its own:

1. **Generation** — cheap, permissive, and deliberately heterogeneous. Study
   blocks, overdue work, weak concepts before an assessment, material decaying
   on the forgetting curve, a break, deferring something low-value. A generator
   that can only ever produce "do more work" cannot recommend a break when a
   break is right.
2. **Scoring** — one documented linear objective over normalised components,
   with configurable weights (:class:`NBAWeights`). Every component the scorer
   uses is either measured or explicitly defaulted; nothing is invented, and
   the per-component contributions ride along on the result so the "why" is
   the arithmetic rather than a story told afterwards.

The LLM is not in this path. It may later *narrate* the result, but it never
produces the ranking, the score, or the numbers behind it.

Purity
------
No Flask, no ORM, no clock reads. ``now`` is passed in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from intelliplan.domain.student import (
    ActionCandidate,
    ActionKind,
    MasteryEstimate,
    NextAction,
    identity_key,
)
from intelliplan.intelligence import methods as methods_engine
from intelliplan.intelligence.behavior import (
    POPULATION_COMPLETION_RATE,
    BehaviorModel,
    slot_for_hour,
)

__all__ = [
    "NBAWeights",
    "NBAContext",
    "generate",
    "score",
    "decide",
    "REASON_LABELS",
]


# ── Weights ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class NBAWeights:
    """The objective, in numbers.

    Positive terms are what we are buying; negative terms are what we are
    paying. The ratios encode the product's actual priorities, in the order
    the brief states them: not missing deadlines beats improving weak
    concepts, which beats a tidy day.

    Nothing here is tuned per student *yet*, and the fact that it is a
    constructor argument rather than module constants is the whole point —
    when there is enough data to personalise the weights, nothing in this
    module has to change.
    """

    deadline: float = 1.60
    academic_value: float = 1.00
    mastery_gap: float = 0.85
    learning_gain: float = 0.60
    completion: float = 0.90
    schedule_fit: float = 0.70
    continuity: float = 0.35

    time_cost: float = 0.30
    stress: float = 0.75
    context_switch: float = 0.30

    @property
    def positive_mass(self) -> float:
        return (
            self.deadline
            + self.academic_value
            + self.mastery_gap
            + self.learning_gain
            + self.completion
            + self.schedule_fit
            + self.continuity
        )

    @property
    def negative_mass(self) -> float:
        return self.time_cost + self.stress + self.context_switch


#: Stable machine keys → the sentence a student reads. Reason codes are
#: generated from measured facts, so this table is the *only* place the
#: wording lives; nothing downstream writes its own version.
REASON_LABELS: dict[str, str] = {
    "overdue": "This is already past its due date.",
    "due_today": "This is due today.",
    "due_tomorrow": "This is due tomorrow.",
    "assessment_near": "Your assessment on this is within three days.",
    "low_mastery": "Your estimated grasp of this is still shaky.",
    "high_academic_value": "This carries a large share of the course grade.",
    "decaying": "You have not touched this in a while and it is fading.",
    "fits_the_gap": "It fits the time you actually have right now.",
    "your_best_time": "You finish this kind of work most reliably at this hour.",
    "already_started": "You are part-way through this already.",
    "same_subject": "You are already in this subject — no context switch.",
    "long_stretch": "You have been working a long stretch without a break.",
    "over_capacity": "Today is already past what you usually sustain.",
    "low_completion_odds": "You historically do not finish sittings like this one.",
    "plan_says_today": "Your plan puts this on today.",
    "little_time_left": "There is not much of today's study time left.",
}


# ── Context ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class NBAContext:
    """Everything the engine is allowed to reason from.

    Anything not in here does not exist as far as the decision is concerned.
    That is the point: it makes the input to a recommendation enumerable, and
    therefore makes a recommendation reproducible from a log line.
    """

    now: datetime
    #: Minutes of usable study time between now and the end of the student's
    #: availability today. Zero means "no time left today", which is a real
    #: answer and produces a different recommendation, not an empty one.
    available_minutes: int
    behavior: BehaviorModel | None = None
    #: Minutes already worked today, and minutes of unbroken work just now.
    worked_today_minutes: int = 0
    continuous_minutes: int = 0
    #: The course of the sitting the student was last in, if any.
    current_course: str = ""
    #: Task ids the student has started but not finished.
    in_progress_task_ids: frozenset[str] = frozenset()
    #: Task ids the student has already declined today. "Not now" has to
    #: mean something, or the card re-offers the thing they just pushed away
    #: and the student is arguing with a machine that does not listen.
    dismissed_task_ids: frozenset[str] = frozenset()
    weights: NBAWeights = field(default_factory=NBAWeights)

    @property
    def today(self) -> date:
        return self.now.date()

    @property
    def slot(self) -> str:
        return slot_for_hour(self.now.hour)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


# ── Generation ───────────────────────────────────────────────────────


#: Continuous minutes past which a break outscores more work. Sourced from
#: ``scheduler_engine.LONG_BREAK_AFTER_MINUTES`` so the two surfaces agree
#: about what "a long stretch" is.
BREAK_AFTER_MINUTES = 90

#: A gap shorter than this holds a review, not a work block.
MIN_WORK_GAP_MINUTES = 15


def _deadline_pressure(due: date | None, today: date) -> float:
    """0..1 urgency from a due date.

    Overdue is 1.0 and stays there — there is no "more overdue". The curve
    below that is deliberately steep in the first three days and flat after
    a week, which is how deadlines actually feel.
    """
    if due is None:
        return 0.15  # undated work is not urgent, but it is not weightless
    days = (due - today).days
    if days <= 0:
        return 1.0
    if days == 1:
        return 0.85
    if days == 2:
        return 0.70
    if days == 3:
        return 0.55
    if days <= 7:
        return 0.35
    if days <= 14:
        return 0.20
    return 0.10


def generate(
    ctx: NBAContext,
    *,
    planned_today: Sequence[Mapping[str, Any]] = (),
    assignments: Sequence[Mapping[str, Any]] = (),
    mastery: Sequence[MasteryEstimate] = (),
) -> list[ActionCandidate]:
    """Produce the things the student could reasonably do next.

    ``planned_today`` are block dicts from ``schedule_data``; ``assignments``
    are the loose rows the app already produces (title, course, due_date,
    priority, est_minutes, points_possible, status). Both are read
    defensively — this runs on live data from eight upstream sources.
    """
    out: list[ActionCandidate] = []
    today = ctx.today
    seen_tasks: set[str] = set()

    # ── 1. today's planned work ─────────────────────────────────────
    for block in planned_today:
        if not isinstance(block, Mapping) or block.get("is_break"):
            continue
        if block.get("done") or block.get("completed"):
            continue
        task_id = str(block.get("task_id") or block.get("id") or "")
        title = str(block.get("parent_title") or block.get("assignment") or "").strip()
        if not title:
            continue
        minutes = _int(block.get("duration_minutes"), 30)
        due = _as_date(block.get("due_date"))
        started = task_id in ctx.in_progress_task_ids
        if task_id:
            seen_tasks.add(task_id)
        out.append(
            ActionCandidate(
                kind=ActionKind.CONTINUE_TASK if started else ActionKind.START_TASK,
                title=title,
                course=str(block.get("course") or ""),
                subject=str(block.get("course") or ""),
                minutes=minutes,
                task_id=task_id,
                due_date=due,
                academic_value=_clamp01(_int(block.get("priority"), 50) / 100.0),
                learning_gain=0.5,
                deadline_pressure=_deadline_pressure(due, today),
                difficulty=_difficulty(block.get("difficulty")),
                metadata={
                    "source": "plan",
                    "block_id": str(block.get("id") or ""),
                    # Carried so the method engine can tell an essay sitting
                    # from a quiz revision sitting. Without it every block
                    # gets the generic homework ladder.
                    "kind": str(block.get("kind") or ""),
                },
            )
        )

    # ── 2. work the plan does not cover but the calendar demands ────
    # Overdue and due-today items that are not on today's plan are the single
    # most common way a plan and reality diverge, and the plan is the one
    # that is wrong.
    for row in assignments:
        if not isinstance(row, Mapping):
            continue
        status = str(row.get("status") or "").lower()
        if status in ("completed", "dismissed", "done"):
            continue
        task_id = str(row.get("id") or row.get("task_id") or "")
        if task_id and task_id in seen_tasks:
            continue
        due = _as_date(row.get("due_date"))
        if due is None or (due - today).days > 1:
            continue
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        out.append(
            ActionCandidate(
                kind=ActionKind.START_TASK,
                title=title,
                course=str(row.get("course") or ""),
                subject=str(row.get("course") or ""),
                minutes=_int(row.get("est_minutes"), 30),
                task_id=task_id,
                due_date=due,
                academic_value=_clamp01(_int(row.get("priority"), 50) / 100.0),
                learning_gain=0.4,
                deadline_pressure=_deadline_pressure(due, today),
                metadata={
                    "source": "assignments",
                    "kind": str(row.get("kind") or ""),
                },
            )
        )

    # ── 3. concepts: weak-before-assessment, and decaying material ──
    for est in mastery:
        gap = _clamp01(1.0 - est.mastery)
        near = est.assessment_days_away is not None and est.assessment_days_away <= 5
        stale = est.days_since_review is not None and est.days_since_review >= 7
        if not (near or (stale and est.is_weak)):
            continue
        kind = ActionKind.EXAM_PREP if near else ActionKind.REVIEW_CONCEPT
        minutes = min(45, max(20, ctx.available_minutes)) if near else 20
        out.append(
            ActionCandidate(
                kind=kind,
                title=f"{est.concept or est.topic}".strip() or est.subject,
                subject=est.subject,
                course=est.subject,
                concept=est.concept,
                minutes=minutes,
                due_date=(
                    today + timedelta(days=est.assessment_days_away)
                    if est.assessment_days_away is not None
                    else None
                ),
                academic_value=_clamp01(est.risk),
                # Retrieval on weak material is where study time buys the most
                # learning, which is a different quantity from what it buys in
                # grade terms — hence a separate component.
                learning_gain=_clamp01(0.45 + 0.5 * gap),
                mastery_gap=gap,
                deadline_pressure=(
                    _deadline_pressure(
                        today + timedelta(days=est.assessment_days_away), today
                    )
                    if est.assessment_days_away is not None
                    else 0.2
                ),
                assessment_days_away=est.assessment_days_away,
                metadata={"source": "mastery", "confidence": f"{est.confidence:.2f}"},
            )
        )

    # ── 4. a break ──────────────────────────────────────────────────
    if ctx.continuous_minutes >= BREAK_AFTER_MINUTES:
        out.append(
            ActionCandidate(
                kind=ActionKind.BREAK,
                title="Take a break",
                minutes=15,
                academic_value=0.0,
                learning_gain=0.0,
                deadline_pressure=0.0,
                metadata={"source": "behaviour"},
            )
        )

    return out


# ── Scoring ──────────────────────────────────────────────────────────


def score(candidate: ActionCandidate, ctx: NBAContext) -> NextAction:
    """Score one candidate. Deterministic and fully explained.

    Every component is 0..1 before weighting, so the weights mean what they
    say and the contributions are directly comparable in the output.
    """
    w = ctx.weights
    reasons: list[str] = []
    parts: list[tuple[str, float]] = []

    # A break is scored on its own terms rather than being forced through an
    # objective built for work. Pretending it competes on academic value
    # would guarantee it never wins, which is the same as not having it.
    if candidate.kind is ActionKind.BREAK:
        return _score_break(candidate, ctx)

    fit, fit_reasons = _schedule_fit(candidate, ctx)
    reasons.extend(fit_reasons)

    completion = _completion(candidate, ctx)
    if completion < 0.45:
        reasons.append("low_completion_odds")

    continuity = 1.0 if (
        candidate.course
        and ctx.current_course
        and candidate.course.lower() == ctx.current_course.lower()
    ) else 0.0
    if continuity:
        reasons.append("same_subject")

    switch = 0.0 if continuity or not ctx.current_course else 1.0

    time_cost = _clamp01(
        candidate.minutes / max(1, ctx.available_minutes)
    ) if ctx.available_minutes > 0 else 1.0

    stress = _stress(ctx)
    if stress > 0.6:
        reasons.append("over_capacity")

    parts.append(("deadline", w.deadline * candidate.deadline_pressure))
    parts.append(("academic_value", w.academic_value * candidate.academic_value))
    parts.append(("mastery_gap", w.mastery_gap * candidate.mastery_gap))
    parts.append(("learning_gain", w.learning_gain * candidate.learning_gain))
    parts.append(("completion", w.completion * completion))
    parts.append(("schedule_fit", w.schedule_fit * fit))
    parts.append(("continuity", w.continuity * continuity))
    parts.append(("time_cost", -w.time_cost * time_cost))
    parts.append(("stress", -w.stress * stress))
    parts.append(("context_switch", -w.context_switch * switch))

    raw = sum(v for _, v in parts)
    normalised = _clamp01((raw + w.negative_mass) / (w.positive_mass + w.negative_mass))

    reasons.extend(_deadline_reasons(candidate, ctx.today))
    if candidate.mastery_gap >= 0.4:
        reasons.append("low_mastery")
    if candidate.academic_value >= 0.75:
        reasons.append("high_academic_value")
    if candidate.assessment_days_away is not None and candidate.assessment_days_away <= 3:
        reasons.append("assessment_near")
    if candidate.kind is ActionKind.CONTINUE_TASK:
        reasons.append("already_started")
    if candidate.kind is ActionKind.REVIEW_CONCEPT:
        reasons.append("decaying")
    if candidate.metadata.get("source") == "plan":
        reasons.append("plan_says_today")

    method = methods_engine.recommend(
        mastery=(1.0 - candidate.mastery_gap) if candidate.mastery_gap else None,
        confidence=_mastery_confidence(candidate),
        minutes=min(candidate.minutes, max(5, ctx.available_minutes)),
        kind=_method_kind(candidate),
        assessment_days_away=candidate.assessment_days_away,
    )

    ordered = _rank_reasons(reasons)
    return NextAction(
        candidate=candidate,
        score=round(normalised, 4),
        confidence=round(_confidence(candidate, ctx, completion), 3),
        reason_codes=ordered,
        components=tuple((k, round(v, 4)) for k, v in parts),
        completion_probability=round(completion, 3),
        method=method.summary,
        explanation=tuple(REASON_LABELS[r] for r in ordered if r in REASON_LABELS),
    )


def _score_break(candidate: ActionCandidate, ctx: NBAContext) -> NextAction:
    """A break's value is entirely a function of how long they have worked.

    It ramps from "not yet" at the break threshold to "clearly now" at twice
    it, so a break only outranks real work once the evidence for it is real.
    """
    over = max(0, ctx.continuous_minutes - BREAK_AFTER_MINUTES)
    value = _clamp01(0.55 + over / (BREAK_AFTER_MINUTES * 2.0))
    reasons = ("long_stretch",)
    return NextAction(
        candidate=candidate,
        score=round(value, 4),
        confidence=0.8,
        reason_codes=reasons,
        components=(("fatigue", round(value, 4)),),
        completion_probability=1.0,
        method="",
        explanation=tuple(REASON_LABELS[r] for r in reasons),
    )


def _schedule_fit(candidate: ActionCandidate, ctx: NBAContext) -> tuple[float, list[str]]:
    """How well this action fits the time and the hour actually available."""
    reasons: list[str] = []
    if ctx.available_minutes <= 0:
        return 0.0, ["little_time_left"]

    ratio = candidate.minutes / ctx.available_minutes
    if ratio <= 1.0:
        # Filling the gap well is better than barely touching it: a 40-minute
        # gap is better spent on a 35-minute block than a 10-minute one.
        fit = 0.6 + 0.4 * ratio
        if ratio >= 0.5:
            reasons.append("fits_the_gap")
    elif ratio <= 1.5:
        # It can be started and carried over. Real, but worse than fitting.
        fit = 0.45
    else:
        fit = 0.2

    if ctx.behavior is not None:
        best = ctx.behavior.best_slot()
        if best and best == ctx.slot:
            fit = _clamp01(fit + 0.15)
            reasons.append("your_best_time")
    return _clamp01(fit), reasons


def _completion(candidate: ActionCandidate, ctx: NBAContext) -> float:
    if ctx.behavior is None:
        return POPULATION_COMPLETION_RATE
    minutes = min(candidate.minutes, max(5, ctx.available_minutes))
    return ctx.behavior.completion_probability(
        course=candidate.course, slot=ctx.slot, minutes=minutes
    ).probability


def _stress(ctx: NBAContext) -> float:
    """How loaded today already is, against what this student sustains."""
    tolerance = (
        ctx.behavior.workload_tolerance_minutes if ctx.behavior is not None else 150
    )
    return _clamp01(ctx.worked_today_minutes / max(1, tolerance))


def _confidence(candidate: ActionCandidate, ctx: NBAContext, completion: float) -> float:
    """How much we trust this recommendation.

    Three independent things have to hold for a recommendation to be
    trustworthy: we know something about *the student* (behavioural
    evidence), we know something about *the work* (a due date, a mastery
    estimate — not a bare title), and the odds we computed are not a
    coin-flip. The weakest of the three caps the result, because confidence
    is not an average — one unknown is enough to make the answer a guess.
    """
    if ctx.behavior is None:
        behavioural = 0.3
    else:
        behavioural = ctx.behavior.completion_probability(
            course=candidate.course, slot=ctx.slot, minutes=candidate.minutes
        ).evidence.confidence

    known = 0.35
    if candidate.due_date is not None:
        known += 0.3
    if candidate.mastery_gap > 0.0:
        known += 0.2
    if candidate.metadata.get("source") == "plan":
        known += 0.15
    known = _clamp01(known)

    decisive = _clamp01(abs(completion - 0.5) * 2.0) * 0.5 + 0.5

    return _clamp01(min(behavioural, known, decisive) * 0.6 + (behavioural + known) / 2 * 0.4)


def _deadline_reasons(candidate: ActionCandidate, today: date) -> list[str]:
    if candidate.due_date is None:
        return []
    days = (candidate.due_date - today).days
    if days < 0:
        return ["overdue"]
    if days == 0:
        return ["due_today"]
    if days == 1:
        return ["due_tomorrow"]
    return []


def _mastery_confidence(candidate: ActionCandidate) -> float:
    try:
        return float(candidate.metadata.get("confidence", "0"))
    except (TypeError, ValueError):
        return 0.0


def _method_kind(candidate: ActionCandidate) -> str:
    if candidate.kind in (ActionKind.EXAM_PREP, ActionKind.REVIEW_CONCEPT):
        return "test"
    if candidate.kind is ActionKind.ADMIN:
        return "administrative"
    meta_kind = candidate.metadata.get("kind")
    return meta_kind or "homework"


#: Reasons in the order a student cares about them. The card shows the first
#: four, so this decides what they actually read.
#:
#: The scorer appends reasons in the order it happens to compute them, which
#: put "there is not much of today's study time left" above "this is due
#: tomorrow" — schedule mechanics ahead of the deadline that is the actual
#: reason to start. Ordering by how the arithmetic runs is not ordering.
_REASON_RANK: dict[str, int] = {
    # 1. Something is about to go wrong.
    "overdue": 0,
    "due_today": 1,
    "due_tomorrow": 2,
    "assessment_near": 3,
    # 2. Why this is worth the time.
    "high_academic_value": 10,
    "low_mastery": 11,
    "decaying": 12,
    # 3. Why now specifically.
    "already_started": 20,
    "same_subject": 21,
    "your_best_time": 22,
    "fits_the_gap": 23,
    "plan_says_today": 24,
    # 4. Caveats — true, and never the headline.
    "long_stretch": 30,
    "over_capacity": 31,
    "low_completion_odds": 32,
    "little_time_left": 33,
}

#: An unknown code sorts between "why it matters" and "why now" rather than
#: last, so a newly added reason is visible instead of silently buried.
_REASON_RANK_DEFAULT = 15


def _rank_reasons(values: Sequence[str]) -> tuple[str, ...]:
    """Deduplicate, then order by how much the student needs to hear it."""
    seen: set[str] = set()
    unique: list[str] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            unique.append(v)
    return tuple(
        sorted(unique, key=lambda v: _REASON_RANK.get(v, _REASON_RANK_DEFAULT))
    )


# ── Decision ─────────────────────────────────────────────────────────


def decide(
    ctx: NBAContext,
    *,
    planned_today: Sequence[Mapping[str, Any]] = (),
    assignments: Sequence[Mapping[str, Any]] = (),
    mastery: Sequence[MasteryEstimate] = (),
    limit: int = 4,
) -> list[NextAction]:
    """Generate, score, and rank. The whole engine, in one call.

    Returns the winner first, then the runners-up — which the Today surface
    shows as "next". Returning the alternatives is not a UI convenience: a
    recommendation the student cannot see past is one they cannot disagree
    with, and disagreement is how the model learns.
    """
    candidates = generate(
        ctx, planned_today=planned_today, assignments=assignments, mastery=mastery
    )
    if not candidates:
        return []
    ranked = sorted(
        (score(c, ctx) for c in candidates),
        key=lambda a: (a.score, a.candidate.deadline_pressure),
        reverse=True,
    )
    # Dedupe *before* honouring dismissals, not after. The two ingest paths
    # mint different ids for the same work, so filtering first removes the
    # plan candidate and leaves the assignment one standing — the student
    # declines their Physics set and it comes straight back wearing a
    # different id. Collapsing to one candidate first means the id the
    # student acted on is the id that survives, so the instruction lands.
    return _honour_dismissals(_dedupe_actions(ranked), ctx)[: max(1, limit)]


def _honour_dismissals(
    actions: Sequence[NextAction], ctx: NBAContext
) -> list[NextAction]:
    """Drop what the student has already said no to today.

    With one exception: work that is genuinely *past* its due date comes
    back. Declining something does not make its deadline go away, and
    silently dropping an overdue item because it was dismissed this morning
    would be the scheduler helping a student miss it.

    The exception tests the date, not the urgency score. Those are not the
    same thing: ``_deadline_pressure`` returns 1.0 for both "due today" and
    "three days late", and using it here would refuse to let a student defer
    something due tonight to this evening — which is a completely reasonable
    thing to want, and the whole point of the override.

    Everything else stays gone until tomorrow. "Not now" is an instruction,
    not a preference to be weighed.
    """
    if not ctx.dismissed_task_ids:
        return list(actions)
    today = ctx.today

    def dismissed(c: ActionCandidate) -> bool:
        # Either identifier matches. The id says "this exact row"; the
        # identity key says "this piece of work, whichever row it arrived
        # on" — and after an override moves the plan block, only the second
        # one still lines up.
        if c.task_id and c.task_id in ctx.dismissed_task_ids:
            return True
        return identity_key(c.title, c.course) in ctx.dismissed_task_ids

    return [
        a
        for a in actions
        if not dismissed(a.candidate)
        or (a.candidate.due_date is not None and a.candidate.due_date < today)
    ]


def _dedupe_actions(actions: Sequence[NextAction]) -> list[NextAction]:
    """One row per piece of work.

    Matching on ``task_id`` alone is not enough, and this is the bug that
    reached a browser: the plan block and the assignment row for the *same*
    essay carry ids minted by different ingest paths ("t-physics" versus
    "manual:412"), so an id-only check happily showed the student their
    Physics set as both the headline and the first thing after it.

    So identity is "same id" **or** "same title in the same course". The
    second is a heuristic and it is the right one here: two genuinely
    different pieces of work with an identical title in an identical course
    are indistinguishable to the student too, and collapsing them costs less
    than showing the same row twice.
    """
    seen_ids: set[str] = set()
    seen_names: set[tuple[str, str]] = set()
    out: list[NextAction] = []
    for a in actions:
        c = a.candidate
        name_key = (c.title.strip().lower(), c.course.strip().lower())
        if c.task_id and c.task_id in seen_ids:
            continue
        # A break has no title worth matching on and there is only ever one.
        if c.kind is not ActionKind.BREAK and name_key in seen_names:
            continue
        if c.task_id:
            seen_ids.add(c.task_id)
        seen_names.add(name_key)
        out.append(a)
    return out


# ── loose-input helpers ──────────────────────────────────────────────


def _int(value: Any, default: int) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return default
    return out if out > 0 else default


def _as_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _difficulty(value: Any) -> float:
    return {"easy": 0.25, "medium": 0.5, "hard": 0.8}.get(
        str(value or "").strip().lower(), 0.5
    )
