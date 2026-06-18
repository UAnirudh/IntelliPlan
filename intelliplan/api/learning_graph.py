"""API blueprint for the Learning Intelligence Layer.

Endpoints serve the Learning Graph Dashboard data to the Command Center
frontend. All heavy lifting delegated to ``LearningGraphService``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from intelliplan.api.serialize import (
    learning_dashboard_to_dict,
    concept_snapshot_to_dict,
    prediction_to_dict,
)


@dataclass(frozen=True)
class LearningGraphDeps:
    get_service: Callable[[], Any]
    feature_enabled: Callable[[str], bool]
    current_user_id: Callable[[], int | None]


def create_learning_graph_blueprint(deps: LearningGraphDeps) -> Blueprint:
    bp = Blueprint("learning_graph", __name__)

    def _require_user() -> int | None:
        uid = deps.current_user_id()
        if uid is None:
            return None
        return uid

    @bp.route("/api/learning/dashboard", methods=["GET"])
    def learning_dashboard():
        uid = _require_user()
        if uid is None:
            return jsonify({"error": "Not authenticated"}), 401
        try:
            svc = deps.get_service()
            payload = svc.build_dashboard(uid)
            return jsonify(learning_dashboard_to_dict(payload))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @bp.route("/api/learning/concepts", methods=["GET"])
    def learning_concepts():
        uid = _require_user()
        if uid is None:
            return jsonify({"error": "Not authenticated"}), 401
        try:
            svc = deps.get_service()
            subject = request.args.get("subject")
            rows = svc._concepts.for_user(uid, subject=subject)
            from datetime import datetime
            now = datetime.utcnow()
            from intelliplan.services.learning_graph import _row_to_snapshot
            snapshots = [_row_to_snapshot(r, now) for r in rows]
            return jsonify({
                "concepts": [concept_snapshot_to_dict(s) for s in snapshots],
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @bp.route("/api/learning/predictions", methods=["GET"])
    def learning_predictions():
        uid = _require_user()
        if uid is None:
            return jsonify({"error": "Not authenticated"}), 401
        try:
            svc = deps.get_service()
            payload = svc.build_dashboard(uid)
            return jsonify({
                "predictions": [prediction_to_dict(p) for p in payload.predictions],
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @bp.route("/api/learning/heatmap", methods=["GET"])
    def learning_heatmap():
        uid = _require_user()
        if uid is None:
            return jsonify({"error": "Not authenticated"}), 401
        try:
            svc = deps.get_service()
            rows = svc._concepts.for_user(uid)
            subjects: dict[str, list[dict]] = {}
            for r in rows:
                if r.subject not in subjects:
                    subjects[r.subject] = []
                subjects[r.subject].append({
                    "topic": r.topic,
                    "concept": r.concept,
                    "mastery": round(r.mastery_score, 3),
                    "confidence": round(r.confidence_score, 3),
                    "times_seen": r.times_seen,
                })
            return jsonify({"heatmap": subjects})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @bp.route("/api/learning/sync", methods=["POST"])
    def learning_sync():
        uid = _require_user()
        if uid is None:
            return jsonify({"error": "Not authenticated"}), 401
        try:
            svc = deps.get_service()
            count = svc.sync_concept_mastery(uid)
            return jsonify({"synced": count})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return bp
