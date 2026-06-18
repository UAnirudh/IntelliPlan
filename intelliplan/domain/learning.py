"""Domain types for the Learning Intelligence Layer.

Frozen dataclasses — pure data, no ORM, no Flask, no I/O.
Follows the same conventions as ``intelliplan.domain.assignment``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True, slots=True)
class ReasonChip:
    """One explainable factor behind a prediction."""

    key: str
    description: str
    impact: float


@dataclass(frozen=True, slots=True)
class ConceptSnapshot:
    """Point-in-time view of a single concept's mastery state."""

    subject: str
    topic: str
    concept: str
    mastery_score: float
    confidence_score: float
    times_seen: int
    accuracy: float
    days_since_review: int | None
    forgetting_risk: float


@dataclass(frozen=True, slots=True)
class StudentProfileSnapshot:
    """Denormalized student profile for engines and API responses."""

    user_id: int
    learning_pace: str
    strongest_subjects: tuple[str, ...]
    weakest_subjects: tuple[str, ...]
    preferred_methods: tuple[str, ...]
    retention_score: float
    avg_estimate_ratio: float
    engagement_score: float
    gpa_snapshot: float | None


@dataclass(frozen=True, slots=True)
class PredictionResult:
    """A prediction with confidence interval and explainable factors."""

    label: str
    value: float
    confidence_low: float
    confidence_high: float
    confidence_level: float
    factors: tuple[ReasonChip, ...]
    narrative: str


@dataclass(frozen=True, slots=True)
class SubjectMastery:
    """Aggregate mastery for one subject."""

    subject: str
    avg_mastery: float
    concept_count: int
    weakest_concept: str
    strongest_concept: str


@dataclass(frozen=True, slots=True)
class LearningDashboardPayload:
    """Full dashboard response — everything the Command Center needs."""

    profile: StudentProfileSnapshot
    strongest_concepts: tuple[ConceptSnapshot, ...]
    weakest_concepts: tuple[ConceptSnapshot, ...]
    forgetting_soon: tuple[ConceptSnapshot, ...]
    mastery_by_subject: tuple[SubjectMastery, ...]
    predictions: tuple[PredictionResult, ...]
    study_trend: dict[str, int]
    retention_trend: dict[str, float]
    event_count_7d: int
