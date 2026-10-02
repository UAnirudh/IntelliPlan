"""Class timetable and the Today timeline.

Endpoints:

  GET    /api/timetable                       classes, rotation, bell, today's label
  POST   /api/timetable/classes               add one class
  POST   /api/timetable/classes/bulk          save several (confirmed photo drafts)
  PATCH  /api/timetable/classes/<id>          edit one
  DELETE /api/timetable/classes/<id>          remove one
  PUT    /api/timetable/settings              rotation, anchor ("today is B"),
                                              no-school days, bell schedule
  POST   /api/timetable/import                {source: auto|studentvue|schoology}
  POST   /api/timetable/photo                 image → draft classes (not saved)
  GET    /api/today/timeline?date=YYYY-MM-DD  the day, merged and to scale
  POST   /api/today/timeline/move             {block_id, date, start, preview}

Signed-in only: a timetable is a standing fact about a student, and a
guest session is not somewhere to keep one.

The move endpoint follows the same contract as ``/api/schedule/adjust``:
``preview: true`` prices the move and saves nothing; the commit recomputes
the price against the plan as it is now (a preview is a claim the browser
made) and refuses a move into a class or another block.

Feature flag ``timetable`` is a kill switch (default on). Off, every route
404s and the scheduler no longer sees class time.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import date, datetime

from flask import Blueprint, jsonify, request
from flask_login import current_user

from intelliplan.domain.timetable import (
    MAX_CYCLE_LENGTH,
    ROTATION_KINDS,
    WEEKDAY_ABBR,
    Rotation,
    anchor_for_today,
    fmt_clock,
    is_school_day,
    label_for_index,
    parse_clock,
    parse_int_list,
    parse_skip_day,
    parse_weekdays,
    rotation_day,
    rotation_label,
)
from intelliplan.services import timeline as timeline_service

logger = logging.getLogger(__name__)

bp = Blueprint("timetable", __name__)

FLAG_KEY = "timetable"
MAX_CLASSES = 30
MAX_SKIP_DAYS = 80
MAX_CLASS_MINUTES = 6 * 60
MAX_IMAGE_BYTES = 10 * 1024 * 1024
_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def _app():
    import App  # lazy: App imports this module's blueprint at boot

    return App


def _fail(message, code=400, **extra):
    return jsonify({"status": "error", "message": message, **extra}), code


def _guard():
    """``(user_id, None)`` or ``(None, error_response)``."""
    App = _app()
    if not App.feature_enabled(FLAG_KEY):
        return None, _fail("Not found.", 404)
    if not current_user.is_authenticated:
        return None, _fail("Sign in to keep a timetable.", 401)
    return current_user.id, None


def _settings_row(uid, create=False):
    App = _app()
    row = App.TimetableSettings.query.filter_by(user_id=uid).first()
    if row is None and create:
        row = App.TimetableSettings(user_id=uid)
        App.db.session.add(row)
    return row


def _rotation(uid):
    row = _settings_row(uid)
    return (row.rotation() if row else Rotation()), (row.bell() if row else {})


def _class_rows(uid):
    App = _app()
    return (
        App.ClassMeeting.query.filter_by(user_id=uid)
        .order_by(App.ClassMeeting.start_time.asc(), App.ClassMeeting.period.asc(), App.ClassMeeting.id.asc())
        .all()
    )


def _parse_date(raw):
    text = str(raw or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _state(uid, today=None):
    today = today or date.today()
    rotation, bell = _rotation(uid)
    meetings = [r.to_meeting() for r in _class_rows(uid)]
    n = rotation_day(today, rotation)
    row = _settings_row(uid)
    return {
        "status": "ok",
        "classes": [m.with_bell(bell).to_dict() | {"inherits_bell": not m.timed and m.with_bell(bell).timed}
                    for m in meetings],
        "settings": {
            **rotation.to_dict(),
            "bell": bell,
            "last_import_source": (row.last_import_source if row else "") or "",
            "last_import_at": row.last_import_at.isoformat() if row and row.last_import_at else None,
            "day_labels": [label_for_index(rotation, i) for i in range(1, rotation.length + 1)]
            if rotation.rotates else [],
        },
        "today": {
            "date": today.isoformat(),
            "rotation_day": n,
            "rotation_label": rotation_label(today, rotation),
            "needs_anchor": rotation.rotates and rotation.anchor_date is None,
        },
    }


def _after_change(uid):
    """Class time feeds the plan; anything cached from before is stale."""
    App = _app()
    try:
        App.invalidate_schedule_cache(user_id=uid)
    except Exception:
        pass
    try:
        from intelliplan.api.command_center import invalidate_today

        invalidate_today(uid)
    except Exception:
        pass


# ── Validation ───────────────────────────────────────────────────────


def _clean_class(data, *, partial=False, max_rotation=MAX_CYCLE_LENGTH):
    """``(fields, error)``. ``fields`` holds only what was sent when partial."""
    if not isinstance(data, dict):
        return None, "Send a JSON object."
    out = {}
    if not partial or "course" in data:
        course = str(data.get("course") or data.get("name") or "").strip()[:256]
        if not course:
            return None, "Enter the class name."
        out["course"] = course
    for field, limit in (("period", 32), ("room", 64), ("teacher", 128), ("color", 16)):
        if not partial or field in data:
            out[field] = str(data.get(field) or "").strip()[:limit]
    if not partial or "start" in data or "end" in data:
        start_raw, end_raw = data.get("start"), data.get("end")
        start = parse_clock(start_raw) if start_raw not in (None, "") else None
        end = parse_clock(end_raw) if end_raw not in (None, "") else None
        if (start_raw not in (None, "") and start is None) or (end_raw not in (None, "") and end is None):
            return None, "Times look like 08:15 or 1:30 PM."
        if (start is None) != (end is None):
            return None, "Give both a start and an end time, or neither."
        if start is not None:
            if end <= start:
                return None, "The class has to end after it starts."
            if end - start > MAX_CLASS_MINUTES:
                return None, "A single class can't be longer than six hours."
        out["start_time"] = fmt_clock(start) if start is not None else ""
        out["end_time"] = fmt_clock(end) if end is not None else ""
    if not partial or "weekdays" in data:
        days = parse_weekdays(data.get("weekdays"))
        out["weekdays"] = ",".join(WEEKDAY_ABBR[d] for d in days)
    if not partial or "rotation_days" in data:
        out["rotation_days"] = ",".join(
            str(n) for n in parse_int_list(data.get("rotation_days"), hi=max_rotation)
        )
    return out, None


def _owned_class(uid, class_id):
    App = _app()
    return App.ClassMeeting.query.filter_by(id=class_id, user_id=uid).first()


# ── Timetable CRUD ───────────────────────────────────────────────────


@bp.route("/api/timetable", methods=["GET"])
def timetable_get():
    uid, err = _guard()
    if err:
        return err
    return jsonify(_state(uid, _parse_date(request.args.get("date"))))


@bp.route("/api/timetable/classes", methods=["POST"])
def timetable_add_class():
    uid, err = _guard()
    if err:
        return err
    App = _app()
    if len(_class_rows(uid)) >= MAX_CLASSES:
        return _fail(f"A timetable holds up to {MAX_CLASSES} classes.")
    fields, problem = _clean_class(request.get_json(silent=True))
    if problem:
        return _fail(problem)
    App.db.session.add(App.ClassMeeting(user_id=uid, source="manual", **fields))
    try:
        App.db.session.commit()
    except Exception as exc:
        App.db.session.rollback()
        logger.warning("timetable add failed: %s", exc)
        return _fail("Could not save that class. Please try again.", 500)
    _after_change(uid)
    return jsonify(_state(uid))


@bp.route("/api/timetable/classes/bulk", methods=["POST"])
def timetable_add_bulk():
    uid, err = _guard()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    rows = body.get("classes") if isinstance(body, dict) else None
    if not isinstance(rows, list) or not rows:
        return _fail("Send {\"classes\": [...]}.")
    source = str(body.get("source") or "manual")
    if source not in ("manual", "photo"):
        source = "manual"
    cleaned = []
    for row in rows[:MAX_CLASSES]:
        fields, problem = _clean_class(row)
        if problem:
            return _fail(f"{(row or {}).get('course') or 'A class'}: {problem}" if isinstance(row, dict) else problem)
        cleaned.append(fields)
    saved = _upsert(uid, cleaned, source)
    if saved is None:
        return _fail("Could not save those classes. Please try again.", 500)
    _after_change(uid)
    return jsonify({**_state(uid), "saved": saved})


@bp.route("/api/timetable/classes/<int:class_id>", methods=["PATCH", "DELETE"])
def timetable_modify_class(class_id):
    uid, err = _guard()
    if err:
        return err
    App = _app()
    row = _owned_class(uid, class_id)
    if row is None:
        return _fail("Class not found.", 404)
    if request.method == "DELETE":
        App.db.session.delete(row)
    else:
        fields, problem = _clean_class(request.get_json(silent=True), partial=True)
        if problem:
            return _fail(problem)
        for key, value in fields.items():
            setattr(row, key, value)
    try:
        App.db.session.commit()
    except Exception as exc:
        App.db.session.rollback()
        logger.warning("timetable modify failed: %s", exc)
        return _fail("Could not save that change.", 500)
    _after_change(uid)
    return jsonify(_state(uid))


@bp.route("/api/timetable/settings", methods=["PUT", "POST"])
def timetable_settings():
    uid, err = _guard()
    if err:
        return err
    App = _app()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _fail("Send a JSON object.")
    row = _settings_row(uid, create=True)

    if "kind" in data:
        kind = str(data.get("kind") or "none")
        if kind not in ROTATION_KINDS:
            return _fail("Rotation must be none, ab, cycle or week.")
        if kind != (row.rotation_kind or "none"):
            # A different rotation means the old anchor names a day that no
            # longer exists.
            row.anchor_date, row.anchor_day = None, 1
        row.rotation_kind = kind
    if "length" in data:
        try:
            length = int(data.get("length"))
        except (TypeError, ValueError):
            return _fail("Cycle length must be a number.")
        if not 2 <= length <= MAX_CYCLE_LENGTH:
            return _fail(f"Cycles run from 2 to {MAX_CYCLE_LENGTH} days.")
        row.cycle_length = length
    if "school_weekdays" in data:
        days = parse_weekdays(data.get("school_weekdays"))
        if not days:
            return _fail("Pick at least one school day.")
        row.school_weekdays = ",".join(WEEKDAY_ABBR[d] for d in days)
    if "skip_days" in data:
        raw = data.get("skip_days")
        if not isinstance(raw, list):
            return _fail("skip_days must be a list.")
        skips = []
        for item in raw[:MAX_SKIP_DAYS]:
            skip = parse_skip_day(item)
            if skip is None:
                return _fail("Each no-school day needs a date (YYYY-MM-DD), and ranges must be under 4 months.")
            skips.append({"start": skip.start.isoformat(), "end": skip.end.isoformat(), "label": skip.label})
        row.skip_days_json = json.dumps(sorted(skips, key=lambda s: s["start"]))
    if "bell" in data:
        raw = data.get("bell")
        if not isinstance(raw, dict):
            return _fail("bell must map a period to [start, end].")
        bell = {}
        for period, span in list(raw.items())[:20]:
            if not isinstance(span, (list, tuple)) or len(span) < 2:
                return _fail(f"Period {period}: give a start and an end time.")
            start, end = parse_clock(span[0]), parse_clock(span[1])
            if start is None or end is None or end <= start:
                return _fail(f"Period {period}: the times don't make sense.")
            bell[str(period).strip()[:32]] = [fmt_clock(start), fmt_clock(end)]
        row.bell_json = json.dumps(bell)

    rotation = row.rotation()
    if "today_is" in data:
        if not rotation.rotates:
            return _fail("Choose a rotation first.")
        today = _parse_date(data.get("date")) or date.today()
        n = parse_int_list([data.get("today_is")], hi=rotation.length)
        if not n:
            return _fail("That isn't a day in this rotation.")
        if rotation.kind != "week" and not is_school_day(today, rotation):
            return _fail("There's no school that day — pick a school day to set the rotation from.")
        anchored = anchor_for_today(today, n[0], rotation)
        row.anchor_date, row.anchor_day = anchored.anchor_date, anchored.anchor_day
    elif "anchor_date" in data:
        row.anchor_date = _parse_date(data.get("anchor_date"))
        if "anchor_day" in data:
            n = parse_int_list([data.get("anchor_day")], hi=rotation.length)
            row.anchor_day = n[0] if n else 1
    try:
        App.db.session.commit()
    except Exception as exc:
        App.db.session.rollback()
        logger.warning("timetable settings failed: %s", exc)
        return _fail("Could not save your timetable settings.", 500)
    _after_change(uid)
    return jsonify(_state(uid, _parse_date(data.get("date"))))


# ── Import ───────────────────────────────────────────────────────────


def _upsert(uid, rows, source):
    """Insert or update classes from one source. Returns the count, or None.

    A re-import replaces what that source said last time — a class dropped
    at semester is gone from the timetable too — and never touches classes
    the student added by hand or brought in from another source.
    """
    App = _app()
    Model = App.ClassMeeting
    existing = Model.query.filter_by(user_id=uid).all()

    def key(course, period):
        return (str(course or "").strip().lower(), str(period or "").strip().lower())

    by_external = {(r.source, r.external_id): r for r in existing if r.external_id}
    by_key = {key(r.course, r.period): r for r in existing}
    kept_ids = set()
    count = 0
    for fields in rows:
        external_id = str(fields.pop("external_id", "") or "")[:64]
        row = (by_external.get((source, external_id)) if external_id else None) or by_key.get(
            key(fields.get("course"), fields.get("period"))
        )
        if row is not None and row.source not in (source, "manual"):
            row = None  # same name from another source: keep both, the student can tidy
        if row is None:
            if len(existing) + count >= MAX_CLASSES:
                break
            row = Model(user_id=uid, source=source, external_id=external_id, **fields)
            App.db.session.add(row)
            count += 1
        else:
            fill_only = row.source == "manual" and source != "manual"
            for name, value in fields.items():
                # An import fills gaps in a hand-made row; it never
                # overwrites something the student typed.
                if fill_only and getattr(row, name):
                    continue
                setattr(row, name, value)
            if row.source == source and external_id:
                row.external_id = external_id
        kept_ids.add(id(row))
    if source in ("studentvue", "schoology"):
        for r in existing:
            if r.source == source and id(r) not in kept_ids:
                App.db.session.delete(r)
    try:
        App.db.session.commit()
    except Exception as exc:
        App.db.session.rollback()
        logger.warning("timetable upsert failed: %s", exc)
        return None
    return len(kept_ids)


def _rows_for_upsert(classes, max_rotation):
    out = []
    for item in classes:
        fields, problem = _clean_class(item, max_rotation=max_rotation)
        if problem:
            # A class with impossible times is still a class: keep the name,
            # drop the times, and let the editor ask for them.
            fields, problem = _clean_class({**item, "start": "", "end": ""}, max_rotation=max_rotation)
            if problem:
                continue
        fields["external_id"] = item.get("external_id") or ""
        out.append(fields)
    return out


def _apply_import_settings(uid, result, source):
    row = _settings_row(uid, create=True)
    hint = result.get("rotation_hint")
    if hint and (row.rotation_kind or "none") == "none" and hint.get("kind") in ROTATION_KINDS:
        row.rotation_kind = hint["kind"]
        row.cycle_length = int(hint.get("length") or 2)
        anchor = _parse_date(hint.get("date"))
        if anchor and hint.get("today_is"):
            row.anchor_date, row.anchor_day = anchor, int(hint["today_is"])
    bell = row.bell()
    for period, span in (result.get("bell") or {}).items():
        bell.setdefault(str(period), list(span))
    row.bell_json = json.dumps(bell)
    row.last_import_source = source
    row.last_import_at = datetime.utcnow()


def _linked_account(kind):
    App = _app()
    acct = None
    try:
        acct = App._linked_account_by_type(kind)
    except Exception:
        acct = None
    if not acct:
        try:
            active = App.get_active_account()
        except Exception:
            active = None
        if active and active.get("login_type") == kind:
            acct = active
    return acct


def fetch_timetable(kind, acct):
    """Run one importer. Split out so tests can stub the network edge."""
    from intelliplan.integrations import timetable_import

    if kind == "studentvue":
        return timetable_import.fetch_studentvue_timetable(
            acct.get("sv_district_url"), acct.get("sv_username"), acct.get("sv_password"),
        )
    return timetable_import.fetch_schoology_timetable(
        acct.get("schoology_key"), acct.get("schoology_secret"),
    )


@bp.route("/api/timetable/import", methods=["POST"])
def timetable_import_route():
    uid, err = _guard()
    if err:
        return err
    App = _app()
    body = request.get_json(silent=True) or {}
    wanted = str(body.get("source") or "auto").lower()
    if wanted not in ("auto", "studentvue", "schoology"):
        return _fail("source must be auto, studentvue or schoology.")
    kinds = ["studentvue", "schoology"] if wanted == "auto" else [wanted]
    for kind in kinds:
        acct = _linked_account(kind)
        if not acct:
            continue
        try:
            result = fetch_timetable(kind, acct)
        except Exception as exc:
            logger.warning("timetable import from %s failed: %s", kind, exc)
            return _fail(
                "Your school's system didn't send a class schedule just now. "
                "Try again later, or add your classes by hand.",
                502, source=kind,
            )
        classes = result.get("classes") or []
        if not classes:
            return jsonify({**_state(uid), "imported": 0, "source": kind,
                            "message": "Your school didn't share a class schedule. Add your classes by hand below."})
        hint = result.get("rotation_hint") or {}
        max_rotation = int(hint.get("length") or MAX_CYCLE_LENGTH)
        saved = _upsert(uid, _rows_for_upsert(classes, max_rotation), kind)
        if saved is None:
            return _fail("Could not save your classes. Please try again.", 500)
        _apply_import_settings(uid, result, kind)
        try:
            App.db.session.commit()
        except Exception as exc:
            App.db.session.rollback()
            logger.warning("timetable import settings failed: %s", exc)
        _after_change(uid)
        untimed = sum(1 for c in classes if not c.get("start"))
        needs_days = sum(1 for c in classes if c.get("needs_days"))
        return jsonify({
            **_state(uid), "imported": saved, "source": kind,
            "untimed": untimed, "needs_days": needs_days,
            "rotation_hint": result.get("rotation_hint"),
        })
    return _fail("Connect StudentVUE or Schoology to import your schedule, or add classes by hand.",
                 409, needs_connection=True)


@bp.route("/api/timetable/photo", methods=["POST"])
def timetable_photo():
    """Read a photo of a printed schedule into *draft* classes.

    Nothing is saved: a vision model can misread a 3 as an 8, and a
    timetable that is silently wrong is worse than none. The student
    checks the drafts and saves them through ``/classes/bulk``.
    """
    uid, err = _guard()
    if err:
        return err
    App = _app()
    upload = request.files.get("image")
    if upload is None:
        return _fail("Choose a photo of your schedule.")
    if upload.content_type not in _ALLOWED_IMAGE_TYPES:
        return _fail("Use a JPEG, PNG, WebP or GIF image.")
    raw = upload.read()
    if not raw:
        return _fail("That photo is empty.")
    if len(raw) > MAX_IMAGE_BYTES:
        return _fail("That photo is too large. The limit is 10 MB.", 413)
    try:
        available = bool(App.ai_available())
    except Exception:
        available = False
    if not available:
        return _fail("Photo import isn't available right now. Add your classes by hand.",
                     503, fallback="manual")
    from intelliplan.integrations.timetable_import import PHOTO_PROMPT, parse_photo_timetable

    try:
        text = App.ai_vision(
            system_prompt="You read school timetables into structured data.",
            user_text=PHOTO_PROMPT,
            image_b64=base64.b64encode(raw).decode("ascii"),
            image_mime=upload.content_type,
            temperature=0.0,
            max_tokens=2000,
        )
    except Exception as exc:
        logger.warning("timetable photo read failed: %s", exc)
        return _fail("We couldn't read that photo just now. Add your classes by hand, or try again.",
                     502, fallback="manual")
    parsed = parse_photo_timetable(text or "")
    if not parsed["classes"]:
        return _fail("We couldn't find any classes in that photo. Try a sharper, straight-on shot.",
                     422, fallback="manual")
    return jsonify({"status": "ok", "draft": True, **parsed})


# ── Today timeline ───────────────────────────────────────────────────


def _active_plan(uid):
    App = _app()
    row = (
        App.SavedSchedule.query.filter_by(user_id=uid, is_active=True)
        .order_by(App.SavedSchedule.created_at.desc())
        .first()
    )
    if row is None:
        return None, None, {}
    try:
        data = json.loads(row.schedule_data) if row.schedule_data else None
    except (TypeError, ValueError):
        data = None
    try:
        progress = json.loads(row.progress_json) if row.progress_json else {}
    except (TypeError, ValueError):
        progress = {}
    return row, data if isinstance(data, dict) else None, progress if isinstance(progress, dict) else {}


def _calendar_busy(day):
    App = _app()
    try:
        return list((App._calendar_busy_by_date(1, start=day) or {}).get(day, []))
    except Exception as exc:
        logger.info("timeline calendar busy failed: %s", exc)
        return []


def _client_now(day):
    """The student's wall clock, when the page sends it (``now=HH:MM``).

    The server's clock is UTC; "the next class" has to be measured from
    the student's own time of day.
    """
    raw = request.args.get("now")
    if raw is None and request.method == "POST":
        body = request.get_json(silent=True)
        raw = body.get("now") if isinstance(body, dict) else None
    minute = parse_clock(raw)
    if minute is None or minute >= 24 * 60:
        return None
    return datetime.combine(day, datetime.min.time()).replace(hour=minute // 60 % 24, minute=minute % 60)


def _timeline_payload(uid, day, now=None):
    _row, data, progress = _active_plan(uid)
    rotation, bell = _rotation(uid)
    meetings = [r.to_meeting() for r in _class_rows(uid)]
    payload = timeline_service.build_timeline(
        day, schedule_data=data, progress=progress, meetings=meetings,
        rotation=rotation, bell=bell, busy=_calendar_busy(day), now=now,
    )
    payload["has_plan"] = bool(data and data.get("schedule"))
    payload["has_timetable"] = bool(meetings)
    return payload


@bp.route("/api/today/timeline", methods=["GET"])
def today_timeline():
    uid, err = _guard()
    if err:
        return err
    raw = request.args.get("date")
    day = _parse_date(raw)
    if raw and day is None:
        return _fail("date must be YYYY-MM-DD.")
    day = day or date.today()
    return jsonify({"status": "ok", **_timeline_payload(uid, day, _client_now(day))})


@bp.route("/api/today/timeline/move", methods=["POST"])
def today_timeline_move():
    uid, err = _guard()
    if err:
        return err
    App = _app()
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _fail("Send a JSON object.")
    block_id = str(body.get("block_id") or "").strip()[:64]
    if not block_id:
        return _fail("block_id is required.")
    day = _parse_date(body.get("date"))
    if day is None:
        return _fail("date must be YYYY-MM-DD.")
    start = parse_clock(body.get("start"))
    if start is None:
        return _fail("start must be a time like 16:30.")
    preview = bool(body.get("preview"))

    row, data, _progress = _active_plan(uid)
    if row is None or not data:
        return _fail("There's no saved plan to move blocks in yet.", 404)
    rotation, bell = _rotation(uid)
    meetings = [r.to_meeting() for r in _class_rows(uid)]
    busy = _calendar_busy(day)
    check = timeline_service.check_move(
        day, block_id, start, schedule_data=data, meetings=meetings,
        rotation=rotation, bell=bell, busy=busy,
    )
    if not check.found:
        return _fail(check.summary, 404)
    result = {"status": "ok", "preview": preview, **check.to_dict()}
    if preview:
        return jsonify({**result, "saved": False})
    if not check.allowed:
        return jsonify({**result, "status": "error", "message": check.summary, "saved": False}), 409

    timeline_service.apply_move(data, day, block_id, check)
    row.schedule_data = json.dumps(data)
    try:
        App.db.session.commit()
    except Exception as exc:
        App.db.session.rollback()
        logger.warning("timeline move save failed: %s", exc)
        return _fail("Couldn't save that move. Your plan is unchanged.", 500)
    _after_change(uid)
    return jsonify({**result, "saved": True, "timeline": _timeline_payload(uid, day, _client_now(day))})
