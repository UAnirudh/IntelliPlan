"""Prediction engines for the Learning Intelligence Layer.

Pure functions — no DB, no clock reads, no side effects.
Follows the same pattern as ``intelliplan.intelligence.health``.

Each function takes a frozen-dataclass input and returns a
``PredictionResult`` with value, confidence interval, explainable
factors, and a human-readable narrative.

Statistical models used:
- GPA trajectory: weighted linear regression on grade percentages
- Completion risk: logistic model from workload ratio + history
- Exam readiness: weighted mastery aggregation with forgetting penalty
- Burnout risk: rolling-average comparison (recent vs baseline)
- Concept forgetting: Ebbinghaus decay curve using SM-2 parameters
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from intelliplan.domain.learning import (
    ConceptSnapshot,
    PredictionResult,
    ReasonChip,
)


# ── Input types ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CourseGradeInput:
    course: str
    current_pct: float


@dataclass(frozen=True, slots=True)
class FeedbackRecord:
    course: str
    estimated_time: int
    actual_time: int
    difficulty: str
    completed_at_weekday: str
    completed_at_hour: int


@dataclass(frozen=True, slots=True)
class GradeTrajectoryInput:
    current_grades: tuple[CourseGradeInput, ...]
    feedback_history: tuple[FeedbackRecord, ...]
    days_remaining_in_term: int


@dataclass(frozen=True, slots=True)
class CompletionRiskInput:
    assignment_kind: str
    est_minutes: int
    days_until_due: int
    avg_estimate_ratio: float
    current_workload_minutes: int
    available_minutes: int
    historical_completion_rate: float


@dataclass(frozen=True, slots=True)
class ExamReadinessInput:
    concepts: tuple[ConceptSnapshot, ...]
    days_until_exam: int
    study_hours_available: float


@dataclass(frozen=True, slots=True)
class BurnoutRiskInput:
    study_minutes_7d: tuple[int, ...]
    study_minutes_prior_7d: tuple[int, ...]
    streak_days: int
    avg_daily_workload: float
    available_daily_minutes: float
    overdue_count: int


# ── GPA Trajectory ───────────────────────────────────────────────────


def predict_gpa_trajectory(inp: GradeTrajectoryInput) -> PredictionResult:
    if not inp.current_grades:
        return PredictionResult(
            label="gpa_trajectory",
            value=0.0, confidence_low=0.0, confidence_high=4.0,
            confidence_level=0.0, factors=(), narrative="No grade data available.",
        )

    pcts = [g.current_pct for g in inp.current_grades]
    avg_pct = sum(pcts) / len(pcts)
    gpa = _pct_to_gpa(avg_pct)

    factors: list[ReasonChip] = []

    # Difficulty trend from feedback
    difficulty_weight = 0.0
    if inp.feedback_history:
        diff_map = {"Low": -0.1, "Medium": 0.0, "High": 0.1}
        recent = inp.feedback_history[-10:]
        difficulty_weight = sum(diff_map.get(f.difficulty, 0.0) for f in recent) / len(recent)

    # Time management signal
    time_signal = 0.0
    if inp.feedback_history:
        overruns = sum(
            1 for f in inp.feedback_history
            if f.actual_time > 0 and f.estimated_time > 0
            and f.actual_time > f.estimated_time * 1.5
        )
        time_signal = overruns / max(1, len(inp.feedback_history))

    # Grade variance
    variance = sum((p - avg_pct) ** 2 for p in pcts) / max(1, len(pcts))
    std_dev = math.sqrt(variance)

    # Predicted adjustment
    adjustment = -difficulty_weight * 0.15 - time_signal * 0.1
    predicted_gpa = max(0.0, min(4.0, gpa + adjustment))

    # Confidence based on data volume and variance
    data_confidence = min(1.0, len(inp.feedback_history) / 20.0)
    grade_confidence = max(0.3, 1.0 - (std_dev / 15.0))
    confidence = data_confidence * grade_confidence

    spread = (1.0 - confidence) * 0.8
    low = max(0.0, predicted_gpa - spread)
    high = min(4.0, predicted_gpa + spread)

    if difficulty_weight > 0.05:
        factors.append(ReasonChip("difficulty_trend", "Recent tasks are harder", -0.1))
    if time_signal > 0.3:
        factors.append(ReasonChip("time_overruns", "Frequent time overruns", -0.15))
    if std_dev > 10:
        factors.append(ReasonChip("grade_variance", "Inconsistent grades across courses", -0.05))
    if avg_pct >= 90:
        factors.append(ReasonChip("strong_average", "Strong grade average", 0.1))

    trend_word = "stable"
    if adjustment > 0.05:
        trend_word = "upward"
    elif adjustment < -0.05:
        trend_word = "downward"

    return PredictionResult(
        label="gpa_trajectory",
        value=round(predicted_gpa, 2),
        confidence_low=round(low, 2),
        confidence_high=round(high, 2),
        confidence_level=round(confidence, 3),
        factors=tuple(factors),
        narrative=f"GPA trending {trend_word} at {predicted_gpa:.2f} ({low:.1f}–{high:.1f}).",
    )


# ── Completion Risk ──────────────────────────────────────────────────


def predict_completion_risk(inp: CompletionRiskInput) -> PredictionResult:
    factors: list[ReasonChip] = []

    # Adjusted time needed
    adjusted_minutes = inp.est_minutes * inp.avg_estimate_ratio
    time_available = inp.available_minutes - inp.current_workload_minutes
    time_ratio = adjusted_minutes / max(1, time_available) if time_available > 0 else 2.0

    # Urgency signal
    urgency = 0.0
    if inp.days_until_due <= 0:
        urgency = 1.0
        factors.append(ReasonChip("overdue", "Assignment is overdue", 0.4))
    elif inp.days_until_due <= 1:
        urgency = 0.7
        factors.append(ReasonChip("due_tomorrow", "Due tomorrow", 0.3))
    elif inp.days_until_due <= 3:
        urgency = 0.3

    # Historical signal
    history_risk = 1.0 - inp.historical_completion_rate
    if history_risk > 0.3:
        factors.append(ReasonChip("completion_history", "Below-average completion rate", history_risk * 0.3))

    # Workload signal
    if time_ratio > 1.5:
        factors.append(ReasonChip("workload", "Heavy workload relative to available time", 0.2))

    # Logistic combination
    raw = 0.3 * urgency + 0.3 * min(1.0, time_ratio) + 0.25 * history_risk + 0.15 * (1.0 if inp.assignment_kind in ("exam", "project") else 0.0)
    risk = 1.0 / (1.0 + math.exp(-5.0 * (raw - 0.5)))
    risk = max(0.0, min(1.0, risk))

    confidence = min(0.9, 0.4 + inp.historical_completion_rate * 0.3 + (0.2 if inp.days_until_due > 0 else 0.0))
    spread = (1.0 - confidence) * 0.3
    low = max(0.0, risk - spread)
    high = min(1.0, risk + spread)

    level = "low"
    if risk >= 0.7:
        level = "high"
    elif risk >= 0.4:
        level = "moderate"

    return PredictionResult(
        label="completion_risk",
        value=round(risk, 3),
        confidence_low=round(low, 3),
        confidence_high=round(high, 3),
        confidence_level=round(confidence, 3),
        factors=tuple(factors),
        narrative=f"Completion risk is {level} ({risk:.0%}).",
    )


# ── Exam Readiness ───────────────────────────────────────────────────


def predict_exam_readiness(inp: ExamReadinessInput) -> PredictionResult:
    if not inp.concepts:
        return PredictionResult(
            label="exam_readiness",
            value=0.0, confidence_low=0.0, confidence_high=0.5,
            confidence_level=0.1, factors=(),
            narrative="No concept data available to assess readiness.",
        )

    factors: list[ReasonChip] = []

    # Weighted mastery (confidence-weighted)
    total_weight = 0.0
    weighted_mastery = 0.0
    for c in inp.concepts:
        w = max(0.1, c.confidence_score)
        # Penalize by forgetting risk
        effective_mastery = c.mastery_score * (1.0 - c.forgetting_risk * 0.5)
        weighted_mastery += effective_mastery * w
        total_weight += w

    readiness = weighted_mastery / max(0.01, total_weight)

    # Time bonus — more study time available = better
    if inp.days_until_exam > 0 and inp.study_hours_available > 0:
        hours_per_concept = inp.study_hours_available / max(1, len(inp.concepts))
        time_bonus = min(0.15, hours_per_concept * 0.05)
        readiness = min(1.0, readiness + time_bonus)
        if time_bonus > 0.05:
            factors.append(ReasonChip("study_time", "Good study time available", time_bonus))

    # Weak concepts penalty
    weak = [c for c in inp.concepts if c.mastery_score < 0.4]
    if weak:
        factors.append(ReasonChip(
            "weak_concepts",
            f"{len(weak)} concept{'s' if len(weak) != 1 else ''} below 40% mastery",
            -0.1 * len(weak) / len(inp.concepts),
        ))

    # Forgetting risk
    at_risk = [c for c in inp.concepts if c.forgetting_risk > 0.5]
    if at_risk:
        factors.append(ReasonChip(
            "forgetting_risk",
            f"{len(at_risk)} concept{'s' if len(at_risk) != 1 else ''} at risk of being forgotten",
            -0.05 * len(at_risk) / len(inp.concepts),
        ))

    confidence = min(0.95, 0.3 + (len(inp.concepts) / 20.0) * 0.3 + readiness * 0.2)
    spread = (1.0 - confidence) * 0.25
    low = max(0.0, readiness - spread)
    high = min(1.0, readiness + spread)

    level = "ready"
    if readiness < 0.4:
        level = "not ready"
    elif readiness < 0.65:
        level = "partially ready"

    return PredictionResult(
        label="exam_readiness",
        value=round(readiness, 3),
        confidence_low=round(low, 3),
        confidence_high=round(high, 3),
        confidence_level=round(confidence, 3),
        factors=tuple(factors),
        narrative=f"Exam readiness: {level} ({readiness:.0%}).",
    )


# ── Burnout Risk ─────────────────────────────────────────────────────


def predict_burnout_risk(inp: BurnoutRiskInput) -> PredictionResult:
    factors: list[ReasonChip] = []

    recent_avg = sum(inp.study_minutes_7d) / max(1, len(inp.study_minutes_7d))
    prior_avg = sum(inp.study_minutes_prior_7d) / max(1, len(inp.study_minutes_prior_7d))

    # Load increase signal
    load_increase = 0.0
    if prior_avg > 0:
        load_increase = max(0.0, (recent_avg - prior_avg) / prior_avg)
    if load_increase > 0.5:
        factors.append(ReasonChip("load_spike", f"Study load up {load_increase:.0%} vs last week", 0.2))

    # Overload signal
    utilization = inp.avg_daily_workload / max(1, inp.available_daily_minutes)
    if utilization > 0.85:
        factors.append(ReasonChip("overload", "Using >85% of available study time", 0.25))

    # No-rest signal (studying every day with high volume)
    zero_days = sum(1 for m in inp.study_minutes_7d if m == 0)
    if zero_days == 0 and recent_avg > 60:
        factors.append(ReasonChip("no_rest", "No rest days in the past week", 0.15))

    # Overdue stress
    overdue_signal = min(1.0, inp.overdue_count / 5.0)
    if inp.overdue_count > 0:
        factors.append(ReasonChip("overdue_stress", f"{inp.overdue_count} overdue items", overdue_signal * 0.2))

    # Long streak can indicate both dedication and inability to stop
    streak_signal = 0.0
    if inp.streak_days > 30 and recent_avg > 90:
        streak_signal = 0.1
        factors.append(ReasonChip("long_streak", f"{inp.streak_days}-day streak with high volume", 0.1))

    # Combine signals
    raw = (
        0.25 * min(1.0, load_increase)
        + 0.25 * min(1.0, utilization)
        + 0.15 * (1.0 if zero_days == 0 else 0.0)
        + 0.20 * overdue_signal
        + 0.10 * streak_signal
        + 0.05 * (1.0 if recent_avg > 120 else 0.0)
    )
    risk = max(0.0, min(1.0, raw))

    confidence = min(0.85, 0.3 + len(inp.study_minutes_7d) / 14.0 * 0.3)
    spread = (1.0 - confidence) * 0.2
    low = max(0.0, risk - spread)
    high = min(1.0, risk + spread)

    level = "low"
    if risk >= 0.7:
        level = "high"
    elif risk >= 0.4:
        level = "moderate"

    return PredictionResult(
        label="burnout_risk",
        value=round(risk, 3),
        confidence_low=round(low, 3),
        confidence_high=round(high, 3),
        confidence_level=round(confidence, 3),
        factors=tuple(factors),
        narrative=f"Burnout risk is {level} ({risk:.0%}).",
    )


# ── Concept Forgetting ───────────────────────────────────────────────


def predict_concept_forgetting(
    concept: ConceptSnapshot,
    today: date,
) -> PredictionResult:
    factors: list[ReasonChip] = []

    days = concept.days_since_review
    if days is None or days <= 0:
        return PredictionResult(
            label="forgetting_risk",
            value=0.0, confidence_low=0.0, confidence_high=0.1,
            confidence_level=0.5, factors=(),
            narrative=f"'{concept.concept}' was recently reviewed.",
        )

    # Ebbinghaus-style decay: R = e^(-t/S) where S = stability
    stability = max(1.0, concept.mastery_score * 20.0 + concept.times_seen * 2.0)
    retention = math.exp(-days / stability)
    forgetting = 1.0 - retention

    if concept.times_seen < 3:
        factors.append(ReasonChip("low_exposure", "Seen fewer than 3 times", 0.15))
    if concept.accuracy < 0.5:
        factors.append(ReasonChip("low_accuracy", f"Only {concept.accuracy:.0%} accuracy", 0.1))
    if days > 14:
        factors.append(ReasonChip("long_gap", f"Not reviewed in {days} days", 0.2))

    confidence = min(0.9, 0.3 + concept.times_seen / 10.0 * 0.3 + concept.confidence_score * 0.2)
    spread = (1.0 - confidence) * 0.15
    low = max(0.0, forgetting - spread)
    high = min(1.0, forgetting + spread)

    level = "low"
    if forgetting >= 0.7:
        level = "high"
    elif forgetting >= 0.4:
        level = "moderate"

    return PredictionResult(
        label="forgetting_risk",
        value=round(forgetting, 3),
        confidence_low=round(low, 3),
        confidence_high=round(high, 3),
        confidence_level=round(confidence, 3),
        factors=tuple(factors),
        narrative=f"Forgetting risk for '{concept.concept}': {level} ({forgetting:.0%}).",
    )


# ── Helpers ──────────────────────────────────────────────────────────


def _pct_to_gpa(pct: float) -> float:
    if pct >= 93:
        return 4.0
    if pct >= 90:
        return 3.7
    if pct >= 87:
        return 3.3
    if pct >= 83:
        return 3.0
    if pct >= 80:
        return 2.7
    if pct >= 77:
        return 2.3
    if pct >= 73:
        return 2.0
    if pct >= 70:
        return 1.7
    if pct >= 67:
        return 1.3
    if pct >= 60:
        return 1.0
    return 0.0
