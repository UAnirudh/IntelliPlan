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
import json
from datetime import date, datetime, timedelta
from time_utils import utcnow

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from intelliplan.repositories.assignments import AssignmentRepository

bp = Blueprint("roles_bp", __name__)
ENCOURAGEMENT = {
    "steady": "I see the effort you are putting in. One steady step at a time.",
    "plan": "Want to look at the next deadline together and make a small plan?",
    "rest": "It is okay to pause. I am here when you want to talk through the next step.",
}
SHARE_SCOPES = frozenset({"work", "study", "foundations"})


def _link_scopes(link) -> set[str]:
    """Legacy approvals keep their previous access; malformed new values fail closed."""
    if link.relationship != "parent" or link.share_scopes_json is None:
        return set(SHARE_SCOPES)
    try:
        scopes = json.loads(link.share_scopes_json)
    except (TypeError, ValueError):
        return set()
    if not isinstance(scopes, list):
        return set()
    return {scope for scope in scopes if isinstance(scope, str) and scope in SHARE_SCOPES}


def _requested_scopes(payload):
    if not isinstance(payload, dict) or "scopes" not in payload:
        return set(SHARE_SCOPES)
    scopes = payload["scopes"]
    if (not isinstance(scopes, list) or not scopes or
            any(type(scope) is not str or scope not in SHARE_SCOPES for scope in scopes)):
        return None
    return set(scopes)


def _db():
    return current_app.intelliplan_db  # type: ignore[attr-defined]


def _link_model():
    return current_app.intelliplan_student_link_model  # type: ignore[attr-defined]


def _user_model():
    return current_app.intelliplan_user_model  # type: ignore[attr-defined]


def _accepted_parent_link(student_id: int):
    if current_user.role != "parent":
        return None
    return _link_model().query.filter_by(
        linker_user_id=current_user.id, student_user_id=student_id,
        relationship="parent").filter(
            _link_model().accepted_at.isnot(None)).first()


def _public_note(note):
    return {"id": note.id, "template_id": note.template_id,
            "message": ENCOURAGEMENT[note.template_id],
            "created_at": note.created_at.isoformat() + "Z",
            "acknowledged_at": note.acknowledged_at.isoformat() + "Z" if note.acknowledged_at else None,
            "withdrawn_at": note.withdrawn_at.isoformat() + "Z" if note.withdrawn_at else None}


# ── PAGES ─────────────────────────────────────────────────────────────


@bp.route("/teacher")
@login_required
def teacher_dashboard():
    if current_user.role != "teacher":
        return jsonify({"error": "forbidden", "detail": "Teacher account required."}), 403
    return render_template("teacher_dashboard.html", active_page="teacher")


@bp.route("/parent")
def parent_dashboard():
    if not current_user.is_authenticated:
        return redirect(url_for("login_account", next="/parent"))
    return render_template("parent_portal.html", noindex_page=True,
                           parent_mode=current_user.role == "parent",
                           family_home_url="/parent")


@bp.route("/linked-accounts")
@login_required
def student_sharing():
    return render_template("student_sharing.html", active_page="settings",
                           noindex_page=True)


# ── API ───────────────────────────────────────────────────────────────


@bp.route("/api/roles/role", methods=["GET", "POST"])
@login_required
def api_role():
    if request.method == "GET":
        return jsonify({"role": current_user.role or "student"})
    body = request.get_json(silent=True)
    role_value = body.get("role") if isinstance(body, dict) else None
    new_role = role_value.strip().lower() if isinstance(role_value, str) else ""
    if new_role not in ("student", "teacher", "parent"):
        return jsonify({"error": "invalid_role"}), 400
    if new_role == "parent" and current_user.birth_year and datetime.utcnow().year - current_user.birth_year < 18:
        return jsonify({"error": "adult_account_required"}), 403
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
    body = request.get_json(silent=True)
    email_value = body.get("student_email") if isinstance(body, dict) else None
    email = email_value.strip().lower() if isinstance(email_value, str) else ""
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
        linker_user_id=current_user.id, student_user_id=student.id,
        relationship=current_user.role,
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
    if current_user.role not in ("parent", "teacher"):
        return jsonify({"links": []})
    links = StudentLink.query.filter_by(linker_user_id=current_user.id,
                                        relationship=current_user.role).all()
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
            "scopes": sorted(_link_scopes(link)),
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


@bp.route("/api/roles/my-links", methods=["GET"])
@login_required
def api_my_links():
    Link = _link_model()
    User = _user_model()
    links = Link.query.filter_by(student_user_id=current_user.id).all()
    result = []
    for link in links:
        linker = User.query.get(link.linker_user_id)
        if linker:
            result.append({"link_id": link.id, "linker_name": linker.name or linker.email,
                           "linker_email": linker.email, "relationship": link.relationship,
                           "accepted": link.accepted_at is not None,
                           "scopes": sorted(_link_scopes(link)),
                           "accepted_at": link.accepted_at.isoformat() + "Z" if link.accepted_at else None})
    return jsonify({"links": result})


@bp.route("/api/roles/links/<int:link_id>/accept", methods=["POST"])
@login_required
def api_accept_link(link_id: int):
    StudentLink = _link_model()
    link = StudentLink.query.get(link_id)
    if link is None or link.student_user_id != current_user.id:
        return jsonify({"error": "not_found"}), 404
    if link.accepted_at is not None:
        return jsonify({"error": "already_accepted"}), 409
    if link.relationship == "parent":
        scopes = _requested_scopes(request.get_json(silent=True))
        if scopes is None:
            return jsonify({"error": "invalid_scopes"}), 400
        link.share_scopes_json = json.dumps(sorted(scopes))
    link.accepted_at = utcnow()
    _db().session.commit()
    return jsonify({"ok": True})


@bp.route("/api/roles/links/<int:link_id>/scopes", methods=["PATCH"])
@login_required
def api_update_link_scopes(link_id: int):
    link = _link_model().query.get(link_id)
    if (link is None or link.student_user_id != current_user.id or
            link.relationship != "parent" or link.accepted_at is None):
        return jsonify({"error": "not_found"}), 404
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or "scopes" not in payload:
        return jsonify({"error": "invalid_scopes"}), 400
    scopes = _requested_scopes(payload)
    if scopes is None:
        return jsonify({"error": "invalid_scopes"}), 400
    link.share_scopes_json = json.dumps(sorted(scopes))
    _db().session.commit()
    return jsonify({"ok": True, "scopes": sorted(scopes)})


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
    current_app.intelliplan_family_nudge_model.query.filter_by(link_id=link.id).delete()
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
        linker_user_id=current_user.id, student_user_id=student_id,
        relationship=current_user.role
    ).first()
    if current_user.role not in ("parent", "teacher") or link is None or link.accepted_at is None:
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
        "summary": _summarize_for_view(student.id, _link_scopes(link)),
    }
    return jsonify(overview)


def _summarize_for_view(student_user_id: int, scopes: set[str] | None = None) -> dict:
    """Keep each approved evidence source independent and mark unavailable data."""
    scopes = set(SHARE_SCOPES) if scopes is None else scopes
    today = date.today()
    summary = {
        "status": "ok", "as_of": datetime.utcnow().isoformat() + "Z",
        "scopes": sorted(scopes), "work_status": "not_shared",
        "study_status": "not_shared", "total_assignments": None,
        "open": None, "completed": None, "overdue": None,
        "avg_percent": None, "study_sessions_7d": None,
        "study_days_7d": None, "upcoming": None,
        "foundations": None,
        "study_source": "Sessions recorded by the student app; this does not verify time spent studying away from IntelliPlan.",
    }
    if "work" in scopes:
        try:
            db = _db()
            ManualTask = current_app.intelliplan_manual_task_model
            DismissedAssignment = current_app.intelliplan_dismissed_assignment_model
            repository = AssignmentRepository(
                ManualTask, db.session, current_app.intelliplan_assignment_fetcher)
            rows = repository.for_user(student_user_id, today)
            manual_done = db.session.query(ManualTask).filter_by(
                user_id=student_user_id, done=True).count()
            dismissed_rows = db.session.query(DismissedAssignment).filter_by(
                user_id=student_user_id).all()
            normalize_title = current_app.intelliplan_norm_title
            dismissed_titles = {normalize_title(row.title) for row in dismissed_rows}
            rows = tuple(row for row in rows if row.source == "manual" or
                         normalize_title(row.title) not in dismissed_titles)
            dismissed = len(dismissed_rows)
            upcoming = [r for r in rows if r.due_date is None or r.due_date >= today]
            summary.update({
                "work_status": "ok",
                "total_assignments": len(rows) + manual_done + dismissed,
                "open": len(rows), "completed": manual_done + dismissed,
                "overdue": sum(1 for r in rows if r.due_date and r.due_date < today),
                "upcoming": [{
                    "title": r.title, "course": r.course,
                    "due_date": r.due_date.isoformat() if r.due_date else None,
                } for r in upcoming][:10],
            })
        except Exception as exc:
            current_app.logger.exception("family work summary unavailable: %s", exc)
            summary["work_status"] = "unavailable"
    if "study" in scopes:
        try:
            StudyPoints = current_app.intelliplan_study_points_model
            points = StudyPoints.query.filter_by(user_id=student_user_id).first()
            history = json.loads(points.session_history or "[]") if points else []
            if not isinstance(history, list):
                history = []
            recent_dates = []
            for session in history:
                if not isinstance(session, dict):
                    continue
                try:
                    day = date.fromisoformat(str(session.get("date", ""))[:10])
                except ValueError:
                    continue
                if today - timedelta(days=6) <= day <= today:
                    recent_dates.append(day)
            summary.update({
                "study_status": "ok",
                "study_sessions_7d": len(recent_dates),
                "study_days_7d": len(set(recent_dates)),
            })
        except Exception as exc:
            current_app.logger.exception("family study summary unavailable: %s", exc)
            summary["study_status"] = "unavailable"
    if "foundations" in scopes:
        summary["foundations"] = _foundations_snapshot(student_user_id)
    return summary


def _foundations_snapshot(student_user_id: int) -> list[dict] | None:
    """Share aggregate practice and a next skill, never answers or tutor text."""
    try:
        from primer import store as primer_store

        result = []
        for learner in primer_store.list_learners(student_user_id)[:5]:
            overview = primer_store.parent_overview(learner['id'], grade=learner['grade'])
            days = overview['days']
            result.append({
                "learner_name": learner['nickname'],
                "grade": learner['grade'],
                "answered_days_7d": sum(day['answers'] > 0 for day in days),
                "answers_7d": sum(day['answers'] for day in days),
                "last_answered_at": overview['last_answered_at'],
                "focus": overview['focus'],
            })
        return result
    except Exception as exc:
        current_app.logger.exception("family foundations snapshot unavailable: %s", exc)
        return None


@bp.route("/api/roles/student/<int:student_id>/encouragement", methods=["GET", "POST"])
@login_required
def api_parent_encouragement(student_id: int):
    link = _accepted_parent_link(student_id)
    if not link:
        return jsonify({"error": "no_access"}), 403
    Note = current_app.intelliplan_family_nudge_model
    if request.method == "GET":
        latest = Note.query.filter_by(link_id=link.id).order_by(Note.id.desc()).first()
        now = utcnow()
        can_send = not latest or (latest.created_at <= now - timedelta(hours=24) and
                                  (latest.acknowledged_at is not None or latest.withdrawn_at is not None))
        return jsonify({"latest": _public_note(latest) if latest else None,
                        "templates": ENCOURAGEMENT, "can_send": can_send,
                        "next_note_at": (latest.created_at + timedelta(hours=24)).isoformat() + "Z"
                        if latest and not can_send else None})
    payload = request.get_json(silent=True)
    template_id = payload.get("template_id") if isinstance(payload, dict) else None
    if not isinstance(template_id, str) or template_id not in ENCOURAGEMENT:
        return jsonify({"error": "invalid_template"}), 400
    db = _db()
    if db.session.query(_link_model()).filter_by(id=link.id).with_for_update().first() is None:
        return jsonify({"error": "no_access"}), 403
    latest = Note.query.filter_by(link_id=link.id).order_by(Note.id.desc()).first()
    now = utcnow()
    if latest and (latest.created_at > now - timedelta(hours=24) or
                   (latest.acknowledged_at is None and latest.withdrawn_at is None)):
        return jsonify({"error": "wait_before_another_note"}), 429
    note = Note(link_id=link.id, template_id=template_id, created_at=now)
    db.session.add(note)
    db.session.commit()
    return jsonify({"note": _public_note(note)}), 201


@bp.route("/api/roles/student/<int:student_id>/encouragement/<int:note_id>", methods=["DELETE"])
@login_required
def api_withdraw_encouragement(student_id: int, note_id: int):
    link = _accepted_parent_link(student_id)
    if not link:
        return jsonify({"error": "no_access"}), 403
    Note = current_app.intelliplan_family_nudge_model
    note = Note.query.filter_by(id=note_id, link_id=link.id).first()
    if not note or note.acknowledged_at or note.withdrawn_at:
        return jsonify({"error": "not_found"}), 404
    note.withdrawn_at = utcnow()
    _db().session.commit()
    return jsonify({"ok": True})


@bp.route("/api/roles/my-encouragement", methods=["GET"])
@login_required
def api_my_encouragement():
    Link = _link_model()
    Note = current_app.intelliplan_family_nudge_model
    notes = _db().session.query(Note).join(Link, Note.link_id == Link.id).filter(
        Link.student_user_id == current_user.id, Link.relationship == "parent",
        Link.accepted_at.isnot(None), Note.acknowledged_at.is_(None),
        Note.withdrawn_at.is_(None), Note.created_at >= utcnow() - timedelta(days=7),
    ).order_by(Note.created_at.desc()).limit(3).all()
    return jsonify({"notes": [_public_note(note) for note in notes]})


@bp.route("/api/roles/my-encouragement/<int:note_id>/acknowledge", methods=["POST"])
@login_required
def api_acknowledge_encouragement(note_id: int):
    Link = _link_model()
    Note = current_app.intelliplan_family_nudge_model
    note = _db().session.query(Note).join(Link, Note.link_id == Link.id).filter(
        Note.id == note_id, Link.student_user_id == current_user.id,
        Link.relationship == "parent", Link.accepted_at.isnot(None),
        Note.withdrawn_at.is_(None)).first()
    if not note:
        return jsonify({"error": "not_found"}), 404
    if note.acknowledged_at is None:
        note.acknowledged_at = utcnow()
        _db().session.commit()
    return jsonify({"ok": True})
