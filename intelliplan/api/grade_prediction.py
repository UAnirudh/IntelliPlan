"""Grade prediction API.

Endpoint: ``POST /api/grade-predictions``

Pulls the user's graded assignments out of the existing live LMS sources
(Canvas, StudentVUE, Schoology) and runs them through
:mod:`intelliplan.services.grade_prediction`.

Request body (optional):
  {"override": [{"course": "Algebra II", "assignments": [...]}]}

When ``override`` is present we skip the LMS pull and use the client's
data — useful for letting students experiment with "what-if" scenarios
in the grade modeler.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

from intelliplan.services.grade_prediction import predict_for_courses

logger = logging.getLogger(__name__)

bp = Blueprint("grade_prediction_bp", __name__)


def _chat_json_adapter() -> Callable[..., dict | None] | None:
    """Adapt the legacy ``ai_provider.chat_json(messages=...)`` signature to
    the keyword-only ``system=, user=, schema=`` shape the service expects."""
    try:
        from ai_provider import ai_available, chat_json as legacy_chat_json
    except Exception:
        return None
    if not ai_available():
        return None

    def _call(*, system: str, user: str, schema: dict | None = None) -> dict | None:
        try:
            return legacy_chat_json(
                [{"role": "system", "content": system},
                 {"role": "user", "content": user}],
                tier="standard",
                temperature=0.25,
            )
        except Exception as exc:
            logger.warning("grade_prediction AI call failed: %s", exc)
            return None

    return _call


def _gather_assignments_from_db(user_id: int) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    """Best-effort pull of graded + upcoming assignments. Returns two dicts
    keyed by course name."""
    db = current_app.intelliplan_db  # type: ignore[attr-defined]
    graded_by_course: dict[str, list[dict[str, Any]]] = {}
    upcoming_by_course: dict[str, int] = {}

    # The app already has helpers that materialize live assignments; we
    # call them lazily so the import doesn't run at module load time
    # (avoids the circular-import dance with App.py).
    try:
        from App import _load_user_assignments  # type: ignore
    except Exception:
        _load_user_assignments = None  # type: ignore[assignment]

    rows: list[dict[str, Any]] = []
    if _load_user_assignments:
        try:
            rows = _load_user_assignments(user_id) or []  # type: ignore[misc]
        except Exception as exc:
            logger.warning("_load_user_assignments failed: %s", exc)

    for r in rows:
        course = (r.get("course") or r.get("class") or "Unknown course").strip() or "Unknown course"
        score = r.get("score")
        possible = r.get("points_possible") or r.get("possible")
        if score is not None and possible:
            graded_by_course.setdefault(course, []).append({
                "score": score,
                "points_possible": possible,
                "due_at": r.get("due_date") or r.get("due_at"),
                "graded_at": r.get("graded_at"),
                "title": r.get("title"),
            })
        elif not r.get("completed") and not r.get("dismissed"):
            upcoming_by_course[course] = upcoming_by_course.get(course, 0) + 1

    return graded_by_course, upcoming_by_course


def _gather_from_payload(payload: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    graded: dict[str, list[dict[str, Any]]] = {}
    upcoming: dict[str, int] = {}
    for entry in payload.get("override", []) or []:
        course = str(entry.get("course") or "Unknown course")
        graded[course] = list(entry.get("assignments") or [])
        upcoming[course] = int(entry.get("upcoming_count") or 0)
    return graded, upcoming


@bp.route("/api/grade-predictions", methods=["POST", "GET"])
@login_required
def api_grade_predictions():
    body: dict[str, Any] = {}
    if request.method == "POST":
        try:
            body = request.get_json(silent=True) or {}
        except Exception:
            body = {}

    if body.get("override"):
        graded, upcoming = _gather_from_payload(body)
    else:
        graded, upcoming = _gather_assignments_from_db(current_user.id)

    if not graded:
        return jsonify({
            "predictions": [],
            "message": "No graded assignments found yet. Connect your LMS to enable predictions.",
        })

    predictions = predict_for_courses(
        assignments_by_course=graded,
        upcoming_by_course=upcoming,
        chat_json=_chat_json_adapter(),
    )
    return jsonify({"predictions": [p.to_dict() for p in predictions]})
