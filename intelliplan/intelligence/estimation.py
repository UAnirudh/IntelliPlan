"""Effort estimation model.

The question this module answers is narrow and load-bearing: *how many
minutes will this specific student actually spend on this specific piece of
work?* Everything downstream — how many sessions a task becomes, how many
days it needs, how much buffer sits before the deadline — is a function of
that number, so getting it from "whatever the LLM guessed" to "what this
student has historically done" is the highest-leverage change in the
scheduler.

Model
-----
We learn a multiplicative bias, not an absolute duration. A student who
consistently takes 35 minutes on work estimated at 60 has a bias of ~0.58,
and that bias transfers to work we have never seen before. Absolute
durations do not transfer; ratios do.

Bias is learned in log space (``r = ln(actual / estimated)``) for three
reasons: the quantity is inherently multiplicative, log-ratios are roughly
symmetric so a mean is meaningful, and "twice as long" and "half as long"
become equal and opposite errors instead of one being four times the other.

Evidence is pooled hierarchically::

    global  →  course  →  (course, kind)

Each level shrinks toward its parent by sample count, so one data point
about Chemistry labs nudges the Chemistry-lab estimate slightly rather than
replacing it, and a student with fifty observations gets an estimate that
is essentially their own. This is the standard James–Stein / empirical-Bayes
shrinkage move and it is what keeps the model useful at n=2 without making
it erratic.

Observations decay with a half-life so the model tracks a student who got
faster mid-semester instead of averaging over their September self.

The model also reports **uncertainty** (the spread of residuals), which the
planner spends as deadline buffer: work we cannot predict well needs to
finish earlier than work we can.

Purity
------
No Flask, no SQLAlchemy, no clock reads. Callers pass observation dicts and
a reference ``now``. Fully unit-testable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from time_utils import utcnow

__all__ = [
    "Observation",
    "Estimate",
    "EstimationModel",
    "build_estimation_model",
    "baseline_minutes",
]


# ── Tuning constants ──────────────────────────────────────────────────

#: Observations older than this contribute half as much as fresh ones.
HALF_LIFE_DAYS = 45.0

#: Effective-sample weight of each parent level's prior when shrinking a
#: child toward it. Higher = more conservative. Chosen so a level needs
#: roughly this many fresh observations before it dominates its parent.
SHRINKAGE_STRENGTH = 4.0

#: The global level shrinks toward "no bias at all" (log-ratio 0) with this
#: strength, so a student with two observations does not get a 40% swing.
GLOBAL_PRIOR_STRENGTH = 3.0

#: Log-ratios beyond this are data errors (a timer left running overnight,
#: a task marked done a week late), not signal. ±ln(4) ≈ ±1.386.
LOG_RATIO_CLAMP = math.log(4.0)

#: Predicted minutes are always clamped into a range a human can act on.
#: The ceiling is *whole-task* effort, not a sitting — a term paper or a
#: cumulative final genuinely is fifteen hours of work, and the planner is
#: what breaks that into sittings the student can sit through. Clamping this
#: at five hours silently told every large project it was a long afternoon,
#: which is the one class of work where being wrong costs a grade.
MIN_PREDICTED_MINUTES = 5
MAX_PREDICTED_MINUTES = 1200

#: Residual spread assumed before we have enough data to measure it. 0.45 in
#: log space ≈ estimates land within roughly ±55% of the truth, which is
#: about what a fresh planner's guesses are worth.
DEFAULT_LOG_SIGMA = 0.45
SIGMA_BOUNDS = (0.12, 0.90)

#: Below this effective sample count a level is reported as low confidence.
CONFIDENT_SAMPLES = 6.0

#: Fallback minutes per assignment kind when no estimate was supplied at
#: all. These are starting priors the model immediately begins correcting;
#: they are not the product's answer, they are its first guess.
KIND_BASELINE_MINUTES: dict[str, int] = {
    "exam": 120,
    "project": 150,
    "test": 75,
    "lab": 70,
    "homework": 45,
    "classwork": 25,
    "other": 40,
}
DEFAULT_BASELINE_MINUTES = 45


def baseline_minutes(kind: str | None, points_possible: float | None = None) -> int:
    """A prior duration for work that arrived with no estimate.

    Points scale the baseline mildly — a 100-point project is more work than
    a 10-point one — but only mildly, because point scales are not
    comparable across courses and treating them as linear effort produces
    absurd numbers for a course graded out of 1000.
    """
    base = KIND_BASELINE_MINUTES.get(str(kind or "").strip().lower(), DEFAULT_BASELINE_MINUTES)
    if points_possible and points_possible > 0:
        # Compress hard: 10x the points is ~1.5x the work, not 10x.
        scale = (float(points_possible) / 20.0) ** 0.25
        base = int(round(base * max(0.6, min(1.8, scale))))
    return max(MIN_PREDICTED_MINUTES, min(MAX_PREDICTED_MINUTES, base))


# ── Observations ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Observation:
    """One completed piece of work with both a prediction and a truth.

    ``estimated`` is what the plan said; ``actual`` is what the student's
    timer (or self-report) recorded. Anything missing either side teaches us
    nothing about bias and is dropped at construction.
    """

    estimated: int
    actual: int
    course: str = ""
    kind: str = ""
    at: datetime | None = None
    #: Whether the student finished the work in that sitting. Abandoned
    #: sessions still tell us about stamina but their duration is a floor,
    #: not a measurement, so they are excluded from bias fitting.
    completed: bool = True

    @property
    def log_ratio(self) -> float:
        return _clamp_log(math.log(self.actual / self.estimated))


def _clamp_log(value: float) -> float:
    return max(-LOG_RATIO_CLAMP, min(LOG_RATIO_CLAMP, value))


def _norm(text: Any) -> str:
    return str(text or "").strip().lower()


def _coerce_observations(
    rows: Iterable[Mapping[str, Any]] | None,
) -> list[Observation]:
    """Build observations from loose dict rows (``TaskFeedback``, sessions).

    Accepts several key spellings because three different call sites feed
    this: the legacy feedback table, the new session log, and tests.
    """
    out: list[Observation] = []
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        est = row.get("estimated_time", row.get("estimated", row.get("estimated_minutes")))
        act = row.get("actual_time", row.get("actual", row.get("actual_minutes")))
        try:
            est_i, act_i = int(est), int(act)
        except (TypeError, ValueError):
            continue
        if est_i <= 0 or act_i <= 0:
            continue
        at = row.get("at") or row.get("completed_at") or row.get("ended_at")
        if not isinstance(at, datetime):
            at = None
        completed = row.get("completed")
        out.append(
            Observation(
                estimated=est_i,
                actual=act_i,
                course=_norm(row.get("course")),
                kind=_norm(row.get("kind") or row.get("type")),
                at=at,
                completed=True if completed is None else bool(completed),
            )
        )
    return out


# ── Fitted levels ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Level:
    """A recency-weighted fit of log-ratios at one level of the hierarchy."""

    mean: float = 0.0
    #: Effective sample size after recency decay — a float, not a count.
    weight: float = 0.0
    #: Weighted variance of residuals around ``mean``, in log space.
    variance: float = 0.0

    def shrunk_toward(self, parent: "_Level", strength: float) -> "_Level":
        """Pull this level's mean toward ``parent`` by its own thinness."""
        denom = self.weight + strength
        if denom <= 0:
            return parent
        mean = (self.mean * self.weight + parent.mean * strength) / denom
        # Variance blends the same way; a thin level inherits its parent's
        # spread rather than claiming false precision from two points.
        variance = (self.variance * self.weight + parent.variance * strength) / denom
        return _Level(mean=mean, weight=self.weight, variance=variance)


def _fit_level(obs: Sequence[Observation], now: datetime) -> _Level:
    """Recency-weighted mean and variance of log-ratios."""
    if not obs:
        return _Level()
    weights: list[float] = []
    values: list[float] = []
    for o in obs:
        age_days = 0.0
        if o.at is not None:
            age_days = max(0.0, (now - o.at).total_seconds() / 86400.0)
        weights.append(0.5 ** (age_days / HALF_LIFE_DAYS))
        values.append(o.log_ratio)
    total = sum(weights)
    if total <= 0:
        return _Level()
    mean = sum(w * v for w, v in zip(weights, values)) / total
    variance = sum(w * (v - mean) ** 2 for w, v in zip(weights, values)) / total
    return _Level(mean=mean, weight=total, variance=variance)


# ── Public estimate ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Estimate:
    """A predicted duration with the uncertainty and the reason behind it."""

    minutes: int
    #: Optimistic / pessimistic bounds (roughly one sigma either side).
    low_minutes: int
    high_minutes: int
    #: 0..1. How much the planner should trust ``minutes``.
    confidence: float
    #: Multiplicative correction applied to the raw estimate.
    ratio: float
    #: Which level of the hierarchy carried the evidence.
    basis: str
    #: Human-readable, surfaced in the "why was this scheduled" panel.
    explanation: str

    @property
    def buffer_minutes(self) -> int:
        """Slack the planner should reserve for this task overrunning."""
        return max(0, self.high_minutes - self.minutes)


# ── The model ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EstimationModel:
    """Fitted per-student effort model. Immutable; rebuild to update.

    Construct via :func:`build_estimation_model`, which does the fitting.
    """

    global_level: _Level = field(default_factory=_Level)
    course_levels: dict[str, _Level] = field(default_factory=dict)
    kind_levels: dict[str, _Level] = field(default_factory=dict)
    course_kind_levels: dict[tuple[str, str], _Level] = field(default_factory=dict)
    sample_size: int = 0
    #: Median completed session length, in minutes — the student's measured
    #: single-sitting capacity. Distinct from bias: it governs how work is
    #: chopped up, not how long it takes in total.
    stamina_minutes: int = 45
    #: Fraction of started sessions the student saw through, 0..1 or None.
    session_completion_rate: float | None = None

    # ── Prediction ────────────────────────────────────────────────────

    def _resolve(self, course: str, kind: str) -> tuple[_Level, str]:
        """Walk the hierarchy from most to least specific, shrinking."""
        course, kind = _norm(course), _norm(kind)

        root = _Level(
            mean=self.global_level.mean,
            weight=self.global_level.weight,
            variance=self.global_level.variance or DEFAULT_LOG_SIGMA ** 2,
        ).shrunk_toward(
            _Level(mean=0.0, weight=GLOBAL_PRIOR_STRENGTH, variance=DEFAULT_LOG_SIGMA ** 2),
            GLOBAL_PRIOR_STRENGTH,
        )

        best, basis = root, "global"

        kind_level = self.kind_levels.get(kind)
        if kind_level is not None:
            best = kind_level.shrunk_toward(best, SHRINKAGE_STRENGTH)
            basis = "kind"

        course_level = self.course_levels.get(course)
        if course_level is not None:
            best = course_level.shrunk_toward(best, SHRINKAGE_STRENGTH)
            basis = "course"

        ck = self.course_kind_levels.get((course, kind))
        if ck is not None:
            best = ck.shrunk_toward(best, SHRINKAGE_STRENGTH)
            basis = "course+kind"

        return best, basis

    def predict(
        self,
        estimated_minutes: int | None,
        course: str = "",
        kind: str = "",
        points_possible: float | None = None,
    ) -> Estimate:
        """Correct a raw estimate using everything the student has taught us."""
        raw = int(estimated_minutes or 0)
        if raw <= 0:
            raw = baseline_minutes(kind, points_possible)

        level, basis = self._resolve(course, kind)
        ratio = math.exp(_clamp_log(level.mean))
        sigma = math.sqrt(max(level.variance, SIGMA_BOUNDS[0] ** 2))
        sigma = max(SIGMA_BOUNDS[0], min(SIGMA_BOUNDS[1], sigma))

        # Thin evidence means the spread of the *mean* matters too, not just
        # the spread of the observations. Standard error of a weighted mean.
        se = sigma / math.sqrt(max(1.0, level.weight + GLOBAL_PRIOR_STRENGTH))
        spread = math.sqrt(sigma ** 2 + se ** 2)

        centre = _clip(raw * ratio)
        low = _clip(raw * math.exp(_clamp_log(level.mean - spread)))
        high = _clip(raw * math.exp(_clamp_log(level.mean + spread)))

        confidence = round(min(1.0, level.weight / CONFIDENT_SAMPLES), 3)

        return Estimate(
            minutes=centre,
            low_minutes=min(low, centre),
            high_minutes=max(high, centre),
            confidence=confidence,
            ratio=round(ratio, 3),
            basis=basis,
            explanation=self._explain(raw, centre, ratio, confidence, basis, course, kind),
        )

    def _explain(
        self,
        raw: int,
        centre: int,
        ratio: float,
        confidence: float,
        basis: str,
        course: str,
        kind: str,
    ) -> str:
        if confidence < 0.2 or abs(ratio - 1.0) < 0.08:
            return f"Estimated at {centre} min."
        direction = "longer" if ratio > 1 else "less time"
        pct = int(round(abs(ratio - 1.0) * 100))
        scope = {
            "course+kind": f"{course.title()} {kind or 'work'}",
            "course": course.title() or "this course",
            "kind": f"{kind or 'this kind of'} work",
            "global": "your work",
        }[basis]
        return (
            f"{raw} min planned → {centre} min: you take about {pct}% "
            f"{direction} than estimated on {scope}."
        )

    # ── Introspection for the UI / prompts ────────────────────────────

    @property
    def has_signal(self) -> bool:
        return self.global_level.weight >= 2.0

    def summary(self) -> dict[str, Any]:
        """Serialisable snapshot for the API and the "how it learned" panel."""
        ratio = math.exp(_clamp_log(self._resolve("", "")[0].mean))
        return {
            "sample_size": self.sample_size,
            "effective_samples": round(self.global_level.weight, 2),
            "overall_ratio": round(ratio, 3),
            "stamina_minutes": self.stamina_minutes,
            "session_completion_rate": self.session_completion_rate,
            "courses": {
                c: round(math.exp(_clamp_log(self._resolve(c, "")[0].mean)), 3)
                for c in sorted(self.course_levels)
            },
            "kinds": {
                k: round(math.exp(_clamp_log(self._resolve("", k)[0].mean)), 3)
                for k in sorted(self.kind_levels)
            },
        }

    def to_prompt(self) -> str:
        """Numeric facts an LLM can plan against. Empty when we know nothing."""
        if not self.has_signal:
            return ""
        bits: list[str] = []
        overall = math.exp(_clamp_log(self._resolve("", "")[0].mean))
        if abs(overall - 1.0) >= 0.1:
            pct = int(round(abs(overall - 1.0) * 100))
            word = "longer than" if overall > 1 else "less time than"
            bits.append(
                f"This student takes about {pct}% {word} they estimate overall — "
                f"size the work accordingly."
            )
        slow = sorted(
            (
                (c, math.exp(_clamp_log(self._resolve(c, "")[0].mean)))
                for c in self.course_levels
            ),
            key=lambda kv: -kv[1],
        )
        over = [f"{c.title()} ({r:.2f}x)" for c, r in slow if r >= 1.2][:3]
        under = [f"{c.title()} ({r:.2f}x)" for c, r in slow if r <= 0.8][-3:]
        if over:
            bits.append(f"Consistently runs over on: {', '.join(over)}.")
        if under:
            bits.append(f"Consistently finishes early on: {', '.join(under)}.")
        bits.append(
            f"Their measured single-sitting focus length is about "
            f"{self.stamina_minutes} minutes."
        )
        if self.session_completion_rate is not None and self.session_completion_rate < 0.6:
            bits.append(
                f"They finish only ~{int(self.session_completion_rate * 100)}% of "
                f"sessions they start — prefer shorter sessions."
            )
        return (
            "\n=== MEASURED EFFORT MODEL (this student's own history) ===\n"
            + "\n".join(f"  - {b}" for b in bits)
            + "\n=== END MEASURED EFFORT MODEL ===\n"
        )


def _clip(minutes: float) -> int:
    return int(max(MIN_PREDICTED_MINUTES, min(MAX_PREDICTED_MINUTES, round(minutes))))


# ── Fitting ───────────────────────────────────────────────────────────


def build_estimation_model(
    feedback_rows: Iterable[Mapping[str, Any]] | None = None,
    session_rows: Iterable[Mapping[str, Any]] | None = None,
    now: datetime | None = None,
) -> EstimationModel:
    """Fit an :class:`EstimationModel` from the student's own history.

    ``feedback_rows`` are ``TaskFeedback``-shaped dicts. ``session_rows`` are
    Active-study session records — richer, because they also carry whether
    the student actually finished the sitting.

    Returns a zeroed model when there is nothing to learn from; every
    consumer treats that as "behave as if personalization did not exist".
    """
    now = now or utcnow()
    feedback_obs = _coerce_observations(feedback_rows)
    session_obs = _coerce_observations(session_rows)
    observations = feedback_obs + session_obs

    # Bias is fit only on finished work. An abandoned session's duration is a
    # lower bound on the task's length, and treating a lower bound as a
    # measurement teaches the model that everything is faster than it is.
    fitted = [o for o in observations if o.completed]

    global_level = _fit_level(fitted, now)

    by_course: dict[str, list[Observation]] = {}
    by_kind: dict[str, list[Observation]] = {}
    by_ck: dict[tuple[str, str], list[Observation]] = {}
    for o in fitted:
        if o.course:
            by_course.setdefault(o.course, []).append(o)
        if o.kind:
            by_kind.setdefault(o.kind, []).append(o)
        if o.course and o.kind:
            by_ck.setdefault((o.course, o.kind), []).append(o)

    # Stamina is measured from *sittings*, never from whole-task durations.
    # A task-feedback row says a task took 140 minutes; it does not say the
    # student sat still for 140 minutes, and reading it that way produces a
    # two-hour "focus length" for someone who works in half-hour bursts.
    stamina = _median_int(
        [o.actual for o in session_obs if o.completed and o.actual > 0],
        default=45,
        bounds=(15, 120),
    )

    started = len(observations)
    finished = sum(1 for o in observations if o.completed)
    completion_rate = round(finished / started, 3) if started >= 4 else None

    return EstimationModel(
        global_level=global_level,
        course_levels={c: _fit_level(v, now) for c, v in by_course.items()},
        kind_levels={k: _fit_level(v, now) for k, v in by_kind.items()},
        course_kind_levels={ck: _fit_level(v, now) for ck, v in by_ck.items()},
        sample_size=len(fitted),
        stamina_minutes=stamina,
        session_completion_rate=completion_rate,
    )


def _median_int(values: Sequence[int], default: int, bounds: tuple[int, int]) -> int:
    if len(values) < 3:
        return default
    ordered = sorted(values)
    mid = len(ordered) // 2
    med = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return int(max(bounds[0], min(bounds[1], round(med))))
