"""Quick-add: one line of text in, a scheduled task out.

Every capture surface goes through :func:`capture` so they cannot drift:

* the web app's command palette (Cmd/Ctrl+K, or ``q``) —
  ``POST /api/quick-add``, session cookie, same-origin CSRF guard;
* the Android app / share sheet and any other bearer-token client —
  the same route, ``Authorization: Bearer <app token>``
  (``load_user_from_request`` in ``App.py``);
* the Chrome extension's popup box and ``ip`` omnibox keyword —
  ``POST /extension/task/add`` with ``X-Extension-Token`` (the extension
  cannot ride the session cookie cross-origin);
* the desktop tray, which opens the web palette in the app window.

The parse is deterministic (:mod:`intelliplan.intelligence.quick_add`): it
answers instantly, never invents a date, and needs no AI key. After saving,
the task is slotted into the student's existing plan
(:mod:`plan_insert_glue`) and the reply says exactly where it went.

Client contract
---------------
``POST /api/quick-add`` with JSON ``{"text": "bio lab due fri 2h",
"timezone": "America/Chicago"}`` (timezone optional; the stored one wins).
Optional ``"courses": [...]`` adds course names the client knows about to
the ones the server has. Reply::

    {"status": "ok", "task": {...}, "parsed": {...},
     "placement": {"status": "ok", "label": "Scheduled Thu 4:00–6:00 PM",
                   "placed": [...]},
     "message": "Added “Bio lab” · due Fri Oct 2 · 2h — Scheduled Thu 4:00–6:00 PM"}

``POST /api/quick-add/preview`` takes the same body and returns only
``parsed`` and ``minutes``, saving nothing — for live chips while typing.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from flask import Blueprint, jsonify, request
from flask_login import current_user

from intelliplan.intelligence.quick_add import QuickAdd, parse_quick_add
from plan_insert_glue import insert_into_plan, student_now

logger = logging.getLogger(__name__)

quick_add_bp = Blueprint("quick_add", __name__)

MAX_TEXT = 500
#: Sittings for a captured task are capped here; a two-hour lab becomes two
#: hour blocks rather than one the student will not sit through.
SITTING_CAP_MINUTES = 60


# ── identity ──────────────────────────────────────────────────────────


def _identity() -> tuple[int | None, str | None]:
    try:
        if current_user.is_authenticated:
            return int(current_user.id), None
    except Exception:
        pass
    from App import get_guest_session_id

    try:
        return None, get_guest_session_id()
    except Exception:
        return None, None


# ── what the student's courses are called ─────────────────────────────


def known_courses(user_id: int | None, guest_id: str | None,
                  extra: Iterable[Any] = ()) -> list[str]:
    """Course names this student uses, from local data only.

    No LMS round trip: quick-add has to answer in well under a second, and
    every course a student has ever had a task or grade in is already here.
    """
    from App import ImportedGrade, ManualCourse, ManualTask, _lms_cache_get

    names: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and len(text) <= 256 and text.lower() not in {"personal", "unknown", ""}:
            names.append(text)

    for value in list(extra or [])[:60]:
        add(value)
    try:
        own = {"user_id": user_id} if user_id else {"guest_session_id": guest_id}
        if not (user_id or guest_id):
            return list(dict.fromkeys(names))
        for row in ManualCourse.query.filter_by(**own).limit(60).all():
            add(row.name)
        for (course,) in ManualTask.query.with_entities(ManualTask.course).filter_by(**own).distinct().limit(60):
            add(course)
        for (course,) in ImportedGrade.query.with_entities(ImportedGrade.course).filter_by(**own).distinct().limit(60):
            add(course)
        if user_id:
            for task in (_lms_cache_get(user_id) or [])[:300]:
                if isinstance(task, dict):
                    add(task.get("course"))
    except Exception as exc:
        logger.warning("course list for quick-add failed: %s", exc)
    return list(dict.fromkeys(names))


# ── capture ───────────────────────────────────────────────────────────


def estimated_minutes(parsed: QuickAdd) -> tuple[int, str]:
    """Typed duration, else the sizing model's prior for this kind of work."""
    if parsed.minutes:
        return parsed.minutes, "typed"
    try:
        from intelliplan.intelligence.sizing import size_assignment

        sized = size_assignment({"title": parsed.title, "kind": parsed.planner_kind})
        return max(10, min(600, int(sized.minutes))), "estimated"
    except Exception:
        return 60, "estimated"


def _sittings(total: int) -> list[int]:
    try:
        from intelliplan.intelligence.planner import split_into_sessions

        parts = split_into_sessions(int(total), SITTING_CAP_MINUTES)
        if parts and sum(parts) == int(total):
            return parts
    except Exception:
        pass
    return [int(total)]


def _summary(parsed: QuickAdd, minutes: int) -> str:
    bits = [f"Added “{parsed.title}”"]
    if parsed.due_date:
        bits.append(f"due {parsed.due_date:%a %b} {parsed.due_date.day}")
    bits.append(f"{minutes // 60}h{minutes % 60:02d}" if minutes >= 60 and minutes % 60
                else (f"{minutes // 60}h" if minutes >= 60 else f"{minutes}m"))
    if parsed.course:
        bits.append(parsed.course)
    return " · ".join(bits)


def capture(
    user_id: int | None,
    guest_id: str | None,
    text: str,
    *,
    tz_hint: str | None = None,
    courses: Iterable[Any] = (),
    overrides: dict[str, Any] | None = None,
    schedule: bool = True,
) -> dict[str, Any]:
    """Parse, save as a ManualTask, place in the plan. Returns the reply body.

    ``overrides`` are explicit fields a client sent alongside the text
    (the extension's older ``{title, due_date, course}`` shape); they win
    over what the parser read.
    """
    from App import ManualTask, db, invalidate_lms_cache_for_user

    now = student_now(user_id, tz_hint)
    parsed = parse_quick_add(text, now=now, courses=known_courses(user_id, guest_id, courses))
    overrides = overrides or {}
    minutes, minutes_source = estimated_minutes(parsed)
    try:
        if overrides.get("estimated_time"):
            minutes = max(5, min(600, int(overrides["estimated_time"])))
            minutes_source = "typed"
    except (TypeError, ValueError):
        pass

    due_iso = (str(overrides.get("due_date") or "")[:32]
               or (parsed.due_date.isoformat() if parsed.due_date else ""))
    notes = str(overrides.get("notes") or "")
    if parsed.due_time and not notes:
        notes = f"Due at {parsed.due_time:%I:%M %p}".replace(" 0", " ")
    task = ManualTask(
        user_id=user_id,
        guest_session_id=None if user_id else guest_id,
        title=parsed.title[:512],
        due_date=due_iso,
        priority=(str(overrides.get("priority") or "") or parsed.priority or "Medium")[:16],
        course=(str(overrides.get("course") or "") or parsed.course or "Personal")[:256],
        estimated_time=minutes,
        notes=notes,
        import_source="quickadd",
    )
    try:
        db.session.add(task)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    if user_id:
        try:
            invalidate_lms_cache_for_user(user_id)
        except Exception:
            pass

    placement: dict[str, Any] = {"status": "skipped", "placed": [], "label": ""}
    if schedule:
        from datetime import date as _date

        try:
            deadline = _date.fromisoformat(due_iso[:10]) if due_iso else None
        except ValueError:
            deadline = None
        parts = _sittings(minutes)
        blocks = [
            {
                "assignment": parsed.title if len(parts) == 1
                else f"{parsed.title} (part {i} of {len(parts)})",
                "parent_title": parsed.title,
                # The same id a regenerated plan gives this task, so the next
                # full replan recognises the block as this task's.
                "task_id": str(task.id),
                "course": task.course if task.course != "Personal" else "",
                "duration_minutes": part,
                "due_date": due_iso,
                "kind": parsed.planner_kind,
                "priority": task.priority,
                "difficulty": "Medium",
                "part_index": i,
                "part_total": len(parts),
                "notes": "Added with quick add.",
                "reasons": ["Added with quick add — placed in your next free time."],
                "quick_add": True,
            }
            for i, part in enumerate(parts, start=1)
        ]
        try:
            placement = insert_into_plan(user_id, guest_id, blocks, now=now, deadline=deadline)
        except Exception as exc:
            logger.warning("quick-add placement failed: %s", exc)
            placement = {"status": "error", "placed": [], "label": ""}

    message = _summary(parsed, minutes)
    if placement.get("label"):
        message += f" — {placement['label']}"
    elif placement.get("status") == "no_plan":
        message += " — it'll be scheduled when you build your plan."
    return {
        "status": "ok",
        "task": {
            "id": task.id, "title": task.title, "due_date": task.due_date,
            "course": task.course, "priority": task.priority,
            "estimated_time": task.estimated_time, "notes": task.notes,
        },
        "parsed": parsed.to_dict(),
        "minutes": minutes,
        "minutes_source": minutes_source,
        "placement": placement,
        "message": message,
    }


# ── routes ────────────────────────────────────────────────────────────


def _body() -> dict[str, Any]:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _text(body: dict[str, Any]) -> str:
    return str(body.get("text") or "").strip()[:MAX_TEXT]


@quick_add_bp.route("/api/quick-add/preview", methods=["POST"])
def quick_add_preview():
    body = _body()
    text = _text(body)
    if not text:
        return jsonify({"status": "error", "message": "Type something to add."}), 400
    uid, gid = _identity()
    now = student_now(uid, body.get("timezone"))
    parsed = parse_quick_add(text, now=now, courses=known_courses(uid, gid, body.get("courses") or ()))
    minutes, source = estimated_minutes(parsed)
    return jsonify({"status": "ok", "parsed": parsed.to_dict(),
                    "minutes": minutes, "minutes_source": source})


@quick_add_bp.route("/api/quick-add", methods=["POST"])
def quick_add():
    body = _body()
    text = _text(body)
    if not text:
        return jsonify({"status": "error", "message": "Type something to add."}), 400
    uid, gid = _identity()
    if not (uid or gid):
        return jsonify({"status": "error", "message": "Sign in or enable cookies to add tasks."}), 401
    try:
        result = capture(uid, gid, text, tz_hint=body.get("timezone"),
                         courses=body.get("courses") or (),
                         schedule=body.get("schedule", True) is not False)
    except Exception as exc:
        logger.warning("quick-add failed: %s", exc)
        return jsonify({"status": "error", "message": "Could not save that task."}), 500
    return jsonify(result), 201
