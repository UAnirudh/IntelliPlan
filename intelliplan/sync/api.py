"""Endpoints the offline queue uses to resolve its own uncertainty.

The queue's hard case is not "the request failed" — that one just retries.
It is "the request may or may not have been delivered": the radio dropped
after the bytes left the phone and before the response came back. Replaying
is safe because of the ledger, but the client still wants to know whether
to show the change as applied or as pending, and it would rather ask once
for fifty ops than replay fifty requests.

``POST /api/sync/ops/check`` answers exactly that, and nothing else.
"""

from __future__ import annotations

from typing import Any, Callable

from flask import Blueprint, jsonify, request

#: A single check call covers a whole queue flush; beyond this the client
#: should be pruning its queue, not asking bigger questions.
MAX_OPS_PER_CHECK = 200


def build_blueprint(SyncOp: type, current_user_id: Callable[[], int | None]) -> Any:
    bp = Blueprint("ip_sync", __name__)

    def _owner() -> int:
        try:
            return int(current_user_id() or 0)
        except (TypeError, ValueError):
            return 0

    @bp.route("/api/sync/ops/check", methods=["POST"])
    def check_ops():
        data = request.get_json(silent=True) or {}
        raw = data.get("op_ids")
        if not isinstance(raw, list):
            return jsonify({"status": "error",
                            "message": "op_ids must be a list"}), 400

        op_ids = [str(x)[:64] for x in raw if x][:MAX_OPS_PER_CHECK]
        if not op_ids:
            return jsonify({"status": "ok", "applied": {}})

        rows = SyncOp.query.filter(
            SyncOp.user_id == _owner(),
            SyncOp.op_id.in_(op_ids),
        ).all()
        return jsonify({
            "status": "ok",
            "applied": {row.op_id: row.to_dict() for row in rows},
        })

    return bp
