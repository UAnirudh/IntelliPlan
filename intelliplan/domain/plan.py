"""Plan-side domain types: scores, plan rows, forecast, briefing.

These are the shapes the Command Center renders and the API serialises.
Frozen dataclasses keep the intelligence engine deterministic — same
input, same object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum

from intelliplan.domain.assignment import Assignment


class PriorityTier(str, Enum):
    """Coarse visual bucket. Derived from the 0..100 score by
    :func:`PriorityScore.tier_for`. Kept stable for telemetry."""

    CRITICAL = "critical"  # 85+
    FOCUS = "focus"        # 65–84
    STEADY = "steady"      # 40–64
    LIGHT = "light"        # 0–39


@dataclass(frozen=True, slots=True)
class ReasonChip:
    """One contributing component of a score, shown next to the number.

    ``key`` is a stable identifier — never rename. ``weight`` is the
    component's contribution to the parent score. ``reason`` is the
    one-line human-readable explanation surfaced in the UI.
    """

    key: str
    weight: int
    reason: str


@dataclass(frozen=True, slots=True)
class PriorityScore:
    """A 0..100 explainable priority score for a single assignment."""

    score: int
    tier: PriorityTier
    rationale: tuple[ReasonChip, ...]

    @staticmethod
    def tier_for(score: int) -> PriorityTier:
        if score >= 85:
            return PriorityTier.CRITICAL
        if score >= 65:
            return PriorityTier.FOCUS
        if score >= 40:
            return PriorityTier.STEADY
        return PriorityTier.LIGHT


@dataclass(frozen=True, slots=True)
class PlannedTask:
    """A row in Today's Action Plan.

    Composes :class:`Assignment` with its :class:`PriorityScore` and the
    Narrator's one-line ``why_now``. The deep link points back into the
    existing IntelliPlan task surfaces (we are a read-only front door).
    """

    assignment: Assignment
    priority: PriorityScore
    why_now: str
    deep_link: str


@dataclass(frozen=True, slots=True)
class DayLoad:
    """Estimated workload for a single calendar day."""

    day: date
    committed_minutes: int  # tasks due that day
    prework_minutes: int    # recommended pre-work for upcoming items
    available_minutes: int  # from student's availability profile
    stress: float           # 0..1, max(0, (committed+prework - available) / max(available,1))


@dataclass(frozen=True, slots=True)
class WorkloadForecast:
    """Seven-day forecast used by the workload card."""

    days: tuple[DayLoad, ...]
    heaviest_day: date | None
    summary: str


@dataclass(frozen=True, slots=True)
class HealthComponent:
    """One contributing component of the Academic Health Score."""

    key: str
    value: float       # raw measurement (count, ratio, …)
    impact: int        # contribution to the score (negative is bad)
    reason: str


@dataclass(frozen=True, slots=True)
class HealthScore:
    """0..100 Academic Health Score with deltas vs. yesterday."""

    score: int
    delta_vs_yesterday: int
    tier: str          # "thriving" | "steady" | "watch" | "at_risk"
    components: tuple[HealthComponent, ...]
    summary: str


@dataclass(frozen=True, slots=True)
class BriefingText:
    """Output of the Narrator. The only AI-produced text on the page."""

    headline: str
    body: str
    tone: str
    generated_by: str        # model id, or "fallback-template"
    cached: bool = False


@dataclass(frozen=True, slots=True)
class TodayPayload:
    """The complete Command Center payload — what ``/api/today`` returns.

    Built by :class:`intelliplan.services.today.TodayService`. Frozen so
    we can hash it for cache invalidation in
    :class:`intelliplan.services.briefing.BriefingService`.
    """

    generated_at: datetime
    student_name: str
    student_grade_level: str
    personalization_enabled: bool
    briefing: BriefingText
    plan: tuple[PlannedTask, ...]
    forecast: WorkloadForecast
    health: HealthScore
    schema_version: int = 1
    cache_age_seconds: int = 0
    extras: dict[str, str] = field(default_factory=dict)
