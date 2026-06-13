"""Serializers — domain dataclasses → JSON-safe dicts.

Shape matches docs/command-center/03-api-design.md exactly. Versioned
via ``meta.schema_version``; additive changes only within v1.
"""

from __future__ import annotations

from typing import Any

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
