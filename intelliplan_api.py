"""
intelliplan_api.py — Public REST API for IntelliPlan

Exposes IntelliPlan functionality (assignments, tests, schedule generation,
streak data, identity) to third-party clients and to the IntelliPlan MCP
server. All endpoints live under /api/v1/ and require a Bearer token from
POST /api/v1/auth/token.

Tokens are itsdangerous-signed payloads keyed off Flask SECRET_KEY (same
mechanism as the extension API in auth_api.py), so no extra DB table is
needed.

Blueprint name: 'intelliplan_api_bp' (kept unique to avoid endpoint
collisions with App.py's web routes).
"""

import json
from datetime import datetime
from time_utils import utcnow
from functools import wraps

from flask import Blueprint, jsonify, request, g, current_app

from auth_api import make_token, verify_token, get_bearer_token

api_bp = Blueprint("intelliplan_api_bp", __name__, url_prefix="/api/v1")
API_VERSION = "v1"


# ── Resolve App.py's models/helpers via current_app ──────────────────
# We attach references on App.py to the live Flask `app` so we don't have
# to `from App import ...` (which creates a SECOND SQLAlchemy instance
# when `python App.py` loads the file as both `__main__` and `App`).
def _models():
    a = current_app
    return a.intelliplan_db, a.intelliplan_user_model, a.intelliplan_bcrypt


def _app():
    return current_app


def _resolve_user():
    token = get_bearer_token()
    if not token:
        return None
    payload = verify_token(token)
    if not payload:
        return None
    db, User, _ = _models()
    return db.session.get(User, payload.get("user_id"))


def require_token(fn):
    """Decorator: 401 unless Bearer token resolves to a valid User."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = _resolve_user()
        if not user:
            return jsonify({"error": "unauthorized", "message": "Missing or invalid Bearer token."}), 401
        g.api_user = user
        return fn(*args, **kwargs)
    return wrapper


def _err(msg, code=400):
    return jsonify({"error": msg}), code


def _impersonate_payload(user):
    """Return Flask-Login session payload that authenticates `user`.

    Used by `_call_internal()` so we can re-enter our own Flask views with
    `current_user` populated to the API caller, without touching the
    actual web session cookie.
    """
    return {
        "_user_id": str(user.id),
        "_id": "",
        "_fresh": True,
    }


def _call_internal(method, path, json_body=None):
    """Invoke a Flask view in-process as the current API user.

    Returns (status_code, json_data_or_text).
    """
    client = current_app.test_client()
    with client.session_transaction() as sess:
        for k, v in _impersonate_payload(g.api_user).items():
            sess[k] = v
    fn = getattr(client, method.lower())
    resp = fn(path, json=json_body) if json_body is not None else fn(path)
    body = resp.get_data(as_text=True)
    try:
        return resp.status_code, json.loads(body)
    except ValueError:
        return resp.status_code, body


# ── Discovery / docs ──────────────────────────────────────────────────
@api_bp.route("/", methods=["GET"])
@api_bp.route("/docs", methods=["GET"])
def api_index():
    return jsonify({
        "api": "IntelliPlan",
        "version": API_VERSION,
        "auth": {"scheme": "Bearer", "obtain_token": "POST /api/v1/auth/token"},
        "endpoints": {
            "POST /api/v1/auth/token": "Exchange email+password for a Bearer token.",
            "GET  /api/v1/me": "Current authenticated user.",
            "GET  /api/v1/assignments": "Unified list of assignments from all connected sources + manual tasks.",
            "POST /api/v1/tasks": "Create a manual task. Body: {title, due_date?, priority?, course?, estimated_time?, notes?}.",
            "POST /api/v1/assignments/dismiss": "Mark an assignment done. Body: {title}.",
            "POST /api/v1/assignments/restore": "Restore a previously dismissed assignment. Body: {title}.",
            "GET  /api/v1/tests": "All assignments marked as tests.",
            "POST /api/v1/tests": "Mark an assignment as a test. Body: {title, ...optional metadata}.",
            "DELETE /api/v1/tests": "Unmark a test. Body: {title}.",
            "POST /api/v1/schedule/generate": "Build a study plan. Body: {hours_per_day?, preferred_time?, custom_tasks?, assignments?}.",
            "GET  /api/v1/streak": "Streak count, sparks, freezes, level, weekly quests.",
            "GET  /api/v1/identity": "Student profile (grade, focus areas, goals, availability).",
            "PATCH /api/v1/identity": "Update student profile fields.",
        },
        "rate_limit": "Reasonable use; avoid >60 requests/minute per token.",
        "mcp": "An official Model Context Protocol server is available at intelliplan_mcp.py.",
    })


# ── Auth ──────────────────────────────────────────────────────────────
@api_bp.route("/auth/token", methods=["POST"])
def auth_token():
    db, User, bcrypt = _models()
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    password = (payload.get("password") or "").strip()
    if not email or not password:
        return _err("email and password required.", 400)
    user = db.session.query(User).filter_by(email=email).first()
    if not user or not user.password_hash:
        return _err("Invalid credentials.", 401)
    if not bcrypt.check_password_hash(user.password_hash, password):
        return _err("Invalid credentials.", 401)
    return jsonify({
        "token": make_token(user),
        "token_type": "Bearer",
        "user": {"id": user.id, "email": user.email, "name": user.name},
    })


@api_bp.route("/me", methods=["GET"])
@require_token
def me():
    u = g.api_user
    return jsonify({
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    })


# ── Assignments ───────────────────────────────────────────────────────
@api_bp.route("/assignments", methods=["GET"])
@require_token
def list_assignments():
    """Return the same unified payload the dashboard renders."""
    status, data = _call_internal("GET", "/live")
    if status >= 400:
        return jsonify({"error": "live_fetch_failed", "detail": data}), status
    return jsonify({"assignments": data if isinstance(data, list) else []})


@api_bp.route("/tasks", methods=["POST"])
@require_token
def create_task():
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return _err("title required.", 400)
    payload = {
        "title": title,
        "due_date": body.get("due_date"),
        "priority": body.get("priority") or "Medium",
        "course": body.get("course") or "Personal",
        "estimated_time": body.get("estimated_time") or 60,
        "notes": body.get("notes") or "",
        "sync_notion": False,
    }
    status, data = _call_internal("POST", "/tasks/manual/create", payload)
    return jsonify(data), status


@api_bp.route("/assignments/dismiss", methods=["POST"])
@require_token
def dismiss_assignment():
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return _err("title required.", 400)
    status, data = _call_internal("POST", "/dismiss", body)
    return jsonify(data), status


@api_bp.route("/assignments/restore", methods=["POST"])
@require_token
def restore_assignment():
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return _err("title required.", 400)
    status, data = _call_internal("POST", "/restore", body)
    return jsonify(data), status


# ── Tests ─────────────────────────────────────────────────────────────
@api_bp.route("/tests", methods=["GET"])
@require_token
def list_tests():
    status, data = _call_internal("GET", "/api/tests")
    return jsonify({"tests": data if isinstance(data, list) else []}), status


@api_bp.route("/tests", methods=["POST"])
@require_token
def mark_test():
    body = request.get_json(silent=True) or {}
    if not (body.get("title") or "").strip():
        return _err("title required.", 400)
    status, data = _call_internal("POST", "/test/mark", body)
    return jsonify(data), status


@api_bp.route("/tests", methods=["DELETE"])
@require_token
def unmark_test():
    body = request.get_json(silent=True) or {}
    if not (body.get("title") or "").strip():
        return _err("title required.", 400)
    status, data = _call_internal("POST", "/test/unmark", body)
    return jsonify(data), status


# ── Schedule ──────────────────────────────────────────────────────────
@api_bp.route("/schedule/generate", methods=["POST"])
@require_token
def generate_schedule():
    """Build a study plan. Pulls assignments from /live if none supplied."""
    body = request.get_json(silent=True) or {}
    assignments = body.get("assignments")
    if assignments is None:
        s, live = _call_internal("GET", "/live")
        assignments = live if isinstance(live, list) else []
    payload = {
        "assignments": assignments,
        "hours_per_day": float(body.get("hours_per_day") or 2),
        "preferred_time": (body.get("preferred_time") or "evening").lower(),
        "custom_tasks": body.get("custom_tasks") or [],
    }
    status, data = _call_internal("POST", "/generate_schedule", payload)
    return jsonify(data), status


# ── Streak / sparks ───────────────────────────────────────────────────
@api_bp.route("/streak", methods=["GET"])
@require_token
def streak_info():
    status, data = _call_internal("GET", "/study/points")
    return jsonify(data), status


# ── Identity / profile ────────────────────────────────────────────────
@api_bp.route("/identity", methods=["GET"])
@require_token
def get_identity():
    try:
        identity = current_app.intelliplan_get_identity(g.api_user.id)
        return jsonify(identity.to_dict())
    except Exception as e:
        return _err(f"Could not load identity: {e}", 500)


@api_bp.route("/identity", methods=["PATCH"])
@require_token
def patch_identity():
    db, _, _ = _models()
    body = request.get_json(silent=True) or {}
    try:
        identity = current_app.intelliplan_get_identity(g.api_user.id)
        if "grade_level" in body:
            identity.grade_level = (str(body.get("grade_level") or "").strip()[:32]) or None
        if "focus_areas" in body and isinstance(body["focus_areas"], list):
            cleaned = [str(x).strip()[:48] for x in body["focus_areas"] if str(x).strip()][:12]
            identity.focus_areas = json.dumps(cleaned)
        if "goals" in body:
            identity.goals = str(body.get("goals") or "").strip()[:1000]
        if "weekly_commitments" in body:
            identity.weekly_commitments = str(body.get("weekly_commitments") or "").strip()[:500]
        if "availability" in body and isinstance(body["availability"], dict):
            identity.availability = json.dumps(body["availability"])
        identity.updated_at = utcnow()
        db.session.commit()
        return jsonify({"status": "ok", "identity": identity.to_dict()})
    except Exception as e:
        return _err(f"Could not update identity: {e}", 500)
