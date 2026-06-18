"""Serializers — domain dataclasses → JSON-safe dicts.

Shape matches docs/command-center/03-api-design.md exactly. Versioned
via ``meta.schema_version``; additive changes only within v1.
"""

from __future__ import annotations

from typing import Any

from intelliplan.domain.learning import (
    ConceptSnapshot,
    LearningDashboardPayload,
    PredictionResult,
    SubjectMastery,
)
from intelliplan.domain.plan import (
    HealthScore,
    PlannedTask,
    TodayPayload,
    WorkloadForecast,
)


def planned_task_to_dict(t: PlannedTask) -> dict[str, Any]:
    a = t.assignment
    return {
        "id": a.id,
        "title": a.title,
        "course": a.course,
        "due_date": a.due_date.isoformat() if a.due_date else None,
        "est_minutes": a.est_minutes,
        "status": a.status.value,
        "kind": a.kind.value,
        "source": a.source,
        "source_ref": a.source_ref,
        "priority": {
            "score": t.priority.score,
            "tier": t.priority.tier.value,
            "rationale": [
                {"key": c.key, "weight": c.weight, "reason": c.reason}
                for c in t.priority.rationale
            ],
        },
        "why_now": t.why_now,
        "deep_link": t.deep_link,
    }


def forecast_to_dict(f: WorkloadForecast) -> dict[str, Any]:
    return {
        "days": [
            {
                "date": d.day.isoformat(),
                "committed_min": d.committed_minutes,
                "prework_min": d.prework_minutes,
                "available_min": d.available_minutes,
                "stress": round(d.stress, 3),
            }
            for d in f.days
        ],
        "heaviest_date": f.heaviest_day.isoformat() if f.heaviest_day else None,
        "summary": f.summary,
    }


def health_to_dict(h: HealthScore) -> dict[str, Any]:
    return {
        "score": h.score,
        "delta_vs_yesterday": h.delta_vs_yesterday,
        "tier": h.tier,
        "components": [
            {"key": c.key, "value": c.value, "impact": c.impact, "reason": c.reason}
            for c in h.components
        ],
        "summary": h.summary,
    }


def _greeting(dt: Any) -> str:
    try:
        h = dt.hour if hasattr(dt, "hour") else 12
    except Exception:
        h = 12
    if h < 12:
        return "Good morning"
    if h < 17:
        return "Good afternoon"
    return "Good evening"


def today_to_dict(p: TodayPayload, *, cache_age_seconds: int = 0) -> dict[str, Any]:
    return {
        "generated_at": p.generated_at.replace(microsecond=0).isoformat() + "Z",
        "student": {
            "name": p.student_name,
            "grade_level": p.student_grade_level,
            "personalization_enabled": p.personalization_enabled,
            "greeting": _greeting(p.generated_at),
        },
        "briefing": {
            "headline": p.briefing.headline,
            "body": p.briefing.body,
            "tone": p.briefing.tone,
            "generated_by": p.briefing.generated_by,
            "cached": p.briefing.cached,
        },
        "plan": [planned_task_to_dict(t) for t in p.plan],
        "forecast": forecast_to_dict(p.forecast),
        "health": health_to_dict(p.health),
        "meta": {
            "schema_version": p.schema_version,
            "cache_age_seconds": cache_age_seconds,
        },
    }


# ── Learning Intelligence Layer serializers ──────────────────────────


def concept_snapshot_to_dict(c: ConceptSnapshot) -> dict[str, Any]:
    return {
        "subject": c.subject,
        "topic": c.topic,
        "concept": c.concept,
        "mastery_score": round(c.mastery_score, 3),
        "confidence_score": round(c.confidence_score, 3),
        "times_seen": c.times_seen,
        "accuracy": round(c.accuracy, 3),
        "days_since_review": c.days_since_review,
        "forgetting_risk": round(c.forgetting_risk, 3),
    }


def prediction_to_dict(p: PredictionResult) -> dict[str, Any]:
    return {
        "label": p.label,
        "value": p.value,
        "confidence_low": p.confidence_low,
        "confidence_high": p.confidence_high,
        "confidence_level": p.confidence_level,
        "factors": [
            {"key": f.key, "description": f.description, "impact": f.impact}
            for f in p.factors
        ],
        "narrative": p.narrative,
    }


def subject_mastery_to_dict(s: SubjectMastery) -> dict[str, Any]:
    return {
        "subject": s.subject,
        "avg_mastery": s.avg_mastery,
        "concept_count": s.concept_count,
        "weakest_concept": s.weakest_concept,
        "strongest_concept": s.strongest_concept,
    }


def learning_dashboard_to_dict(d: LearningDashboardPayload) -> dict[str, Any]:
    return {
        "profile": {
            "user_id": d.profile.user_id,
            "learning_pace": d.profile.learning_pace,
            "strongest_subjects": list(d.profile.strongest_subjects),
            "weakest_subjects": list(d.profile.weakest_subjects),
            "preferred_methods": list(d.profile.preferred_methods),
            "retention_score": d.profile.retention_score,
            "avg_estimate_ratio": d.profile.avg_estimate_ratio,
            "engagement_score": d.profile.engagement_score,
            "gpa_snapshot": d.profile.gpa_snapshot,
        },
        "strongest_concepts": [concept_snapshot_to_dict(c) for c in d.strongest_concepts],
        "weakest_concepts": [concept_snapshot_to_dict(c) for c in d.weakest_concepts],
        "forgetting_soon": [concept_snapshot_to_dict(c) for c in d.forgetting_soon],
        "mastery_by_subject": [subject_mastery_to_dict(s) for s in d.mastery_by_subject],
        "predictions": [prediction_to_dict(p) for p in d.predictions],
        "study_trend": d.study_trend,
        "retention_trend": d.retention_trend,
        "event_count_7d": d.event_count_7d,
    }
