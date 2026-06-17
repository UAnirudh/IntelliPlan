"""Regression: the Command Center serializer must emit source_ref.

The "mark done" checkmark silently broke because planned_task_to_dict
dropped source_ref. The template reads task.source_ref to decide whether a
card routes to /tasks/manual/update (manual) or /dismiss (LMS); with the
field missing, every card looked source='manual' ref='' and mis-routed,
so completions never persisted. Pin the field so it can't regress.
"""

from datetime import date, datetime

from intelliplan.api.serialize import planned_task_to_dict, today_to_dict
from intelliplan.domain.assignment import (
    Assignment,
    AssignmentKind,
    AssignmentStatus,
)
from intelliplan.domain.plan import (
    BriefingText,
    DayLoad,
    HealthScore,
    PlannedTask,
    PriorityScore,
    PriorityTier,
    ReasonChip,
    TodayPayload,
    WorkloadForecast,
)

TODAY = date(2026, 6, 16)
NOW = datetime(2026, 6, 16, 7, 56, 0)


def _planned(source: str, source_ref: str) -> PlannedTask:
    a = Assignment(
        id=f"{source}-{source_ref}",
        title="Project 3",
        course="Math 101",
        due_date=TODAY,
        kind=AssignmentKind.HOMEWORK,
        status=AssignmentStatus.NOT_STARTED,
        points_possible=10.0,
        est_minutes=60,
        source=source,
        source_ref=source_ref,
    )
    score = PriorityScore(
        score=70,
        tier=PriorityTier.FOCUS,
        rationale=(ReasonChip(key="urgency", weight=30, reason="Overdue"),),
    )
    return PlannedTask(assignment=a, priority=score, why_now="Overdue", deep_link="/priority")


def test_planned_task_dict_includes_source_ref():
    d = planned_task_to_dict(_planned("manual", "42"))
    assert "source_ref" in d, "serializer must emit source_ref for routing"
    assert d["source_ref"] == "42"
    assert d["source"] == "manual"


def test_today_dict_plan_items_carry_source_ref():
    payload = TodayPayload(
        generated_at=NOW,
        student_name="Anirudh",
        student_grade_level="11th grade",
        personalization_enabled=True,
        briefing=BriefingText(headline="H", body="B", tone="calm-focused",
                              generated_by="test", cached=True),
        plan=(_planned("manual", "7"), _planned("studentvue", "sv-99")),
        forecast=WorkloadForecast(
            days=(DayLoad(day=TODAY, committed_minutes=60, prework_minutes=0,
                          available_minutes=120, stress=0.5),),
            heaviest_day=TODAY, summary="busy",
        ),
        health=HealthScore(score=76, delta_vs_yesterday=0, tier="steady",
                           components=(), summary="ok"),
    )
    data = today_to_dict(payload)
    refs = [item["source_ref"] for item in data["plan"]]
    assert refs == ["7", "sv-99"], "every plan item must carry its source_ref"
