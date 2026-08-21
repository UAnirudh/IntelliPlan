"""Behavioural model — will this student actually finish this sitting?

The estimation model (:mod:`intelliplan.intelligence.estimation`) answers
*how long* work takes. This module answers the other half of the question the
scheduler needs: *how likely is it that the work happens at all*, given who
is being asked, what subject it is, what time of day it is, and how long the
sitting is.

That number matters more than it sounds. A plan built only from deadlines and
capacity will happily schedule 90 minutes of Chemistry at 9 PM for a student
who has abandoned every evening Chemistry sitting they have ever started. It
is a feasible plan and a fictional one.

Model
-----
Completion is a Bernoulli outcome, so the estimator is a Beta-Binomial:

    p = (successes + k · p_parent) / (trials + k)

The interesting part is what ``p_parent`` is. History is sliced several ways
— by course, by time of day, by both — and the estimate for a sitting uses
the **narrowest slice that has any data at all**, shrunk toward *everything
else that student has done*.

"Everything else" is literal: the parent is the global tally with the child
slice subtracted out. That subtraction is the difference between a working
model and a broken one. A course's sittings are also in the global tally, so
shrinking the course estimate toward the raw global estimate counts the same
sittings twice and moves the answer twice as far as the evidence supports —
in an earlier version of this module a single abandoned session dropped a
student's odds from 62% to 40%. With the overlap removed, one observation
nudges and fifty decide, which is what shrinkage is for.

Observations decay with the same 45-day half-life used elsewhere: what a
student did in September is weak evidence about them in December. The
shrinkage strength is shared with the estimation model deliberately — two
different constants for the same statistical job would be two things to tune
and one of them would rot.

Sitting length is handled as a *learned multiplier*, not a hardcoded penalty.
Sittings are bucketed relative to the student's own stamina (short / typical /
long) and each bucket is compared against sittings of other lengths. The
factor is 1.0 by construction when there is no evidence — a student we know
nothing about gets no length penalty rather than an invented one.

Purity
------
No Flask, no ORM, no clock reads. ``now`` is passed in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from intelliplan.domain.student import (
    BehaviorProfile,
    Evidence,
    EvidenceSource,
    SlotStat,
)

__all__ = [
    "BehaviorObservation",
    "CompletionPrediction",
    "BehaviorModel",
    "build_behavior_model",
    "coerce_observations",
    "slot_for_hour",
    "POPULATION_COMPLETION_RATE",
]

# ── Constants ────────────────────────────────────────────────────────

#: What a student with no history is assumed to complete. Measured across
#: IntelliPlan's Active-study sessions at the time of writing; it exists so
#: a brand-new user gets a defensible number instead of 0.5-by-superstition.
#: Re-derive it, do not nudge it.
POPULATION_COMPLETION_RATE = 0.62

#: Evidence older than this counts half as much. Shared with the estimation
#: model on purpose — see the module docstring.
HALF_LIFE_DAYS = 45.0

#: Pseudo-observations of the parent mixed into every child level.
SHRINKAGE_STRENGTH = 4.0

#: Below this effective sample count we refuse to call a rate "measured".
MIN_SAMPLES_FOR_SIGNAL = 4.0

#: Effective samples at which we consider ourselves confident.
CONFIDENT_SAMPLES = 12.0

#: Sitting-length buckets, as multiples of the student's stamina.
SHORT_BUCKET_MAX = 0.75
LONG_BUCKET_MIN = 1.25

#: The learned length factor is clamped: a bucket with three lucky samples
#: should not be allowed to double or annihilate a completion probability.
LENGTH_FACTOR_BOUNDS = (0.55, 1.30)

#: Guard rails on the returned probability. Never 0 (a student *can* do the
#: thing) and never 1 (nobody finishes everything).
PROBABILITY_BOUNDS = (0.05, 0.97)

DEFAULT_STAMINA_MINUTES = 45
STAMINA_BOUNDS = (15, 120)

#: Daily minutes assumed sustainable before we have seen a student's weeks.
DEFAULT_WORKLOAD_TOLERANCE = 150
WORKLOAD_TOLERANCE_BOUNDS = (45, 480)

SLOT_ORDER: tuple[str, ...] = ("morning", "afternoon", "evening")


def slot_for_hour(hour: int) -> str:
    """Name the part of the day an hour falls in.

    Mirrors ``scheduler_engine.slot_for_hour`` exactly — including folding
    late-night hours into "evening" — because a student's 1 AM sitting must
    be credited to the same slot by both modules, or the scheduler and the
    behavioural model will disagree about that student's own history.
    """
    if 6 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    return "evening"


# ── Observations ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class BehaviorObservation:
    """One sitting the student either finished or did not."""

    completed: bool
    minutes: int
    course: str = ""
    kind: str = ""
    slot: str = "evening"
    at: datetime | None = None
    rescheduled: bool = False

    def weight(self, now: datetime) -> float:
        """Decay-weighted worth of this observation."""
        if self.at is None:
            return 0.6  # dateless rows are real but unplaceable in time
        try:
            age_days = max(0.0, (now - self.at).total_seconds() / 86400.0)
        except TypeError:
            # Naive/aware mismatch — the ORM stores naive UTC, tests may not.
            return 0.6
        return 0.5 ** (age_days / HALF_LIFE_DAYS)


@dataclass(frozen=True, slots=True)
class _Cell:
    """Decay-weighted successes and trials for one slice of history."""

    successes: float = 0.0
    trials: float = 0.0

    def posterior(self, parent: float) -> float:
        return (self.successes + SHRINKAGE_STRENGTH * parent) / (
            self.trials + SHRINKAGE_STRENGTH
        )


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ── Prediction ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CompletionPrediction:
    """P(finish) for a specific proposed sitting, with its provenance."""

    probability: float
    evidence: Evidence
    length_factor: float
    slot: str

    @property
    def is_confident(self) -> bool:
        return self.evidence.confidence >= 0.6


# ── The model ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class BehaviorModel:
    """Fitted completion behaviour for one student.

    Construct with :func:`build_behavior_model`; the constructor is not
    meant to be called directly outside tests.
    """

    global_cell: _Cell
    slot_cells: dict[str, _Cell]
    course_cells: dict[str, _Cell]
    course_slot_cells: dict[tuple[str, str], _Cell]
    length_cells: dict[str, _Cell]
    stamina_minutes: int
    workload_tolerance_minutes: int
    abandon_rate: float
    reschedule_rate: float
    slot_minutes: dict[str, int]
    total_weight: float

    # ── core query ───────────────────────────────────────────────────

    def completion_probability(
        self,
        *,
        course: str = "",
        slot: str = "",
        minutes: int | None = None,
    ) -> CompletionPrediction:
        """P(this student finishes this sitting).

        Every argument is optional. Supplying fewer of them does not make
        the answer wrong, only less specific — each missing dimension simply
        stops the chain one level earlier.
        """
        slot_key = slot if slot in SLOT_ORDER else ""
        cell = self._most_specific(course, slot_key)
        p = cell.posterior(self._background(cell))

        factor = self._length_factor(minutes)
        p = _clamp(p * factor, *PROBABILITY_BOUNDS)

        return CompletionPrediction(
            probability=round(p, 3),
            evidence=self._evidence(cell.trials),
            length_factor=round(factor, 3),
            slot=slot_key,
        )

    def _most_specific(self, course: str, slot: str) -> _Cell:
        """The narrowest slice of history that describes this sitting.

        Narrowest *that has any data*: an empty (Chemistry, morning) cell
        teaches nothing that the Chemistry cell does not already say, and
        walking through it would only shrink the estimate a second time on
        the strength of no observations at all.
        """
        course_key = _norm(course)
        slot_key = slot if slot in SLOT_ORDER else ""

        for candidate in (
            self.course_slot_cells.get((course_key, slot_key))
            if course_key and slot_key
            else None,
            self.course_cells.get(course_key) if course_key else None,
            self.slot_cells.get(slot_key) if slot_key else None,
        ):
            if candidate is not None and candidate.trials > 0:
                return candidate
        return self.global_cell

    def _background(self, cell: _Cell) -> float:
        """This student's rate *excluding* the slice being asked about.

        The subtraction is the whole trick. A course cell's observations are
        also in the global cell, so shrinking the course estimate toward the
        raw global estimate would count the same sittings twice and move the
        answer twice as far as one sitting's worth of evidence justifies —
        which is how a single abandoned session used to knock twenty points
        off a student's completion odds.

        With the overlap removed, the parent means "everything else you have
        done", which is exactly the right thing to fall back on.
        """
        residual = _Cell(
            successes=max(0.0, self.global_cell.successes - cell.successes),
            trials=max(0.0, self.global_cell.trials - cell.trials),
        )
        return residual.posterior(POPULATION_COMPLETION_RATE)

    def _length_factor(self, minutes: int | None) -> float:
        """Learned multiplier for sitting length, 1.0 when unevidenced."""
        if not minutes or minutes <= 0:
            return 1.0
        bucket = self.length_bucket(minutes)
        cell = self.length_cells.get(bucket, _Cell())
        # Compared against sittings of *other* lengths, not against all
        # sittings — otherwise the bucket is being measured partly against
        # itself and a student whose every sitting is long looks average.
        base = self._background(cell)
        if base <= 0:
            return 1.0
        return _clamp(cell.posterior(base) / base, *LENGTH_FACTOR_BOUNDS)

    def length_bucket(self, minutes: int) -> str:
        ratio = minutes / max(1, self.stamina_minutes)
        if ratio <= SHORT_BUCKET_MAX:
            return "short"
        if ratio >= LONG_BUCKET_MIN:
            return "long"
        return "typical"

    def _evidence(self, trials: float) -> Evidence:
        if self.total_weight < 1.0:
            return Evidence.population()
        source = (
            EvidenceSource.MEASURED
            if trials >= MIN_SAMPLES_FOR_SIGNAL
            else EvidenceSource.PARTIAL
        )
        confidence = _clamp(
            math.sqrt(min(trials, CONFIDENT_SAMPLES) / CONFIDENT_SAMPLES), 0.15, 0.95
        )
        return Evidence(source, round(trials, 2), round(confidence, 3))

    # ── derived views ────────────────────────────────────────────────

    def best_slot(self) -> str | None:
        """The slot this student most reliably finishes work in.

        Returns ``None`` rather than a coin-flip winner when no slot has
        cleared the noise floor: telling a student "you work best in the
        morning" on the strength of two sittings is a fabricated insight.
        """
        eligible = [
            (
                self.slot_cells[s].posterior(self._background(self.slot_cells[s])),
                self.slot_cells[s].trials,
                s,
            )
            for s in SLOT_ORDER
            if s in self.slot_cells
            and self.slot_cells[s].trials >= MIN_SAMPLES_FOR_SIGNAL
        ]
        if not eligible:
            return None
        return max(eligible)[2]

    def profile(self) -> BehaviorProfile:
        """The student-facing summary of everything fitted here."""
        base = self.global_cell.posterior(POPULATION_COMPLETION_RATE)
        slots = tuple(
            SlotStat(
                slot=s,
                completion_rate=round(
                    self.slot_cells[s].posterior(self._background(self.slot_cells[s])), 3
                ),
                sessions=round(self.slot_cells[s].trials, 2),
                median_minutes=self.slot_minutes.get(s, self.stamina_minutes),
            )
            for s in SLOT_ORDER
            if s in self.slot_cells
        )
        return BehaviorProfile(
            completion_rate=round(base, 3),
            completion_evidence=self._evidence(self.global_cell.trials),
            abandon_rate=round(self.abandon_rate, 3),
            slots=slots,
            best_slot=self.best_slot(),
            stamina_minutes=self.stamina_minutes,
            workload_tolerance_minutes=self.workload_tolerance_minutes,
            reschedule_rate=round(self.reschedule_rate, 3),
            observations=round(self.total_weight, 2),
        )


# ── Fitting ──────────────────────────────────────────────────────────


def coerce_observations(
    rows: Iterable[Mapping[str, Any]] | None,
) -> list[BehaviorObservation]:
    """Build observations from the loose dicts the app already produces.

    Accepts Active-study session rows, ``TaskFeedback`` rows, and the test
    fixtures' shorthand. Rows that carry no usable duration are dropped
    rather than defaulted: a made-up duration would be fed straight into the
    length buckets.
    """
    out: list[BehaviorObservation] = []
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        minutes = row.get("actual", row.get("actual_time", row.get("actual_minutes")))
        if minutes in (None, "", 0):
            minutes = row.get(
                "planned_minutes",
                row.get("estimated", row.get("estimated_time")),
            )
        try:
            minutes_i = int(minutes)
        except (TypeError, ValueError):
            continue
        if minutes_i <= 0:
            continue

        at = (
            row.get("started_at")
            or row.get("at")
            or row.get("completed_at")
            or row.get("ended_at")
        )
        if not isinstance(at, datetime):
            at = None

        slot = _norm(row.get("slot"))
        if slot not in SLOT_ORDER:
            hour = row.get("hour")
            if hour is None and at is not None:
                hour = at.hour
            try:
                slot = slot_for_hour(int(hour))
            except (TypeError, ValueError):
                slot = "evening"

        completed = row.get("completed")
        if completed is None:
            completed = row.get("completed_work")
        out.append(
            BehaviorObservation(
                completed=True if completed is None else bool(completed),
                minutes=minutes_i,
                course=_norm(row.get("course")),
                kind=_norm(row.get("kind") or row.get("type")),
                slot=slot,
                at=at,
                rescheduled=bool(row.get("rescheduled")),
            )
        )
    return out


def _tally(
    observations: Sequence[BehaviorObservation],
    now: datetime,
    key,
) -> dict[Any, _Cell]:
    acc: dict[Any, list[float]] = {}
    for o in observations:
        k = key(o)
        if k is None:
            continue
        w = o.weight(now)
        bucket = acc.setdefault(k, [0.0, 0.0])
        bucket[1] += w
        if o.completed:
            bucket[0] += w
    return {k: _Cell(successes=v[0], trials=v[1]) for k, v in acc.items()}


def _weighted_percentile(values: Sequence[tuple[float, int]], pct: float) -> int | None:
    """Percentile over ``(weight, value)`` pairs. ``None`` when empty."""
    if not values:
        return None
    ordered = sorted(values, key=lambda p: p[1])
    total = sum(w for w, _ in ordered)
    if total <= 0:
        return None
    target = total * pct
    running = 0.0
    for w, v in ordered:
        running += w
        if running >= target:
            return int(v)
    return int(ordered[-1][1])


def build_behavior_model(
    session_rows: Iterable[Mapping[str, Any]] | None = None,
    feedback_rows: Iterable[Mapping[str, Any]] | None = None,
    *,
    daily_minutes: Mapping[Any, int] | None = None,
    reschedule_events: int = 0,
    now: datetime | None = None,
) -> BehaviorModel:
    """Fit a :class:`BehaviorModel` from a student's own history.

    ``session_rows`` are Active-study sittings — the only source that knows
    whether a sitting was *finished*, which is why they are listed first and
    why feedback rows are a weaker supplement rather than an equal input.

    ``daily_minutes`` maps a date (any hashable) to minutes of work actually
    completed that day; it is what workload tolerance is measured from.

    With no history at all this returns a model whose every answer is the
    population prior. That is the correct behaviour for a new user, and it is
    why nothing downstream needs a "do they have history yet" branch.
    """
    if now is None:
        raise ValueError("build_behavior_model requires an explicit `now`")

    sessions = coerce_observations(session_rows)
    feedback = coerce_observations(feedback_rows)
    observations = sessions + feedback

    total_weight = sum(o.weight(now) for o in observations)

    # Stamina is measured from finished *sittings* only. A task-feedback row
    # says a task took 140 minutes; it does not say the student sat still for
    # 140 minutes. Reading it that way gives a burst worker a two-hour focus
    # length, and then schedules them two-hour blocks they never finish.
    finished_sittings = [(o.weight(now), o.minutes) for o in sessions if o.completed]
    stamina_raw = _weighted_percentile(finished_sittings, 0.5)
    stamina = int(
        _clamp(
            float(stamina_raw if stamina_raw is not None else DEFAULT_STAMINA_MINUTES),
            *STAMINA_BOUNDS,
        )
    )

    # Workload tolerance is the 80th percentile of days actually delivered,
    # not the maximum: one heroic Sunday is not a sustainable weekday.
    tolerance_raw = None
    if daily_minutes:
        pairs = [(1.0, int(v)) for v in daily_minutes.values() if int(v or 0) > 0]
        tolerance_raw = _weighted_percentile(pairs, 0.8)
    tolerance = int(
        _clamp(
            float(
                tolerance_raw
                if tolerance_raw is not None
                else DEFAULT_WORKLOAD_TOLERANCE
            ),
            *WORKLOAD_TOLERANCE_BOUNDS,
        )
    )

    started = sum(o.weight(now) for o in sessions)
    abandoned = sum(o.weight(now) for o in sessions if not o.completed)
    abandon_rate = (abandoned / started) if started > 0 else 0.0

    reschedule_rate = _clamp(
        float(reschedule_events) / max(1.0, total_weight), 0.0, 1.0
    )

    slot_minutes: dict[str, int] = {}
    for s in SLOT_ORDER:
        rows = [
            (o.weight(now), o.minutes)
            for o in sessions
            if o.slot == s and o.completed
        ]
        median = _weighted_percentile(rows, 0.5)
        if median is not None:
            slot_minutes[s] = median

    global_cell = _Cell(
        successes=sum(o.weight(now) for o in observations if o.completed),
        trials=total_weight,
    )

    def _bucket(o: BehaviorObservation) -> str:
        ratio = o.minutes / max(1, stamina)
        if ratio <= SHORT_BUCKET_MAX:
            return "short"
        if ratio >= LONG_BUCKET_MIN:
            return "long"
        return "typical"

    return BehaviorModel(
        global_cell=global_cell,
        slot_cells=_tally(observations, now, lambda o: o.slot or None),
        course_cells=_tally(observations, now, lambda o: o.course or None),
        course_slot_cells=_tally(
            observations,
            now,
            lambda o: (o.course, o.slot) if o.course and o.slot else None,
        ),
        length_cells=_tally(observations, now, _bucket),
        stamina_minutes=stamina,
        workload_tolerance_minutes=tolerance,
        abandon_rate=abandon_rate,
        reschedule_rate=reschedule_rate,
        slot_minutes=slot_minutes,
        total_weight=total_weight,
    )
