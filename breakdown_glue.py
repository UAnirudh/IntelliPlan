"""HTTP surface for "Break it down" and "Just 5 minutes".

Routes (session cookie or bearer app token; guests use their guest session,
like the rest of the planner):

* ``GET  /api/breakdown?title=…`` — the saved checklist for an assignment.
* ``POST /api/breakdown`` — (re)build it: ``{title, course, due_date,
  description, id, course_id, estimated_time, granularity: 1|2|3,
  schedule: true}``. Steps come from the directions, a grounded model call
  when allowed, or the work's shape (:mod:`intelliplan.intelligence.breakdown`).
  Minutes are calibrated by the student's own finished steps. With
  ``schedule`` (default) the steps replace the assignment's future blocks in
  the saved plan and the reply says where each landed.
* ``POST /api/breakdown/steps`` — add a step of the student's own.
* ``PATCH /api/breakdown/steps/<id>`` — tick/untick, rename, re-estimate.
  Ticking records how long it really took (``actual_minutes``, or the Active
  timer's sittings on that step) and ticks the plan block once all of its
  steps are done.
* ``POST /api/breakdown/start`` — "Just 5 minutes": opens an Active session
  on the first unfinished step and returns where to go (``/active``).

Every App import is lazy, matching the other ``*_glue`` modules.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from typing import Any, Iterable

from flask import Blueprint, abort, jsonify, request
from flask_login import current_user

from intelliplan.intelligence import breakdown as bd
from intelliplan.models.assignment_step import assignment_key
from time_utils import utcnow

logger = logging.getLogger(__name__)

breakdown_bp = Blueprint("breakdown", __name__)

MAX_DESCRIPTION = 12000


# ── identity & rows ───────────────────────────────────────────────────


def _identity() -> tuple[int | None, str | None]:
    try:
        if current_user.is_authenticated:
            return int(current_user.id), None
    except Exception:
        pass
    from App import get_guest_session_id

    try:
        gid = get_guest_session_id()
    except Exception:
        gid = None
    if not gid:
        abort(401, description="No session. Enable cookies or sign in.")
    return None, gid


def _owned(query: Any, model: Any, uid: int | None, gid: str | None) -> Any:
    if uid:
        return query.filter(model.user_id == uid)
    return query.filter(model.user_id.is_(None), model.guest_session_id == gid)


def _steps_for(uid: int | None, gid: str | None, key: str) -> list[Any]:
    from App import AssignmentStep

    q = _owned(AssignmentStep.query, AssignmentStep, uid, gid).filter(
        AssignmentStep.assignment_key == key, AssignmentStep.archived.is_(False),
    )
    return q.order_by(AssignmentStep.position.asc(), AssignmentStep.id.asc()).all()


def steps_by_title(titles: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    """``{assignment_key: [step dicts]}`` for the current owner — what the
    planner rows carry so a full replan schedules the student's own steps."""
    from App import AssignmentStep

    keys = sorted({assignment_key(t) for t in titles if str(t or "").strip()})[:300]
    if not keys:
        return {}
    try:
        uid, gid = _identity()
    except Exception:
        return {}
    rows = (
        _owned(AssignmentStep.query, AssignmentStep, uid, gid)
        .filter(AssignmentStep.assignment_key.in_(keys), AssignmentStep.archived.is_(False))
        .order_by(AssignmentStep.position.asc(), AssignmentStep.id.asc())
        .all()
    )
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(row.assignment_key, []).append(row.to_dict())
    return out


def _ratio(uid: int | None, gid: str | None) -> tuple[float, str, int]:
    """This student's actual/estimate ratio, from steps first, then tasks."""
    from App import AssignmentStep, TaskFeedback

    try:
        step_pairs = [
            (r.estimate_minutes, r.actual_minutes)
            for r in _owned(AssignmentStep.query, AssignmentStep, uid, gid)
            .filter(AssignmentStep.done.is_(True), AssignmentStep.actual_minutes.isnot(None))
            .order_by(AssignmentStep.done_at.desc()).limit(60).all()
        ]
        fq = TaskFeedback.query.filter(TaskFeedback.actual_time.isnot(None))
        fq = fq.filter(TaskFeedback.user_id == uid) if uid else fq.filter(TaskFeedback.guest_session_id == gid)
        feedback_pairs = [(r.estimated_time, r.actual_time)
                          for r in fq.order_by(TaskFeedback.id.desc()).limit(60).all()]
    except Exception as exc:
        logger.warning("ratio history load failed: %s", exc)
        return 1.0, "none", 0
    return bd.personal_ratio(step_pairs, feedback_pairs)


def _payload(uid: int | None, gid: str | None, title: str, rows: list[Any], **extra: Any) -> dict[str, Any]:
    ratio, source, samples = _ratio(uid, gid)
    steps = [r.to_dict() for r in rows]
    nxt = bd.first_open_step(steps)
    return {
        "status": "ok",
        "title": title,
        "steps": steps,
        "next_step": nxt,
        "done_count": sum(1 for s in steps if s["done"]),
        "remaining_minutes": sum(s["minutes"] for s in steps if not s["done"]),
        "calibration": {"ratio": ratio, "source": source, "samples": samples},
        "granularity_labels": bd.GRANULARITY_LABELS,
        **extra,
    }


# ── assignment context ────────────────────────────────────────────────


def _saved_description(title: str) -> str:
    from App import get_custom_description

    try:
        raw = get_custom_description(title) or ""
    except Exception:
        return ""
    if raw.lstrip().startswith("{"):
        try:
            return str((json.loads(raw) or {}).get("notes") or "")
        except (TypeError, ValueError):
            return raw
    return raw


def _canvas_context(body: dict[str, Any]) -> dict[str, Any] | None:
    """Directions and readable attachments straight from Canvas, when the
    student is connected and the client named the assignment. Best effort:
    any failure means the steps are built from what the client sent."""
    assignment_id, course_id = body.get("id"), body.get("course_id")
    if not (str(assignment_id or "").isdigit() and str(course_id or "").isdigit()):
        return None
    try:
        from App import get_active_account
        from assignment_materials import load_assignment

        account = get_active_account()
        if not account or account.get("login_type") != "canvas" or not account.get("canvas_token"):
            return None
        return load_assignment(account.get("canvas_url"), account["canvas_token"], course_id, assignment_id)
    except Exception as exc:
        logger.info("canvas context unavailable for breakdown: %s", type(exc).__name__)
        return None


def _ai_allowed() -> bool:
    """A model only sees school work when the student has opted in, exactly
    as the tutor's study map requires — and only when a provider exists."""
    try:
        from ai_provider import ai_available
        from App import _ai_personalization_enabled

        return bool(current_user.is_authenticated and ai_available() and _ai_personalization_enabled())
    except Exception:
        return False


def _ai_breakdown(title: str, course: str, context: dict[str, Any], total: int, granularity: int):
    import ai_firewall
    from ai_provider import chat_json

    messages = bd.ai_messages(title=title, course=course, context=context,
                              total_minutes=total, granularity=granularity)
    decision = ai_firewall.guard(current_user, prompts=[messages[-1]["content"]],
                                 want_output_tokens=900, feature="breakdown")
    raw = chat_json(messages, tier="fast", temperature=0.2,
                    max_tokens=min(900, decision.max_output_tokens))
    try:
        ai_firewall.record_tokens(decision, sum(len(m["content"]) for m in messages), len(json.dumps(raw)))
    except Exception:
        pass
    return bd.parse_ai_steps(raw, context, total_minutes=total, granularity=granularity)


def _kind_for(title: str, body_kind: Any) -> str:
    from intelliplan.intelligence.quick_add import infer_kind

    declared = str(body_kind or "").strip().lower()
    return declared or infer_kind(title)


def _total_minutes(body: dict[str, Any], title: str, kind: str, description: str) -> int:
    from App import _sized_estimate
    from intelliplan.intelligence.quick_add import planner_kind

    try:
        minutes, _subtasks, _signals = _sized_estimate(
            {"title": title, "estimated_time": body.get("estimated_time"),
             "points_possible": body.get("points_possible"), "description": description},
            planner_kind(kind), description,
        )
        return max(15, min(1200, int(minutes or 60)))
    except Exception:
        try:
            return max(15, min(1200, int(body.get("estimated_time") or 60)))
        except (TypeError, ValueError):
            return 60


# ── plan surgery ──────────────────────────────────────────────────────


def _due(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _block_is_for(block: dict[str, Any], title: str) -> bool:
    key = assignment_key(title)
    return assignment_key(block.get("parent_title") or block.get("assignment")) == key


def _schedule_steps(uid: int | None, gid: str | None, title: str, course: str,
                    due_iso: str, rows: list[Any], tz_hint: str | None) -> dict[str, Any]:
    """Replace this assignment's future, unfinished blocks with its steps."""
    from plan_insert_glue import active_plan_row, insert_into_plan, student_now

    now = student_now(uid, tz_hint)
    open_steps = [r.to_dict() for r in rows if not r.done]
    if not open_steps:
        return {"status": "ok", "placed": [], "label": "Every step is done — nothing left to schedule."}
    row = active_plan_row(uid, gid)
    progress: dict[str, Any] = {}
    if row is not None and row.progress_json:
        try:
            progress = json.loads(row.progress_json) or {}
        except (TypeError, ValueError):
            progress = {}
    today = now.date().isoformat()

    def prune(data: dict[str, Any]) -> None:
        for day in data.get("schedule") or []:
            if str(day.get("date") or "")[:10] < today:
                continue
            kept = []
            for block in day.get("blocks") or []:
                entry = progress.get(str(block.get("id")))
                done = entry is True or (isinstance(entry, dict) and entry.get("done"))
                if _block_is_for(block, title) and not block.get("is_break") and not done:
                    continue
                kept.append(block)
            day["blocks"] = kept

    base = f"{assignment_key(title)}::steps:"
    blocks = []
    groups = bd.group_into_sittings(open_steps)
    for index, group in enumerate(groups, start=1):
        ids = [s["id"] for s in group]
        blocks.append({
            "assignment": f"{title} — {bd.sitting_label(group)}",
            "parent_title": title,
            "stage_title": bd.sitting_label(group),
            "task_id": base + "-".join(str(i) for i in ids),
            "step_ids": ids,
            "course": course,
            "duration_minutes": max(5, sum(int(s["minutes"]) for s in group)),
            "due_date": due_iso,
            "kind": "homework",
            "difficulty": "Medium",
            "part_index": index,
            "part_total": len(groups),
            "notes": "; ".join(s["text"] for s in group),
            "reasons": ["Your own steps from “Break it down”, in order."],
            "checklist": [s["text"] for s in group],
        })
    return insert_into_plan(uid, gid, blocks, now=now, deadline=_due(due_iso), prune=prune)


def _sync_plan_progress(uid: int | None, gid: str | None, title: str) -> None:
    """Tick (or untick) step blocks whose steps are all done.

    Ticking steps is partial progress on the plan, so it has to reach the
    same ``progress_json`` the Interactive View writes — otherwise the plan
    keeps offering a block whose every step is already checked off.
    """
    from App import AssignmentStep, db
    from plan_insert_glue import active_plan_row

    row = active_plan_row(uid, gid)
    if row is None:
        return
    try:
        data = json.loads(row.schedule_data or "{}")
        progress = json.loads(row.progress_json) if row.progress_json else {}
        if not isinstance(progress, dict):
            progress = {}
    except (TypeError, ValueError):
        return
    done_ids = {
        r.id for r in _owned(AssignmentStep.query, AssignmentStep, uid, gid)
        .filter(AssignmentStep.assignment_key == assignment_key(title), AssignmentStep.done.is_(True)).all()
    }
    changed = False
    for day in data.get("schedule") or []:
        for block in day.get("blocks") or []:
            m = re.search(r"::steps:([\d-]+)$", str(block.get("task_id") or ""))
            if not m or not _block_is_for(block, title):
                continue
            ids = {int(x) for x in m.group(1).split("-") if x}
            key = str(block.get("id"))
            entry = progress.get(key)
            all_done = bool(ids) and ids <= done_ids
            if all_done and not (isinstance(entry, dict) and entry.get("done")) and entry is not True:
                progress[key] = {"done": True, "via": "steps"}
                changed = True
            elif not all_done and isinstance(entry, dict) and entry.get("via") == "steps":
                progress.pop(key, None)
                changed = True
    if changed:
        try:
            row.progress_json = json.dumps(progress)
            db.session.commit()
        except Exception:
            db.session.rollback()


# ── routes ────────────────────────────────────────────────────────────


def _body() -> dict[str, Any]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _title(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:512]


@breakdown_bp.route("/api/breakdown", methods=["GET"])
def get_breakdown():
    title = _title(request.args.get("title"))
    if not title:
        return jsonify({"status": "error", "message": "title required"}), 400
    uid, gid = _identity()
    return jsonify(_payload(uid, gid, title, _steps_for(uid, gid, assignment_key(title))))


@breakdown_bp.route("/api/breakdown", methods=["POST"])
def build_breakdown():
    from App import AssignmentStep, db

    body = _body()
    title = _title(body.get("title"))
    if not title:
        return jsonify({"status": "error", "message": "title required"}), 400
    uid, gid = _identity()
    course = str(body.get("course") or "")[:256]
    due_iso = str(body.get("due_date") or "")[:32]
    granularity = bd._granularity(body.get("granularity"))

    description = str(body.get("description") or "")[:MAX_DESCRIPTION].strip() or _saved_description(title)
    context: dict[str, Any] = {"title": title, "description": description, "materials": []}
    canvas = _canvas_context(body) if _ai_allowed() or not description else None
    if canvas:
        context = {**canvas, "description": canvas.get("description") or description}
        description = context["description"]
    kind = _kind_for(title, body.get("kind"))
    total = _total_minutes(body, title, kind, description)

    result = None
    if _ai_allowed():
        try:
            result = _ai_breakdown(title, course, context, total, granularity)
        except Exception as exc:
            logger.info("AI breakdown unavailable, using directions/template: %s", type(exc).__name__)
            result = None
    if result is None:
        result = bd.deterministic_breakdown(title=title, total_minutes=total, kind=kind,
                                            description=description, granularity=granularity)
    ratio, ratio_source, samples = _ratio(uid, gid)
    result = bd.calibrate(result, ratio)

    key = assignment_key(title)
    existing = _owned(AssignmentStep.query, AssignmentStep, uid, gid).filter(
        AssignmentStep.assignment_key == key, AssignmentStep.archived.is_(False)).all()
    try:
        for row in existing:
            if row.done:
                row.archived = True
            else:
                db.session.delete(row)
        for position, step in enumerate(result.steps):
            db.session.add(AssignmentStep(
                user_id=uid, guest_session_id=None if uid else gid,
                assignment_key=key, assignment_title=title, course=course, due_date=due_iso,
                position=position, text=step.text[:512], estimate_minutes=step.minutes,
                evidence=step.evidence[:2000], source=step.source[:16],
            ))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.warning("breakdown save failed: %s", exc)
        return jsonify({"status": "error", "message": "Could not save the steps."}), 500

    rows = _steps_for(uid, gid, key)
    placement: dict[str, Any] = {"status": "skipped", "placed": [], "label": ""}
    if body.get("schedule", True) is not False:
        try:
            placement = _schedule_steps(uid, gid, title, course, due_iso, rows, body.get("timezone"))
        except Exception as exc:
            logger.warning("step scheduling failed: %s", exc)
            placement = {"status": "error", "placed": [], "label": ""}
    return jsonify(_payload(
        uid, gid, title, rows,
        method=result.method, note=result.note, granularity=granularity,
        placement=placement,
    ))


@breakdown_bp.route("/api/breakdown/steps", methods=["POST"])
def add_step():
    from App import AssignmentStep, db

    body = _body()
    title = _title(body.get("title"))
    text = re.sub(r"\s+", " ", str(body.get("text") or "")).strip()[:512]
    if not title or not text:
        return jsonify({"status": "error", "message": "title and text required"}), 400
    uid, gid = _identity()
    try:
        minutes = max(5, min(240, int(body.get("minutes") or 15)))
    except (TypeError, ValueError):
        minutes = 15
    rows = _steps_for(uid, gid, assignment_key(title))
    db.session.add(AssignmentStep(
        user_id=uid, guest_session_id=None if uid else gid,
        assignment_key=assignment_key(title), assignment_title=title,
        course=str(body.get("course") or (rows[0].course if rows else ""))[:256],
        due_date=str(body.get("due_date") or (rows[0].due_date if rows else ""))[:32],
        position=(max((r.position for r in rows), default=-1) + 1),
        text=text, estimate_minutes=minutes, source="student",
    ))
    db.session.commit()
    return jsonify(_payload(uid, gid, title, _steps_for(uid, gid, assignment_key(title)))), 201


def _timer_minutes(uid: int | None, gid: str | None, step_id: int) -> int:
    """Minutes the Active timer ran on this step, across every sitting."""
    from App import ActiveSession

    try:
        q = _owned(ActiveSession.query, ActiveSession, uid, gid).filter(
            ActiveSession.task_id == f"step:{step_id}")
        seconds = sum(int(r.active_seconds or 0) for r in q.all())
    except Exception:
        return 0
    return int(round(seconds / 60.0))


def mark_step_done(uid: int | None, gid: str | None, step: Any, *, done: bool,
                   actual_minutes: Any = None) -> None:
    """Tick a step and record what it really took. Commits."""
    from App import db

    step.done = bool(done)
    if step.done:
        step.done_at = utcnow()
        minutes = None
        try:
            if actual_minutes not in (None, ""):
                minutes = int(actual_minutes)
        except (TypeError, ValueError):
            minutes = None
        if minutes is None or minutes <= 0:
            minutes = _timer_minutes(uid, gid, step.id) or None
        # No timer and no answer means no measurement. Recording the
        # estimate as the actual would teach the model it is always right.
        step.actual_minutes = max(1, min(600, minutes)) if minutes else None
    else:
        step.done_at = None
        step.actual_minutes = None
    db.session.commit()
    _sync_plan_progress(uid, gid, step.assignment_title)


@breakdown_bp.route("/api/breakdown/steps/<int:step_id>", methods=["PATCH", "POST"])
def update_step(step_id: int):
    from App import AssignmentStep, db

    uid, gid = _identity()
    step = _owned(AssignmentStep.query, AssignmentStep, uid, gid).filter(
        AssignmentStep.id == step_id).first()
    if step is None:
        return jsonify({"status": "error", "message": "Step not found."}), 404
    body = _body()
    if "text" in body:
        text = re.sub(r"\s+", " ", str(body.get("text") or "")).strip()[:512]
        if text:
            step.text = text
    if "minutes" in body:
        try:
            step.estimate_minutes = max(5, min(240, int(body["minutes"])))
        except (TypeError, ValueError):
            pass
    if body.get("delete"):
        db.session.delete(step)
        db.session.commit()
        return jsonify(_payload(uid, gid, step.assignment_title,
                                _steps_for(uid, gid, step.assignment_key)))
    if "done" in body:
        mark_step_done(uid, gid, step, done=bool(body.get("done")),
                       actual_minutes=body.get("actual_minutes"))
    else:
        db.session.commit()
    return jsonify(_payload(uid, gid, step.assignment_title,
                            _steps_for(uid, gid, step.assignment_key)))


@breakdown_bp.route("/api/breakdown/start", methods=["POST"])
def start_five_minutes():
    """"Just 5 minutes" on the first unfinished step.

    Reuses the Active study timer rather than inventing a second one: the
    session is an ordinary Active session with ``task_id = step:<id>`` and a
    five-minute plan, so it heartbeats, survives a reload, feeds the
    estimation model like any other sitting — and when the student marks it
    done, the step is ticked with the timer's minutes. The Active page sees
    the step session and offers to keep going when the five minutes are up.
    """
    from App import ActiveFocusSample, ActiveSession, AssignmentStep, db, feature_enabled
    from intelliplan.api.active import FLAG_KEY
    from intelliplan.repositories.active_sessions import ActiveSessionRepository

    try:
        if not feature_enabled(FLAG_KEY):
            return jsonify({"status": "error", "message": "Active study is switched off right now."}), 404
    except Exception:
        pass
    uid, gid = _identity()
    body = _body()
    step = None
    if body.get("step_id") is not None:
        try:
            step = _owned(AssignmentStep.query, AssignmentStep, uid, gid).filter(
                AssignmentStep.id == int(body["step_id"])).first()
        except (TypeError, ValueError):
            step = None
    else:
        title = _title(body.get("title"))
        if title:
            step = bd.first_open_step(_steps_for(uid, gid, assignment_key(title)))
    if step is None:
        return jsonify({"status": "error",
                        "message": "Break it down first — there's no unfinished step to start."}), 404
    minutes = bd.FIVE_MINUTE_START
    try:
        if body.get("minutes"):
            minutes = max(1, min(60, int(body["minutes"])))
    except (TypeError, ValueError):
        pass
    repo = ActiveSessionRepository(ActiveSession, ActiveFocusSample, db.session)
    session_row = repo.start(
        user_id=uid, guest_id=gid,
        title=f"{step.assignment_title} — {step.text}"[:512],
        planned_minutes=minutes,
        task_id=step.task_ref,
        course=step.course or "",
        kind="homework",
        difficulty="medium",
        due_date=_due(step.due_date),
    )
    return jsonify({
        "status": "ok",
        "session": session_row.to_dict(),
        "step": step.to_dict(),
        "redirect": "/active?start=5",
    }), 201


def on_active_session_finished(row: Any) -> None:
    """Close the loop from the timer: a step session marked done ticks the step."""
    task_id = str(getattr(row, "task_id", "") or "")
    m = re.fullmatch(r"step:(\d+)", task_id)
    if not m or not getattr(row, "completed_work", False):
        return
    from App import AssignmentStep

    uid = getattr(row, "user_id", None)
    gid = None if uid else getattr(row, "guest_session_id", None)
    step = _owned(AssignmentStep.query, AssignmentStep, uid, gid).filter(
        AssignmentStep.id == int(m.group(1))).first()
    if step is None or step.done:
        return
    mark_step_done(uid, gid, step, done=True)
