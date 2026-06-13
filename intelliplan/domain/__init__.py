"""Domain types — frozen dataclasses shared across the package.

Importing :mod:`intelliplan.domain` is free and has no side effects.
"""

from intelliplan.domain.assignment import Assignment, AssignmentKind, AssignmentStatus
from intelliplan.domain.plan import (
    BriefingText,
    DayLoad,
    HealthComponent,
    HealthScore,
    PlannedTask,
    PriorityScore,
    PriorityTier,
    ReasonChip,
    TodayPayload,
    WorkloadForecast,
)

__all__ = [
    "Assignment",
    "AssignmentKind",
    "AssignmentStatus",
    "BriefingText",
    "DayLoad",
    "HealthComponent",
    "HealthScore",
    "PlannedTask",
    "PriorityScore",
    "PriorityTier",
    "ReasonChip",
    "TodayPayload",
    "WorkloadForecast",
]
