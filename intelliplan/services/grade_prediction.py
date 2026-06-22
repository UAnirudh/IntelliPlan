"""AI-powered grade prediction.

Two-stage prediction:

1. **Baseline statistical forecast** — weighted moving average of past
   percentages per course, with a recency bias (recent assignments
   matter more than first-of-semester ones). This always returns
   something, even when the AI provider is offline.

2. **AI refinement** — when ``ai_provider.chat_json`` is available, we
   send a compact JSON brief (recent scores, trend direction, upcoming
   workload) and ask the model for a calibrated forecast + a one-sentence
   narrative the UI can show next to the number.

The service is pure — it takes the data it needs as arguments. The
blueprint in :mod:`intelliplan.api.grade_prediction` is what reads
SQLAlchemy rows and converts them into the dicts this module consumes.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable


@dataclass(frozen=True)
class CoursePrediction:
    course: str
    current_percent: float | None
    predicted_percent: float
    confidence: float            # 0.0 – 1.0
    trend: str                   # "improving" | "steady" | "slipping"
    narrative: str
    sample_size: int
    method: str                  # "ai" | "statistical" | "insufficient"
    components: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "course": self.course,
            "current_percent": self.current_percent,
            "predicted_percent": round(self.predicted_percent, 1),
            "confidence": round(self.confidence, 2),
            "trend": self.trend,
            "narrative": self.narrative,
            "sample_size": self.sample_size,
            "method": self.method,
            "components": self.components,
        }


def _percent(score: float | None, possible: float | None) -> float | None:
    if score is None or not possible:
        return None
    try:
        return max(0.0, min(100.0, (float(score) / float(possible)) * 100.0))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _trend(percents: list[float]) -> str:
    if len(percents) < 3:
        return "steady"
    head = statistics.mean(percents[: max(1, len(percents) // 3)])
    tail = statistics.mean(percents[-max(1, len(percents) // 3):])
    if tail - head >= 3.0:
        return "improving"
    if head - tail >= 3.0:
        return "slipping"
    return "steady"


def _weighted_mean(percents: list[float]) -> float:
    """Recency-biased weighted mean. Newest assignment counts ~2x oldest."""
    if not percents:
        return 0.0
    n = len(percents)
    weights = [1.0 + (i / max(1, n - 1)) for i in range(n)]  # 1.0 → 2.0
    total_w = sum(weights)
    return sum(p * w for p, w in zip(percents, weights)) / total_w


def _confidence(sample_size: int, percents: list[float]) -> float:
    """Confidence is high when (a) we have many graded assignments and
    (b) their distribution is narrow (low stdev means stable performance)."""
    if sample_size < 3:
        return 0.25
    base = min(1.0, sample_size / 12.0)  # saturates at 12 graded assignments
    spread = statistics.pstdev(percents) if len(percents) >= 2 else 0
    stability = max(0.0, 1.0 - (spread / 30.0))  # stdev 30+ → no stability credit
    return round(0.4 * base + 0.6 * stability, 2)


def _baseline(course: str, assignments: list[dict[str, Any]]) -> CoursePrediction:
    graded = [a for a in assignments if a.get("score") is not None and a.get("points_possible")]
    graded.sort(key=lambda a: a.get("graded_at") or a.get("due_at") or "")
    percents = [
        p for p in (_percent(a.get("score"), a.get("points_possible")) for a in graded)
        if p is not None
    ]
    if not percents:
        return CoursePrediction(
            course=course,
            current_percent=None,
            predicted_percent=0.0,
            confidence=0.0,
            trend="steady",
            narrative="No graded assignments yet — connect your LMS to enable predictions.",
            sample_size=0,
            method="insufficient",
        )

    current = statistics.mean(percents)
    predicted = _weighted_mean(percents)
    trend = _trend(percents)

    if trend == "improving":
        predicted = min(100.0, predicted + 1.5)
    elif trend == "slipping":
        predicted = max(0.0, predicted - 2.0)

    narrative = {
        "improving": f"You're trending up in {course} — recent work outperforms early scores.",
        "slipping":  f"{course} is drifting down — recent scores are lower than your early average.",
        "steady":    f"You're holding steady in {course} at around {predicted:.0f}%.",
    }[trend]

    return CoursePrediction(
        course=course,
        current_percent=round(current, 1),
        predicted_percent=predicted,
        confidence=_confidence(len(percents), percents),
        trend=trend,
        narrative=narrative,
        sample_size=len(percents),
        method="statistical",
        components={"recent_5": percents[-5:], "mean": round(current, 1)},
    )


_AI_SYSTEM = (
    "You are an academic forecaster. Given a student's recent graded assignments "
    "in a course, predict their semester-end percentage and produce one short "
    "encouraging-but-honest sentence about the trajectory. Return STRICT JSON: "
    '{"predicted_percent": <number 0-100>, "narrative": "<one sentence>", "confidence": <0-1>}.'
)


def _ai_refine(
    *,
    course: str,
    baseline: CoursePrediction,
    upcoming_count: int,
    chat_json: Callable[..., dict | None] | None,
) -> CoursePrediction:
    if chat_json is None or baseline.method == "insufficient":
        return baseline
    payload = {
        "course": course,
        "current_percent": baseline.current_percent,
        "trend": baseline.trend,
        "sample_size": baseline.sample_size,
        "recent_scores": baseline.components.get("recent_5", []),
        "upcoming_assignments": upcoming_count,
        "baseline_prediction": round(baseline.predicted_percent, 1),
    }
    try:
        result = chat_json(
            system=_AI_SYSTEM,
            user=json.dumps(payload),
            schema={
                "type": "object",
                "required": ["predicted_percent", "narrative"],
                "properties": {
                    "predicted_percent": {"type": "number"},
                    "narrative": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        )
        if not isinstance(result, dict):
            return baseline
        pp = float(result.get("predicted_percent", baseline.predicted_percent))
        # Clamp AI output to [baseline - 8, baseline + 8] so a hallucination
        # can't move the needle more than the statistical model thinks plausible.
        pp = max(baseline.predicted_percent - 8.0, min(baseline.predicted_percent + 8.0, pp))
        return CoursePrediction(
            course=baseline.course,
            current_percent=baseline.current_percent,
            predicted_percent=pp,
            confidence=float(result.get("confidence", baseline.confidence)),
            trend=baseline.trend,
            narrative=str(result.get("narrative") or baseline.narrative),
            sample_size=baseline.sample_size,
            method="ai",
            components=baseline.components,
        )
    except Exception:
        return baseline


def predict_for_courses(
    *,
    assignments_by_course: dict[str, list[dict[str, Any]]],
    upcoming_by_course: dict[str, int] | None = None,
    chat_json: Callable[..., dict | None] | None = None,
) -> list[CoursePrediction]:
    """Top-level API. ``assignments_by_course`` maps a course name to a list
    of dicts with keys: ``score``, ``points_possible``, ``due_at`` (ISO or
    datetime), ``graded_at`` (optional).

    ``chat_json`` is an optional callable matching ``ai_provider.chat_json``
    that lets us refine the baseline. When omitted, predictions are purely
    statistical.
    """
    upcoming_by_course = upcoming_by_course or {}
    out: list[CoursePrediction] = []
    for course, items in assignments_by_course.items():
        baseline = _baseline(course, items)
        refined = _ai_refine(
            course=course,
            baseline=baseline,
            upcoming_count=upcoming_by_course.get(course, 0),
            chat_json=chat_json,
        )
        out.append(refined)
    return sorted(out, key=lambda p: p.predicted_percent, reverse=True)
