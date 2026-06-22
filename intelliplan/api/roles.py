"""Teacher / parent role management + read-only dashboards.

Roles are stored on the ``User`` model via a single ``role`` column
(``student`` | ``teacher`` | ``parent``). Linkage between a teacher/parent
and their students is the ``StudentLink`` table:

    StudentLink(linker_user_id, student_user_id, relationship, accepted_at)

The "linker" is the teacher or parent. The student is the one being
viewed. Students approve the link on first view in /linked-accounts —
nothing is exposed without consent.
"""

from __future__ import annotations

import secrets
from datetime import datetime

from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import current_user, login_required

bp = Blueprint("roles_bp", __name__)


def _db():
    return current_app.intelliplan_db  # type: ignore[attr-defined]


def _link_model():
    return current_app.intelliplan_student_link_model  # type: ignore[attr-defined]


def _user_model():
    return current_app.intelliplan_user_model  # type: ignore[attr-defined]


# ── PAGES ─────────────────────────────────────────────────────────────


@bp.route("/teacher")
@login_required
def teacher_dashboard():
    if current_user.role != "teacher":
        return jsonify({"error": "forbidden", "detail": "Teacher account required."}), 403
    return render_template("teacher_dashboard.html", active_page="teacher")


@bp.route("/parent")
@login_required
def parent_dashboard():
    if current_user.role != "parent":
        return jsonify({"error": "forbidden", "detail": "Parent account required."}), 403
    return render_template("parent_dashboard.html", active_page="parent")


# ── API ───────────────────────────────────────────────────────────────


@bp.route("/api/roles/role", methods=["GET", "POST"])
@login_required
def api_role():
    if request.method == "GET":
        return jsonify({"role": current_user.role or "student"})
    body = request.get_json(silent=True) or {}
    new_role = (body.get("role") or "").strip().lower()
    if new_role not in ("student", "teacher", "parent"):
        return jsonify({"error": "invalid_role"}), 400
    current_user.role = new_role
    _db().session.commit()
    return jsonify({"role": current_user.role})


@bp.route("/api/roles/invite", methods=["POST"])
@login_required
def api_invite():
    """A teacher/parent invites a student by email. Creates a pending
    StudentLink — the student approves from /linked-accounts."""
    if current_user.role not in ("teacher", "parent"):
        return jsonify({"error": "forbidden"}), 403
    body = request.get_json(silent=True) or {}
    email = (body.get("student_email") or "").strip().lower()
    if not email:
        return jsonify({"error": "missing_email"}), 400
    User = _user_model()
    StudentLink = _link_model()
    student = User.query.filter_by(email=email).first()
    if student is None:
        return jsonify({"error": "student_not_found"}), 404
    if student.id == current_user.id:
        return jsonify({"error": "cannot_link_to_self"}), 400
    existing = StudentLink.query.filter_by(
        linker_user_id=current_user.id, student_user_id=student.id
    ).first()
    if existing:
        return jsonify({"ok": True, "status": "already_invited", "link_id": existing.id})
    link = StudentLink(
        linker_user_id=current_user.id,
        student_user_id=student.id,
        relationship=current_user.role,
        invite_token=secrets.token_urlsafe(16),
        accepted_at=None,
    )
    db = _db()
    db.session.add(link)
    db.session.commit()
    return jsonify({"ok": True, "link_id": link.id, "status": "invited"})


@bp.route("/api/roles/links", methods=["GET"])
@login_required
def api_list_links():
    StudentLink = _link_model()
    User = _user_model()
    links = StudentLink.query.filter_by(linker_user_id=current_user.id).all()
    out = []
    for link in links:
        student = User.query.get(link.student_user_id)
        if not student:
            continue
        out.append({
            "link_id": link.id,
            "student_id": student.id,
            "student_name": student.name or student.email,
            "student_email": student.email,
            "relationship": link.relationship,
            "accepted": link.accepted_at is not None,
            "accepted_at": link.accepted_at.isoformat() if link.accepted_at else None,
        })
    return jsonify({"links": out})


@bp.route("/api/roles/pending", methods=["GET"])
@login_required
def api_pending_for_student():
    StudentLink = _link_model()
    User = _user_model()
    pending = StudentLink.query.filter_by(
        student_user_id=current_user.id, accepted_at=None
    ).all()
    out = []
    for link in pending:
        linker = User.query.get(link.linker_user_id)
        if not linker:
            continue
        out.append({
            "link_id": link.id,
            "linker_name": linker.name or linker.email,
            "linker_email": linker.email,
            "relationship": link.relationship,
        })
    return jsonify({"pending": out})


@bp.route("/api/roles/links/<int:link_id>/accept", methods=["POST"])
@login_required
def api_accept_link(link_id: int):
    StudentLink = _link_model()
    link = StudentLink.query.get(link_id)
    if link is None or link.student_user_id != current_user.id:
        return jsonify({"error": "not_found"}), 404
    link.accepted_at = datetime.utcnow()
    _db().session.commit()
    return jsonify({"ok": True})


@bp.route("/api/roles/links/<int:link_id>", methods=["DELETE"])
@login_required
def api_delete_link(link_id: int):
    StudentLink = _link_model()
    link = StudentLink.query.get(link_id)
    if link is None:
        return jsonify({"error": "not_found"}), 404
    if link.linker_user_id != current_user.id and link.student_user_id != current_user.id:
        return jsonify({"error": "forbidden"}), 403
    db = _db()
    db.session.delete(link)
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/roles/student/<int:student_id>/overview", methods=["GET"])
@login_required
def api_student_overview(student_id: int):
    """Read-only snapshot of a linked student. Returns 403 unless an
    accepted StudentLink exists between the caller and the student."""
    StudentLink = _link_model()
    link = StudentLink.query.filter_by(
        linker_user_id=current_user.id, student_user_id=student_id
    ).first()
    if link is None or link.accepted_at is None:
        return jsonify({"error": "no_access"}), 403

    User = _user_model()
    student = User.query.get(student_id)
    if not student:
        return jsonify({"error": "not_found"}), 404

    overview = {
        "student": {
            "id": student.id,
            "name": student.name or student.email,
            "email": student.email,
        },
        "summary": _summarize_for_view(student.id),
    }
    return jsonify(overview)


def _summarize_for_view(student_user_id: int) -> dict:
    """Best-effort assignment + grade summary the linked viewer is allowed
    to see. Falls back to a blank shape when the helper isn't available."""
    try:
        from App import _load_user_assignments  # type: ignore
        rows = _load_user_assignments(student_user_id) or []  # type: ignore[misc]
    except Exception:
        rows = []
    total = len(rows)
    completed = sum(1 for r in rows if r.get("completed"))
    overdue = sum(1 for r in rows if r.get("overdue"))
    avg_percent = None
    percents = []
    for r in rows:
        s, p = r.get("score"), r.get("points_possible")
        if s is not None and p:
            try:
                percents.append((float(s) / float(p)) * 100.0)
            except (TypeError, ValueError, ZeroDivisionError):
                continue
    if percents:
        avg_percent = round(sum(percents) / len(percents), 1)
    return {
        "total_assignments": total,
        "completed": completed,
        "overdue": overdue,
        "avg_percent": avg_percent,
        "upcoming": [
            {
                "title": r.get("title"),
                "course": r.get("course"),
                "due_date": r.get("due_date"),
            }
            for r in rows
            if not r.get("completed")
        ][:10],
    }
