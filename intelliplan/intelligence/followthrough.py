"""Follow-Through model — will this student actually do this sitting?

The planner has always been able to answer "does this work *fit*". It could
not answer "will this work *happen*", and those are different questions. A
plan that parks ninety minutes of Chemistry on a Friday evening, after two
other subjects, for a student who has skipped every Friday-evening block they
have ever been given, is a feasible plan and a fictional one. This module is
the half of the scheduler that knows the difference.

What it predicts
----------------
``P(done | sitting)`` — the probability that a planned block of work is
actually carried out, as a function of *when* it is, *how long* it is, *what*
it is, and *how much the day already holds*.

Why logistic regression, and why Bayesian
-----------------------------------------
The older behavioural model (:mod:`intelliplan.intelligence.behavior`) keeps
Beta-Binomial tallies per slice — per course, per time of day — and answers
from the narrowest slice that has data. That is sound for one dimension at a
time and cannot combine them: it can learn "evenings are weak" and "Chemistry
is weak" but never "evenings are weak *and* long sittings are weak *and* the
third block of a day is weak", because every extra dimension multiplies the
number of slices until none of them has data.

A logistic regression shares strength across dimensions. Each effect is one
coefficient, learned from every observation that carries it, and effects add
in log-odds. Twelve observations can say something about time of day *and*
sitting length at once.

The catch with regression on one student's history is that there is very
little of it. So the coefficients are not fitted freely — each has a Gaussian
prior centred on what is true of students in general (the *population
prior*), and the fit is the posterior mode (MAP). With no history the model
returns the population's answer; with a semester of history it returns the
student's own. Nothing in between needs a special case: shrinkage *is* the
cold-start strategy. It is also ridge regression with a non-zero centre, for
anyone who prefers that name.

The population prior is itself learned — :func:`fit_population_prior` pools
every student's outcomes, and the app refits it on a timer. Every student who
uses IntelliPlan makes the next student's first plan better. That is the
compounding part.

Training data
-------------
Two sources, and the choice of what to *exclude* matters as much:

* **Plan outcomes** — every block the plan scheduled on a day that has now
  passed, labelled done or not by the student's own checkboxes. This is the
  exact quantity the planner needs: "a block like this, on a day like this,
  got done". :func:`harvest_plan_outcomes` extracts them.
* **Active-study sittings** — sessions the student started, labelled by
  whether they finished. These are *conditional on starting*, so they carry a
  ``source_session`` indicator whose coefficient learns the offset instead of
  letting it contaminate the plan-level base rate.

``TaskFeedback`` rows are deliberately **not** used. A feedback row exists
only because a task was completed, so every one of them is a success, and
fitting on them teaches the model that everything gets done.

Sitting length uses *planned* minutes, never actual ones. An abandoned
sitting's actual duration is short because it was abandoned; using it as the
length feature would teach the model that short sittings fail, which is the
leak running backwards.

Honesty guards
--------------
* Every coefficient carries a posterior standard deviation (Laplace
  approximation). An insight is only surfaced when the effect is at least
  1.5 sd from zero *and* backed by real observations — a population prior is
  never presented as something we learned about this student.
* :func:`evaluate_holdout` scores the model on the student's most recent
  history after fitting on the older part, against the base-rate baseline.
  A model that cannot beat "always predict your average" should say so.

Purity
------
No Flask, no ORM, no clock reads. ``now`` is always passed in. Pure Python —
per-student data is hundreds of rows, and the sparse Newton solver below fits
it in a few milliseconds without a numerical dependency in the deploy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "FollowThroughObservation",
    "Prior",
    "DEFAULT_PRIOR",
    "FollowThroughModel",
    "Insight",
    "Evaluation",
    "fit_followthrough",
    "fit_population_prior",
    "evaluate_holdout",
    "observations_from_sessions",
    "observations_from_outcomes",
    "harvest_plan_outcomes",
    "slot_mix_for_windows",
    "slot_for_hour",
]


# ── Constants ────────────────────────────────────────────────────────

#: Same half-life as the estimation and behavioural models: what a student
#: did in September is weak evidence about them in December.
HALF_LIFE_DAYS = 45.0

#: Newton iterations. The log-posterior is strictly concave (Gaussian prior),
#: so this converges in a handful of steps; the cap is a guard, not a budget.
MAX_ITERATIONS = 30
TOLERANCE = 1e-7

#: At most this many course effects are learned; rarer courses share the
#: intercept. A course with two sittings is not a random effect, it is noise.
MAX_COURSE_FEATURES = 12
MIN_COURSE_OBSERVATIONS = 3

#: Probability guard rails. Nobody finishes everything; everybody can do
#: something.
PROBABILITY_BOUNDS = (0.03, 0.98)

#: Effective observations below which the model refuses to describe the
#: student at all — every answer is then visibly the population's.
MIN_OBSERVATIONS_FOR_INSIGHT = 12.0

#: How many posterior sds an effect must clear before it is called real.
INSIGHT_Z = 1.5

#: Rows per student in the pooled population fit. Without a cap, the three
#: most active students would *be* the population.
POPULATION_ROWS_PER_USER = 200

DEFAULT_STAMINA_MINUTES = 45

SLOTS: tuple[str, ...] = ("morning", "afternoon", "evening")
_DOW = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MEMORY_KINDS = frozenset({"exam", "test", "quiz"})
DEEP_KINDS = frozenset({"project", "lab", "essay"})


def slot_for_hour(hour: int) -> str:
    """Mirrors ``scheduler_engine.slot_for_hour`` — late night is evening."""
    if 6 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    return "evening"


def _sigmoid(z: float) -> float:
    if z >= 0:
        e = math.exp(-z)
        return 1.0 / (1.0 + e)
    e = math.exp(z)
    return e / (1.0 + e)


def _logit(p: float) -> float:
    p = min(1 - 1e-9, max(1e-9, p))
    return math.log(p / (1 - p))


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ── Feature space ────────────────────────────────────────────────────

#: The fixed part of the design. Course effects are appended per fit.
BASE_FEATURES: tuple[str, ...] = (
    "bias",
    "slot_morning",
    "slot_afternoon",
    *(f"dow_{d}" for d in _DOW),
    "length",
    "load",
    "urgency",
    "hard",
    "easy",
    "kind_memory",
    "kind_deep",
    "source_session",
)


@dataclass(frozen=True, slots=True)
class Prior:
    """Gaussian prior over coefficients: a mean and a variance per feature.

    The means say what is true of students in general; the variances say
    how much individual students differ from that — which is what decides
    how quickly one student's evidence overrides the population. They are
    between-student spreads, *not* the standard error of the pooled fit: that
    shrinks to zero with enough students and would stop anyone being
    personalised at all.
    """

    means: Mapping[str, float]
    variances: Mapping[str, float]
    course_variance: float = 0.30
    #: Pooled observations behind ``means``. 0 for the hand-set default.
    sample_size: int = 0
    users: int = 0
    version: str = "followthrough-v1"

    def mean(self, name: str) -> float:
        return float(self.means.get(name, 0.0))

    def variance(self, name: str) -> float:
        if name.startswith("course:"):
            return float(self.course_variance)
        return float(self.variances.get(name, 0.25))

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "means": {k: round(float(v), 6) for k, v in self.means.items()},
            "variances": {k: round(float(v), 6) for k, v in self.variances.items()},
            "course_variance": self.course_variance,
            "sample_size": self.sample_size,
            "users": self.users,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "Prior":
        """Rebuild a stored prior; anything malformed falls back to default.

        A corrupt row in the priors table must degrade to the hand-set prior,
        not to a planner that cannot start.
        """
        if not isinstance(payload, Mapping):
            return DEFAULT_PRIOR
        try:
            means = {str(k): float(v) for k, v in dict(payload.get("means") or {}).items()}
            variances = {
                str(k): max(1e-4, float(v))
                for k, v in dict(payload.get("variances") or {}).items()
            }
        except (TypeError, ValueError):
            return DEFAULT_PRIOR
        if not means or any(not math.isfinite(v) for v in means.values()):
            return DEFAULT_PRIOR
        merged_means = {**DEFAULT_PRIOR.means, **means}
        merged_vars = {**DEFAULT_PRIOR.variances, **variances}
        try:
            course_variance = float(payload.get("course_variance", 0.30))
        except (TypeError, ValueError):
            course_variance = 0.30
        return cls(
            means=merged_means,
            variances=merged_vars,
            course_variance=max(1e-3, course_variance),
            sample_size=int(payload.get("sample_size") or 0),
            users=int(payload.get("users") or 0),
            version=str(payload.get("version") or "followthrough-v1"),
        )


#: What we assume before any data exists. Each number is a stated belief,
#: small, and in the direction the learning-science literature and ordinary
#: experience agree on. They are starting points the population fit replaces
#: as soon as the cron has run once.
DEFAULT_PRIOR = Prior(
    means={
        # ~60% of planned blocks get done by a typical student.
        "bias": _logit(0.60),
        "slot_morning": 0.0,
        "slot_afternoon": 0.0,
        **{f"dow_{d}": 0.0 for d in _DOW},
        # A sitting twice your usual focus length: roughly −12 points.
        "length": -0.35,
        # Each hour already on the day costs a little follow-through.
        "load": -0.15,
        # Work due tomorrow gets done more than work due in two weeks.
        "urgency": 0.50,
        "hard": -0.20,
        "easy": 0.10,
        "kind_memory": 0.0,
        "kind_deep": 0.0,
        # A sitting someone has already *started* is likelier to finish than
        # a block that merely exists on a calendar.
        "source_session": 0.30,
    },
    variances={
        "bias": 1.0,
        "slot_morning": 0.50,
        "slot_afternoon": 0.50,
        **{f"dow_{d}": 0.25 for d in _DOW},
        "length": 0.25,
        "load": 0.10,
        "urgency": 0.50,
        "hard": 0.25,
        "easy": 0.25,
        "kind_memory": 0.25,
        "kind_deep": 0.25,
        "source_session": 1.0,
    },
)


# ── Observations ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FollowThroughObservation:
    """One planned or started sitting, and whether it happened."""

    done: bool
    #: Planned length of the sitting.
    minutes: int
    day: date
    #: Share of the sitting in each part of the day. A block at 4 PM is
    #: ``{"afternoon": 1.0}``; a day-level prediction may be a mix.
    slot_mix: Mapping[str, float] = field(default_factory=lambda: {"evening": 1.0})
    #: Minutes of work earlier the same day (planned for plan blocks, actual
    #: for sittings) — the fatigue input.
    prior_load_minutes: int = 0
    days_to_due: int | None = None
    course: str = ""
    kind: str = ""
    difficulty: str = "medium"
    source: str = "plan"            # "plan" | "session"
    at: datetime | None = None

    def weight(self, now: datetime | None) -> float:
        if now is None:
            return 1.0
        stamp = self.at or datetime.combine(self.day, datetime.min.time())
        try:
            age = max(0.0, (now - stamp).total_seconds() / 86400.0)
        except TypeError:
            return 0.6
        return 0.5 ** (age / HALF_LIFE_DAYS)


def _urgency(days_to_due: int | None) -> float:
    """1.0 on the due date, 0.5 the day before, → 0 far out or undated."""
    if days_to_due is None:
        return 0.0
    return 1.0 / (1.0 + max(0, int(days_to_due)))


def _length(minutes: int, stamina: int) -> float:
    if not minutes or minutes <= 0:
        return 0.0
    return _clamp(math.log(minutes / max(10, stamina)), -1.6, 1.6)


def _slot_shares(mix: Mapping[str, float] | str | None) -> tuple[float, float]:
    """``(morning, afternoon)`` shares; evening is the reference level."""
    if isinstance(mix, str):
        mix = {mix: 1.0}
    if not mix:
        return 0.0, 0.0
    total = sum(max(0.0, float(v)) for v in mix.values()) or 1.0
    return (
        max(0.0, float(mix.get("morning", 0.0))) / total,
        max(0.0, float(mix.get("afternoon", 0.0))) / total,
    )


def _features(
    obs: FollowThroughObservation,
    stamina: int,
    index: Mapping[str, int],
) -> list[tuple[int, float]]:
    """Sparse feature vector ``[(column, value), ...]`` for one observation."""
    row: list[tuple[int, float]] = [(index["bias"], 1.0)]
    morning, afternoon = _slot_shares(obs.slot_mix)
    if morning:
        row.append((index["slot_morning"], morning))
    if afternoon:
        row.append((index["slot_afternoon"], afternoon))
    row.append((index[f"dow_{_DOW[obs.day.weekday()]}"], 1.0))
    length = _length(obs.minutes, stamina)
    if length:
        row.append((index["length"], length))
    if obs.prior_load_minutes > 0:
        row.append((index["load"], min(6.0, obs.prior_load_minutes / 60.0)))
    urgency = _urgency(obs.days_to_due)
    if urgency:
        row.append((index["urgency"], urgency))
    diff = _norm(obs.difficulty)
    if diff == "hard":
        row.append((index["hard"], 1.0))
    elif diff == "easy":
        row.append((index["easy"], 1.0))
    kind = _norm(obs.kind)
    if kind in MEMORY_KINDS:
        row.append((index["kind_memory"], 1.0))
    elif kind in DEEP_KINDS:
        row.append((index["kind_deep"], 1.0))
    if obs.source == "session":
        row.append((index["source_session"], 1.0))
    course_col = index.get(f"course:{_norm(obs.course)}")
    if course_col is not None:
        row.append((course_col, 1.0))
    return row


# ── Linear algebra (small, dense, SPD) ───────────────────────────────


def _cholesky(a: list[list[float]]) -> list[list[float]]:
    n = len(a)
    low = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = a[i][j] - sum(low[i][k] * low[j][k] for k in range(j))
            if i == j:
                # The prior adds a strictly positive diagonal, so this only
                # fails on NaN input; floor it rather than raise.
                low[i][j] = math.sqrt(max(s, 1e-12))
            else:
                low[i][j] = s / low[j][j]
    return low


def _chol_solve(low: list[list[float]], b: Sequence[float]) -> list[float]:
    n = len(low)
    y = [0.0] * n
    for i in range(n):
        y[i] = (b[i] - sum(low[i][k] * y[k] for k in range(i))) / low[i][i]
    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (y[i] - sum(low[k][i] * x[k] for k in range(i + 1, n))) / low[i][i]
    return x


def _chol_inverse_diag(low: list[list[float]]) -> list[float]:
    n = len(low)
    out = []
    for i in range(n):
        e = [0.0] * n
        e[i] = 1.0
        out.append(_chol_solve(low, e)[i])
    return out


# ── The fitted model ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Insight:
    """One thing the model is confident it has learned about this student."""

    key: str
    text: str
    #: Change in follow-through probability, in points, at the student's
    #: typical sitting. Signed.
    effect_points: int
    #: Posterior z-score of the underlying coefficient(s). ≥ 1.5 by
    #: construction — weaker effects are never surfaced.
    strength: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "text": self.text,
            "effect_points": self.effect_points,
            "strength": round(self.strength, 2),
        }


@dataclass(frozen=True, slots=True)
class Evaluation:
    """Out-of-time accuracy, against the only baseline that matters."""

    n_train: int
    n_test: int
    log_loss: float
    baseline_log_loss: float
    brier: float
    baseline_brier: float

    @property
    def lift(self) -> float:
        """Fractional log-loss reduction over predicting the base rate."""
        if self.baseline_log_loss <= 0:
            return 0.0
        return 1.0 - self.log_loss / self.baseline_log_loss

    @property
    def beats_baseline(self) -> bool:
        return self.log_loss < self.baseline_log_loss

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_train": self.n_train,
            "n_test": self.n_test,
            "log_loss": round(self.log_loss, 4),
            "baseline_log_loss": round(self.baseline_log_loss, 4),
            "brier": round(self.brier, 4),
            "baseline_brier": round(self.baseline_brier, 4),
            "lift": round(self.lift, 4),
            "beats_baseline": self.beats_baseline,
        }


@dataclass(frozen=True)
class FollowThroughModel:
    """A fitted posterior. Immutable; refit to update.

    Build with :func:`fit_followthrough`. A model fitted on nothing is valid
    and returns the prior's answers — nothing downstream needs a "does this
    student have history" branch.
    """

    names: tuple[str, ...]
    beta: tuple[float, ...]
    sd: tuple[float, ...]
    prior: Prior = DEFAULT_PRIOR
    stamina_minutes: int = DEFAULT_STAMINA_MINUTES
    n_observations: int = 0
    effective_observations: float = 0.0
    observed_rate: float | None = None
    iterations: int = 0
    evaluation: Evaluation | None = None
    _index: Mapping[str, int] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self._index:
            object.__setattr__(self, "_index", {n: i for i, n in enumerate(self.names)})

    # ── coefficient access ───────────────────────────────────────────

    def coef(self, name: str) -> float:
        i = self._index.get(name)
        if i is not None:
            return self.beta[i]
        # A course never seen in training contributes its prior mean (0).
        return self.prior.mean(name) if not name.startswith("course:") else 0.0

    def coef_sd(self, name: str) -> float:
        i = self._index.get(name)
        if i is not None:
            return self.sd[i]
        return math.sqrt(self.prior.variance(name))

    @property
    def has_signal(self) -> bool:
        return self.effective_observations >= MIN_OBSERVATIONS_FOR_INSIGHT

    # ── prediction ───────────────────────────────────────────────────

    def item_logit(
        self,
        *,
        course: str = "",
        kind: str = "",
        difficulty: str = "medium",
        minutes: int = 0,
        source: str = "plan",
    ) -> float:
        """The part of the log-odds fixed by *what* the sitting is."""
        z = self.coef("bias")
        z += self.coef("length") * _length(minutes, self.stamina_minutes)
        diff = _norm(difficulty)
        if diff == "hard":
            z += self.coef("hard")
        elif diff == "easy":
            z += self.coef("easy")
        k = _norm(kind)
        if k in MEMORY_KINDS:
            z += self.coef("kind_memory")
        elif k in DEEP_KINDS:
            z += self.coef("kind_deep")
        if source == "session":
            z += self.coef("source_session")
        c = _norm(course)
        if c:
            z += self.coef(f"course:{c}")
        return z

    def day_logit(self, day: date, slot_mix: Mapping[str, float] | str | None) -> float:
        """The part of the log-odds fixed by *when* the sitting is."""
        morning, afternoon = _slot_shares(slot_mix)
        return (
            self.coef(f"dow_{_DOW[day.weekday()]}")
            + morning * self.coef("slot_morning")
            + afternoon * self.coef("slot_afternoon")
        )

    def load_logit(self, prior_load_minutes: int) -> float:
        return self.coef("load") * min(6.0, max(0, prior_load_minutes) / 60.0)

    def urgency_logit(self, days_to_due: int | None) -> float:
        return self.coef("urgency") * _urgency(days_to_due)

    def probability(
        self,
        *,
        day: date,
        course: str = "",
        kind: str = "",
        difficulty: str = "medium",
        minutes: int = 0,
        slot_mix: Mapping[str, float] | str | None = None,
        prior_load_minutes: int = 0,
        days_to_due: int | None = None,
        source: str = "plan",
    ) -> float:
        """P(this sitting gets done)."""
        z = (
            self.item_logit(
                course=course, kind=kind, difficulty=difficulty,
                minutes=minutes, source=source,
            )
            + self.day_logit(day, slot_mix)
            + self.load_logit(prior_load_minutes)
            + self.urgency_logit(days_to_due)
        )
        return _clamp(_sigmoid(z), *PROBABILITY_BOUNDS)

    def predict(self, obs: FollowThroughObservation) -> float:
        return self.probability(
            day=obs.day,
            course=obs.course,
            kind=obs.kind,
            difficulty=obs.difficulty,
            minutes=obs.minutes,
            slot_mix=obs.slot_mix,
            prior_load_minutes=obs.prior_load_minutes,
            days_to_due=obs.days_to_due,
            source=obs.source,
        )

    @property
    def baseline_probability(self) -> float:
        """A typical sitting: evening, usual length, empty day, no deadline."""
        return _clamp(_sigmoid(self.coef("bias")), *PROBABILITY_BOUNDS)

    # ── explanation ──────────────────────────────────────────────────

    def insights(self, limit: int = 5) -> list[Insight]:
        """What the model is confident it has learned, strongest first.

        Every sentence is derived from a coefficient that cleared both
        guards: enough real observations, and a posterior at least
        :data:`INSIGHT_Z` sds from zero. Nothing here is a population prior
        dressed up as a finding.
        """
        if not self.has_signal:
            return []
        base_z = self.coef("bias")
        base_p = _sigmoid(base_z)

        def points(delta: float) -> int:
            return int(round((_sigmoid(base_z + delta) - base_p) * 100))

        def z_of(name: str) -> float:
            sd = self.coef_sd(name)
            return abs(self.coef(name)) / sd if sd > 0 else 0.0

        out: list[Insight] = []

        # Time of day: evening is the reference, so compare each to it and
        # report the widest real gap.
        slot_effects = {
            "morning": (self.coef("slot_morning"), z_of("slot_morning")),
            "afternoon": (self.coef("slot_afternoon"), z_of("slot_afternoon")),
        }
        best_slot = max(slot_effects.items(), key=lambda kv: abs(kv[1][0]))
        slot, (beta, strength) = best_slot
        if strength >= INSIGHT_Z and abs(points(beta)) >= 5:
            if beta > 0:
                text = f"You get through about {abs(points(beta))}% more of your {slot} sessions than evening ones."
            else:
                text = f"Your {slot} sessions get done about {abs(points(beta))}% less often than evening ones."
            out.append(Insight(f"slot_{slot}", text, points(beta), strength))

        # Weekdays: the day that stands out most, in either direction.
        dows = [(d, self.coef(f"dow_{d}"), z_of(f"dow_{d}")) for d in _DOW]
        worst = min(dows, key=lambda t: t[1])
        best = max(dows, key=lambda t: t[1])
        names = {
            "mon": "Mondays", "tue": "Tuesdays", "wed": "Wednesdays", "thu": "Thursdays",
            "fri": "Fridays", "sat": "Saturdays", "sun": "Sundays",
        }
        if worst[2] >= INSIGHT_Z and points(worst[1]) <= -5:
            out.append(Insight(
                f"dow_{worst[0]}",
                f"{names[worst[0]]} are your hardest day — about {abs(points(worst[1]))}% fewer planned sessions get done.",
                points(worst[1]), worst[2],
            ))
        if best[2] >= INSIGHT_Z and points(best[1]) >= 5:
            out.append(Insight(
                f"dow_{best[0]}",
                f"{names[best[0]]} are your most reliable day — about {points(best[1])}% more gets done.",
                points(best[1]), best[2],
            ))

        # Sitting length: effect of a sitting 50% longer than usual.
        length_beta = self.coef("length")
        length_delta = length_beta * math.log(1.5)
        if z_of("length") >= INSIGHT_Z and points(length_delta) <= -4:
            out.append(Insight(
                "length",
                f"Sessions longer than your usual ~{self.stamina_minutes} min are about "
                f"{abs(points(length_delta))}% less likely to get finished — the plan keeps them short.",
                points(length_delta), z_of("length"),
            ))

        # Fatigue: effect of two hours already on the day.
        load_delta = self.coef("load") * 2.0
        if z_of("load") >= INSIGHT_Z and points(load_delta) <= -4:
            out.append(Insight(
                "load",
                f"After two hours of work in a day, your follow-through drops about "
                f"{abs(points(load_delta))}% — the plan spreads heavy days out.",
                points(load_delta), z_of("load"),
            ))

        # Deadline pull: work due tomorrow vs undated.
        urgency_delta = self.coef("urgency") * 0.5
        if z_of("urgency") >= INSIGHT_Z and points(urgency_delta) >= 6:
            out.append(Insight(
                "urgency",
                "You're much more likely to do work in the last day or two before it's due. "
                "The plan still finishes early, and keeps a buffer so that pull doesn't become a crunch.",
                points(urgency_delta), z_of("urgency"),
            ))

        # Courses: the single weakest, if it is really weak.
        courses = [
            (n.split(":", 1)[1], self.coef(n), z_of(n))
            for n in self.names if n.startswith("course:")
        ]
        if courses:
            name, beta, strength = min(courses, key=lambda t: t[1])
            if strength >= INSIGHT_Z and points(beta) <= -6:
                out.append(Insight(
                    f"course:{name}",
                    f"{name.title()} sessions get skipped most — about {abs(points(beta))}% less often "
                    f"done than your other subjects, so they're placed where you're strongest.",
                    points(beta), strength,
                ))

        out.sort(key=lambda i: (-abs(i.effect_points) * min(i.strength, 4.0), i.key))
        return out[: max(0, limit)]

    def summary(self) -> dict[str, Any]:
        """Serialisable snapshot for the forecast panel and admin views."""
        return {
            "model": self.prior.version,
            "observations": self.n_observations,
            "effective_observations": round(self.effective_observations, 2),
            "observed_rate": None if self.observed_rate is None else round(self.observed_rate, 3),
            "baseline_probability": round(self.baseline_probability, 3),
            "personalised": self.has_signal,
            "prior_from_population": self.prior.sample_size > 0,
            "stamina_minutes": self.stamina_minutes,
            "evaluation": self.evaluation.to_dict() if self.evaluation else None,
            "coefficients": {
                n: {"beta": round(b, 4), "sd": round(s, 4)}
                for n, b, s in zip(self.names, self.beta, self.sd)
            },
        }


# ── Fitting ──────────────────────────────────────────────────────────


def _course_features(observations: Sequence[FollowThroughObservation]) -> list[str]:
    counts: dict[str, int] = {}
    for o in observations:
        c = _norm(o.course)
        if c:
            counts[c] = counts.get(c, 0) + 1
    eligible = [c for c, n in counts.items() if n >= MIN_COURSE_OBSERVATIONS]
    eligible.sort(key=lambda c: (-counts[c], c))
    return [f"course:{c}" for c in eligible[:MAX_COURSE_FEATURES]]


def _log_posterior(
    rows: Sequence[tuple[list[tuple[int, float]], float, float]],
    beta: Sequence[float],
    mu: Sequence[float],
    precision: Sequence[float],
) -> float:
    ll = 0.0
    for x, y, w in rows:
        z = sum(beta[j] * v for j, v in x)
        # log σ(z) and log(1−σ(z)), computed stably.
        if z >= 0:
            log_p, log_q = -math.log1p(math.exp(-z)), -z - math.log1p(math.exp(-z))
        else:
            log_p, log_q = z - math.log1p(math.exp(z)), -math.log1p(math.exp(z))
        ll += w * (y * log_p + (1.0 - y) * log_q)
    penalty = 0.5 * sum(p * (b - m) ** 2 for b, m, p in zip(beta, mu, precision))
    return ll - penalty


def fit_followthrough(
    observations: Iterable[FollowThroughObservation],
    *,
    now: datetime | None,
    prior: Prior | None = None,
    stamina_minutes: int = DEFAULT_STAMINA_MINUTES,
    include_courses: bool = True,
    evaluate: bool = False,
) -> FollowThroughModel:
    """MAP logistic regression under a Gaussian prior, by Newton's method.

    The log-posterior is strictly concave — the log-likelihood is concave and
    the prior adds a negative-definite quadratic — so Newton converges to the
    unique optimum. Step-halving is kept anyway as a guard against the
    handful of floating-point edge cases where a full step overshoots.
    """
    prior = prior or DEFAULT_PRIOR
    obs = [o for o in observations if isinstance(o, FollowThroughObservation)]
    stamina = int(_clamp(float(stamina_minutes or DEFAULT_STAMINA_MINUTES), 15, 120))

    names = list(BASE_FEATURES)
    if include_courses:
        names += _course_features(obs)
    index = {n: i for i, n in enumerate(names)}
    d = len(names)

    mu = [prior.mean(n) if not n.startswith("course:") else 0.0 for n in names]
    precision = [1.0 / max(1e-6, prior.variance(n)) for n in names]

    rows = [
        (_features(o, stamina, index), 1.0 if o.done else 0.0, o.weight(now))
        for o in obs
    ]
    rows = [r for r in rows if r[2] > 0]

    beta = list(mu)
    iterations = 0
    current = _log_posterior(rows, beta, mu, precision)
    for iterations in range(1, MAX_ITERATIONS + 1):
        grad = [-(p * (b - m)) for b, m, p in zip(beta, mu, precision)]
        hess = [[0.0] * d for _ in range(d)]
        for j in range(d):
            hess[j][j] = precision[j]
        for x, y, w in rows:
            p = _sigmoid(sum(beta[j] * v for j, v in x))
            r = w * (y - p)
            s = w * p * (1.0 - p)
            for j, v in x:
                grad[j] += r * v
                sv = s * v
                for k, u in x:
                    hess[j][k] += sv * u
        low = _cholesky(hess)
        step = _chol_solve(low, grad)

        scale = 1.0
        accepted = False
        for _ in range(8):
            candidate = [b + scale * s for b, s in zip(beta, step)]
            value = _log_posterior(rows, candidate, mu, precision)
            if value >= current - 1e-12:
                accepted = True
                break
            scale *= 0.5
        if not accepted:
            break
        moved = max((abs(scale * s) for s in step), default=0.0)
        beta, current = candidate, value
        if moved < TOLERANCE:
            break

    # Laplace approximation: posterior covariance is the inverse Hessian at
    # the mode. Recomputed at the final beta.
    hess = [[0.0] * d for _ in range(d)]
    for j in range(d):
        hess[j][j] = precision[j]
    for x, _y, w in rows:
        p = _sigmoid(sum(beta[j] * v for j, v in x))
        s = w * p * (1.0 - p)
        for j, v in x:
            sv = s * v
            for k, u in x:
                hess[j][k] += sv * u
    variances = _chol_inverse_diag(_cholesky(hess))
    sd = [math.sqrt(max(v, 0.0)) for v in variances]

    total_w = sum(w for _, _, w in rows)
    done_w = sum(w * y for _, y, w in rows)
    evaluation = None
    if evaluate:
        evaluation = evaluate_holdout(
            obs, now=now, prior=prior, stamina_minutes=stamina,
            include_courses=include_courses,
        )

    return FollowThroughModel(
        names=tuple(names),
        beta=tuple(beta),
        sd=tuple(sd),
        prior=prior,
        stamina_minutes=stamina,
        n_observations=len(rows),
        effective_observations=total_w,
        observed_rate=(done_w / total_w) if total_w > 0 else None,
        iterations=iterations,
        evaluation=evaluation,
    )


def evaluate_holdout(
    observations: Sequence[FollowThroughObservation],
    *,
    now: datetime | None,
    prior: Prior | None = None,
    stamina_minutes: int = DEFAULT_STAMINA_MINUTES,
    include_courses: bool = True,
    holdout_fraction: float = 0.2,
    min_rows: int = 30,
) -> Evaluation | None:
    """Fit on the older history, score on the newest. ``None`` if too thin.

    Out-of-*time*, not a random split: a random split lets the model see
    Thursday while predicting Wednesday, which flatters it in exactly the way
    that production never will.
    """
    ordered = sorted(
        observations,
        key=lambda o: o.at or datetime.combine(o.day, datetime.min.time()),
    )
    if len(ordered) < min_rows:
        return None
    cut = int(round(len(ordered) * (1.0 - holdout_fraction)))
    train, test = ordered[:cut], ordered[cut:]
    if len(test) < 8 or len(train) < 10:
        return None
    model = fit_followthrough(
        train, now=now, prior=prior, stamina_minutes=stamina_minutes,
        include_courses=include_courses, evaluate=False,
    )
    base = sum(1 for o in train if o.done) / len(train)
    base = _clamp(base, 0.02, 0.98)

    def losses(predict) -> tuple[float, float]:
        ll = brier = 0.0
        for o in test:
            p = _clamp(predict(o), 1e-4, 1 - 1e-4)
            y = 1.0 if o.done else 0.0
            ll -= y * math.log(p) + (1 - y) * math.log(1 - p)
            brier += (p - y) ** 2
        return ll / len(test), brier / len(test)

    ll, brier = losses(model.predict)
    base_ll, base_brier = losses(lambda _o: base)
    return Evaluation(
        n_train=len(train),
        n_test=len(test),
        log_loss=ll,
        baseline_log_loss=base_ll,
        brier=brier,
        baseline_brier=base_brier,
    )


def fit_population_prior(
    observations_by_user: Mapping[Any, Sequence[FollowThroughObservation]],
    *,
    now: datetime | None,
    rows_per_user: int = POPULATION_ROWS_PER_USER,
    min_users: int = 5,
) -> Prior:
    """Pool every student's outcomes into the prior new students start from.

    Course effects are left out: "Chemistry" means a different class, a
    different teacher, and a different workload at every school, so a pooled
    Chemistry effect is not knowledge about anyone. Each student contributes
    at most ``rows_per_user`` of their most recent rows so that the heaviest
    users do not become the population.

    Returns the default prior until at least ``min_users`` students have
    contributed — a "population" of two students is two students.
    """
    pooled: list[FollowThroughObservation] = []
    users = 0
    for rows in observations_by_user.values():
        ordered = sorted(
            (r for r in rows if isinstance(r, FollowThroughObservation)),
            key=lambda o: o.at or datetime.combine(o.day, datetime.min.time()),
            reverse=True,
        )[: max(1, rows_per_user)]
        if ordered:
            users += 1
            pooled.extend(ordered)
    if users < min_users or not pooled:
        return DEFAULT_PRIOR

    # A weak prior for the pooled fit: with thousands of rows the data
    # decides, and the hand-set means only stabilise features nobody has
    # exercised yet.
    weak = Prior(
        means=DEFAULT_PRIOR.means,
        variances={k: v * 4.0 for k, v in DEFAULT_PRIOR.variances.items()},
    )
    fitted = fit_followthrough(
        pooled, now=now, prior=weak, include_courses=False, evaluate=False,
    )
    means = {n: fitted.coef(n) for n in BASE_FEATURES}
    return Prior(
        means=means,
        variances=dict(DEFAULT_PRIOR.variances),
        course_variance=DEFAULT_PRIOR.course_variance,
        sample_size=fitted.n_observations,
        users=users,
    )


# ── Adapters: raw rows → observations ────────────────────────────────


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def observations_from_sessions(
    rows: Iterable[Mapping[str, Any]] | None,
) -> list[FollowThroughObservation]:
    """Active-study sittings → observations.

    Fatigue is reconstructed from the rows themselves: for each sitting, the
    minutes the student had already put in earlier that day. That is why the
    rows are processed in start-time order and grouped by day.
    """
    parsed: list[tuple[datetime, Mapping[str, Any]]] = []
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        started = _as_datetime(row.get("started_at") or row.get("at"))
        if started is None:
            continue
        parsed.append((started, row))
    parsed.sort(key=lambda t: t[0])

    out: list[FollowThroughObservation] = []
    day_load: dict[date, int] = {}
    for started, row in parsed:
        completed = row.get("completed", row.get("completed_work"))
        done = bool(completed)
        actual = _int(row.get("actual", row.get("actual_minutes")))
        planned = _int(row.get("planned_minutes", row.get("estimated")))
        # Planned length is the feature. Actual is only a fallback for a
        # *finished* sitting — an abandoned one's actual length is short
        # because it was abandoned, and using it would leak the label.
        minutes = planned if planned > 0 else (actual if done else 0)
        if minutes <= 0:
            continue
        day = started.date()
        due = _as_date(row.get("due_date"))
        out.append(
            FollowThroughObservation(
                done=done,
                minutes=minutes,
                day=day,
                slot_mix={slot_for_hour(started.hour): 1.0},
                prior_load_minutes=day_load.get(day, 0),
                days_to_due=(due - day).days if due else None,
                course=_norm(row.get("course")),
                kind=_norm(row.get("kind")),
                difficulty=_norm(row.get("difficulty")) or "medium",
                source="session",
                at=started,
            )
        )
        day_load[day] = day_load.get(day, 0) + max(0, actual)
    return out


def observations_from_outcomes(
    rows: Iterable[Mapping[str, Any]] | None,
) -> list[FollowThroughObservation]:
    """Harvested plan outcomes (see :func:`harvest_plan_outcomes`) → observations.

    Deduplicates on the outcome ``key``: the same past block can be harvested
    from the live plan and from the durable log, and must count once.
    """
    seen: set[str] = set()
    out: list[FollowThroughObservation] = []
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        key = str(row.get("key") or "")
        if key:
            if key in seen:
                continue
            seen.add(key)
        day = _as_date(row.get("date"))
        minutes = _int(row.get("minutes"))
        if day is None or minutes <= 0:
            continue
        slot = _norm(row.get("slot"))
        due = _as_date(row.get("due_date"))
        out.append(
            FollowThroughObservation(
                done=bool(row.get("done")),
                minutes=minutes,
                day=day,
                slot_mix={slot if slot in SLOTS else "evening": 1.0},
                prior_load_minutes=max(0, _int(row.get("prior_load"))),
                days_to_due=(due - day).days if due else None,
                course=_norm(row.get("course")),
                kind=_norm(row.get("kind")),
                difficulty=_norm(row.get("difficulty")) or "medium",
                source="plan",
                at=datetime.combine(day, datetime.min.time()) + timedelta(hours=12),
            )
        )
    return out


def _block_is_done(entry: Any) -> bool:
    if entry is True:
        return True
    if isinstance(entry, Mapping):
        return bool(entry.get("done"))
    return False


def _block_slot(block: Mapping[str, Any]) -> str:
    start = _as_datetime(block.get("start_iso"))
    if start is not None:
        return slot_for_hour(start.hour)
    raw = str(block.get("time_slot") or "").split("-")[0].strip().upper()
    try:
        hh_mm, meridiem = raw.split(" ")
        hour = int(hh_mm.split(":")[0]) % 12 + (12 if meridiem == "PM" else 0)
        return slot_for_hour(hour)
    except (ValueError, IndexError):
        return "evening"


def harvest_plan_outcomes(
    schedule_data: Mapping[str, Any] | None,
    progress: Mapping[str, Any] | None,
    today: date,
    *,
    valid_from: date | None = None,
    valid_until: date | None = None,
) -> list[dict[str, Any]]:
    """Every block on a past day, labelled by whether it was checked off.

    ``valid_from`` / ``valid_until`` bound the days this plan was actually the
    student's plan. A superseded plan's blocks after it was replaced were not
    *missed* — they were never asked of anyone — and counting them as
    failures would teach the model that every regenerated week is a week
    the student abandoned.

    Returns plain dicts, with no assignment titles — the same privacy rule as
    the audit tables. They go straight into the append-only outcome log.
    """
    out: list[dict[str, Any]] = []
    progress = progress or {}
    for day in (schedule_data or {}).get("schedule") or []:
        if not isinstance(day, Mapping):
            continue
        day_date = _as_date(day.get("date"))
        if day_date is None or day_date >= today:
            continue
        if valid_from is not None and day_date < valid_from:
            continue
        if valid_until is not None and day_date >= valid_until:
            continue
        load = 0
        for block in day.get("blocks") or []:
            if not isinstance(block, Mapping) or block.get("is_break"):
                continue
            minutes = _int(block.get("duration_minutes"))
            if minutes <= 0:
                continue
            task_id = str(block.get("task_id") or block.get("parent_title") or "")
            key_id = str(block.get("block_id") or block.get("id") or "")
            done = _block_is_done(progress.get(key_id)) if key_id else False
            due = _as_date(block.get("due_date"))
            out.append({
                "key": f"{day_date.isoformat()}|{task_id}|{_int(block.get('part_index'), 1)}|{minutes}",
                "date": day_date.isoformat(),
                "slot": _block_slot(block),
                "minutes": minutes,
                "prior_load": load,
                "course": str(block.get("course") or ""),
                "kind": str(block.get("kind") or ""),
                "difficulty": str(block.get("difficulty") or "medium").lower(),
                "due_date": due.isoformat() if due else None,
                "done": done,
            })
            load += minutes
    return out


def slot_mix_for_windows(windows: Iterable[Any]) -> dict[str, float]:
    """Share of a day's free time in each part of the day.

    ``windows`` are ``scheduler_engine.Window``-like objects with
    ``start_minute``/``end_minute`` (or ``start``/``end`` datetimes). The
    planner allocates work to days, not clock times, so this is its best
    knowledge of *when* on that day the work will fall.
    """
    totals = {s: 0.0 for s in SLOTS}
    for w in windows or []:
        start = getattr(w, "start_minute", None)
        end = getattr(w, "end_minute", None)
        if start is None or end is None:
            s_dt, e_dt = getattr(w, "start", None), getattr(w, "end", None)
            if isinstance(s_dt, datetime) and isinstance(e_dt, datetime):
                start = s_dt.hour * 60 + s_dt.minute
                end = start + int((e_dt - s_dt).total_seconds() // 60)
        if start is None or end is None or end <= start:
            continue
        # Walk the window in 15-minute steps; windows are at most a day.
        t = int(start)
        while t < int(end):
            step = min(15, int(end) - t)
            totals[slot_for_hour((t // 60) % 24)] += step
            t += step
    total = sum(totals.values())
    if total <= 0:
        return {"evening": 1.0}
    return {k: round(v / total, 4) for k, v in totals.items() if v > 0}
