"""Collaborative tasks inside Study Groups.

Extends the existing /groups feature: any group member can add a shared
task, mark it done, or claim it. The model lives next to the existing
StudyGroup model in App.py and is exposed here via a thin REST API.

Endpoints:
  GET    /api/groups/<group_id>/tasks
  POST   /api/groups/<group_id>/tasks         body: {title, due_date?, notes?}
  POST   /api/groups/tasks/<task_id>/claim
  POST   /api/groups/tasks/<task_id>/toggle   body: {done: true|false}
  DELETE /api/groups/tasks/<task_id>
"""

from __future__ import annotations

from datetime import datetime

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

bp = Blueprint("group_tasks_bp", __name__)


def _db():
    return current_app.intelliplan_db  # type: ignore[attr-defined]


def _models():
    a = current_app
    return (
        a.intelliplan_study_group_model,  # type: ignore[attr-defined]
        a.intelliplan_study_group_member_model,  # type: ignore[attr-defined]
        a.intelliplan_study_group_task_model,  # type: ignore[attr-defined]
    )


def _is_member(group_id: int, user_id: int) -> bool:
    _, Member, _ = _models()
    return Member.query.filter_by(group_id=group_id, user_id=user_id).first() is not None


def _task_to_dict(task) -> dict:
    return {
        "id": task.id,
        "group_id": task.group_id,
        "title": task.title,
        "notes": task.notes,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "done": bool(task.done),
        "claimed_by_user_id": task.claimed_by_user_id,
        "created_by_user_id": task.created_by_user_id,
        "created_at": task.created_at.isoformat() if task.created_at else None,
    }


@bp.route("/api/groups/<int:group_id>/tasks", methods=["GET"])
@login_required
def api_list_tasks(group_id: int):
    if not _is_member(group_id, current_user.id):
        return jsonify({"error": "not_a_member"}), 403
    _, _, Task = _models()
    tasks = Task.query.filter_by(group_id=group_id).order_by(Task.created_at.desc()).all()
    return jsonify({"tasks": [_task_to_dict(t) for t in tasks]})


@bp.route("/api/groups/<int:group_id>/tasks", methods=["POST"])
@login_required
def api_create_task(group_id: int):
    if not _is_member(group_id, current_user.id):
        return jsonify({"error": "not_a_member"}), 403
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "missing_title"}), 400
    due_date = None
    raw_due = body.get("due_date")
    if raw_due:
        try:
            due_date = datetime.fromisoformat(str(raw_due).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            due_date = None

    _, _, Task = _models()
    task = Task(
        group_id=group_id,
        title=title[:200],
        notes=(body.get("notes") or "")[:2000] or None,
        due_date=due_date,
        created_by_user_id=current_user.id,
    )
    db = _db()
    db.session.add(task)
    db.session.commit()
    return jsonify({"task": _task_to_dict(task)}), 201


@bp.route("/api/groups/tasks/<int:task_id>/claim", methods=["POST"])
@login_required
def api_claim_task(task_id: int):
    _, _, Task = _models()
    task = Task.query.get(task_id)
    if task is None:
        return jsonify({"error": "not_found"}), 404
    if not _is_member(task.group_id, current_user.id):
        return jsonify({"error": "not_a_member"}), 403
    # Toggle: claim if unclaimed, release if already mine.
    if task.claimed_by_user_id == current_user.id:
        task.claimed_by_user_id = None
    elif task.claimed_by_user_id is None:
        task.claimed_by_user_id = current_user.id
    else:
        return jsonify({"error": "already_claimed_by_other"}), 409
    _db().session.commit()
    return jsonify({"task": _task_to_dict(task)})


@bp.route("/api/groups/tasks/<int:task_id>/toggle", methods=["POST"])
@login_required
def api_toggle_task(task_id: int):
    _, _, Task = _models()
    task = Task.query.get(task_id)
    if task is None:
        return jsonify({"error": "not_found"}), 404
    if not _is_member(task.group_id, current_user.id):
        return jsonify({"error": "not_a_member"}), 403
    body = request.get_json(silent=True) or {}
    task.done = bool(body.get("done", not task.done))
    task.completed_at = datetime.utcnow() if task.done else None
    _db().session.commit()
    return jsonify({"task": _task_to_dict(task)})


@bp.route("/api/groups/tasks/<int:task_id>", methods=["DELETE"])
@login_required
def api_delete_task(task_id: int):
    _, _, Task = _models()
    task = Task.query.get(task_id)
    if task is None:
        return jsonify({"error": "not_found"}), 404
    if not _is_member(task.group_id, current_user.id):
        return jsonify({"error": "not_a_member"}), 403
    if task.created_by_user_id != current_user.id:
        return jsonify({"error": "only_creator_can_delete"}), 403
    db = _db()
    db.session.delete(task)
    db.session.commit()
    return jsonify({"ok": True})
