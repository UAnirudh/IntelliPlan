"""Hand-built study days, and the presets that save their shape.

The generated scheduler decides where work goes. That is the right
default and it is wrong for a real week: a student who has band practice
until 6:30 on the days the availability form does not know about, or who
simply wants Chemistry before dinner because that is when they think
best, has no way to say so. This is that way.

What it is not: a second scheduler. There is no placement engine here
and no AI. The student states the blocks and the times; this validates
them, refuses the ones that cannot be true, and writes the result in the
exact shape the interactive view already renders. Everything downstream
— progress ticks, reflow, the day archive — works on it unchanged
because it is the same structure the engine produces.

Presets are dateless. A saved day is a routine ("Weekday evening"), and
applying one stamps its clock times onto whatever date is chosen.

Endpoints:
  GET    /api/scheduler/manual/presets
  POST   /api/scheduler/manual/presets            {name, blocks}
  DELETE /api/scheduler/manual/presets/<id>
  POST   /api/scheduler/manual/build              {days:[{date, blocks}], name?, save?}
"""

from __future__ import annotations

import json
from datetime import date as date_cls, datetime, timedelta

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user

from time_utils import utcnow

bp = Blueprint("manual_schedule_bp", __name__)

MAX_PRESETS = 12
MAX_BLOCKS_PER_DAY = 24
MAX_DAYS = 14
MIN_BLOCK_MINUTES = 5
MAX_BLOCK_MINUTES = 8 * 60


def _db():
    return current_app.intelliplan_db  # type: ignore[attr-defined]


def _models():
    a = current_app
    return (
        a.intelliplan_manual_preset_model,   # type: ignore[attr-defined]
        a.intelliplan_saved_schedule_model,  # type: ignore[attr-defined]
    )


def _fail(message, code=400, **extra):
    payload = {"status": "error", "message": message}
    payload.update(extra)
    return jsonify(payload), code


def _owner_filter(query, Model):
    """Scope to the signed-in user, or to the guest session.

    The scheduler works signed out — that is most of its first-time
    traffic — so every query here has to be answerable for a guest too.
    """
    if current_user.is_authenticated:
        return query.filter(Model.user_id == current_user.id)
    return query.filter(Model.guest_session_id == _guest_id())


def _guest_id():
    from App import get_guest_session_id
    return get_guest_session_id()


def _owner_kwargs():
    if current_user.is_authenticated:
        return {"user_id": current_user.id, "guest_session_id": None}
    return {"user_id": None, "guest_session_id": _guest_id()}


# ── Time parsing ──────────────────────────────────────────────────────
def _parse_hhmm(value):
    """Accept "18:30" and "6:30 PM". Returns minutes past midnight, or None.

    The <input type="time"> the UI uses always sends 24-hour, but a preset
    saved by an older build, or a value typed into the field on a browser
    that renders it as text, can arrive in either form.
    """
    text = str(value or "").strip().upper()
    if not text:
        return None
    meridiem = None
    for tag in ("AM", "PM"):
        if text.endswith(tag):
            meridiem = tag
            text = text[: -len(tag)].strip()
            break
    parts = text.replace(".", ":").split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return None
    if meridiem == "PM" and hour < 12:
        hour += 12
    if meridiem == "AM" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def _fmt12(minutes):
    h, m = divmod(int(minutes) % (24 * 60), 60)
    suffix = "AM" if h < 12 else "PM"
    display = h % 12 or 12
    return f"{display}:{m:02d} {suffix}"


def _fmt24(minutes):
    h, m = divmod(int(minutes) % (24 * 60), 60)
    return f"{h:02d}:{m:02d}"


# ── Block validation ──────────────────────────────────────────────────
def normalize_blocks(raw_blocks, day_date=None):
    """Turn what the form sent into engine-shaped blocks.

    Returns (blocks, errors). Errors are per-block and name the field, so
    the UI can point at the row that is wrong rather than rejecting the
    whole day with one message.

    Overlap is checked here rather than trusted from the client. A plan
    with two things at 7 PM is not a plan, and the client cannot be the
    only thing standing between the student and one — the same endpoint
    is reachable from the API.
    """
    blocks, errors = [], []
    if not isinstance(raw_blocks, list):
        return [], [{"index": -1, "field": "blocks", "message": "blocks must be a list."}]
    if len(raw_blocks) > MAX_BLOCKS_PER_DAY:
        return [], [{"index": -1, "field": "blocks",
                     "message": f"A day can hold at most {MAX_BLOCKS_PER_DAY} blocks."}]

    for i, raw in enumerate(raw_blocks):
        if not isinstance(raw, dict):
            errors.append({"index": i, "field": "block", "message": "Not a block."})
            continue

        title = str(raw.get("assignment") or raw.get("title") or "").strip()[:200]
        is_break = bool(raw.get("is_break"))
        if not title:
            title = "Break" if is_break else ""
        if not title:
            errors.append({"index": i, "field": "assignment",
                           "message": "Give this block a name."})
            continue

        start = _parse_hhmm(raw.get("start") or raw.get("start_time"))
        if start is None:
            errors.append({"index": i, "field": "start",
                           "message": "Start time is not a time."})
            continue

        end = _parse_hhmm(raw.get("end") or raw.get("end_time"))
        duration = raw.get("duration_minutes")
        if end is None and duration is not None:
            try:
                end = start + max(0, int(duration))
            except (TypeError, ValueError):
                end = None
        if end is None:
            errors.append({"index": i, "field": "end",
                           "message": "Give an end time or a length."})
            continue

        # A block that ends before it starts is the classic result of
        # typing 11 PM to 1 AM. Rolling it over midnight would silently
        # give someone a study block at 1 in the morning, so it is
        # rejected and named instead.
        if end <= start:
            errors.append({"index": i, "field": "end",
                           "message": "Ends before it starts. A block cannot cross midnight — "
                                      "split it across two days."})
            continue

        length = end - start
        if length < MIN_BLOCK_MINUTES:
            errors.append({"index": i, "field": "end",
                           "message": f"Too short — {MIN_BLOCK_MINUTES} minutes is the minimum."})
            continue
        if length > MAX_BLOCK_MINUTES:
            errors.append({"index": i, "field": "end",
                           "message": f"Longer than {MAX_BLOCK_MINUTES // 60} hours. "
                                      "Split it into sittings."})
            continue

        block = {
            "assignment": title,
            "course": str(raw.get("course") or "").strip()[:120],
            "duration_minutes": length,
            # `time_slot` is what the app renders; `start`/`end` are what
            # this function reads. Both are kept so a normalized block can
            # be fed back through — which is exactly what applying a saved
            # preset does. Emitting only the display string made presets
            # unusable the moment they were reloaded.
            "start": _fmt24(start),
            "end": _fmt24(end),
            "time_slot": f"{_fmt12(start)} - {_fmt12(end)}",
            "notes": str(raw.get("notes") or "").strip()[:500],
            "is_break": is_break,
            "difficulty": str(raw.get("difficulty") or "Medium")[:16],
            "priority": str(raw.get("priority") or "Medium")[:16],
            "manual": True,          # so reflow leaves it where it was put
            "_start": start,
            "_end": end,
        }
        if day_date is not None:
            base = datetime.combine(day_date, datetime.min.time())
            block["start_iso"] = (base + timedelta(minutes=start)).isoformat()
            block["end_iso"] = (base + timedelta(minutes=end)).isoformat()
        blocks.append(block)

    blocks.sort(key=lambda b: b["_start"])

    # Overlap, after sorting, so one pass finds every collision.
    for prev, nxt in zip(blocks, blocks[1:]):
        if nxt["_start"] < prev["_end"]:
            errors.append({
                "index": -1, "field": "overlap",
                "message": (f"“{prev['assignment']}” and “{nxt['assignment']}” "
                            f"overlap at {_fmt12(nxt['_start'])}."),
            })

    for b in blocks:
        b.pop("_start", None)
        b.pop("_end", None)
    return blocks, errors


def _parse_date(value, fallback=None):
    text = str(value or "").strip()[:10]
    if not text:
        return fallback
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return fallback


# ── Presets ───────────────────────────────────────────────────────────
@bp.route("/api/scheduler/manual/presets", methods=["GET"])
def list_presets():
    Preset, _ = _models()
    rows = (_owner_filter(Preset.query, Preset)
            .order_by(Preset.last_used_at.desc())
            .limit(MAX_PRESETS)
            .all())
    return jsonify({"status": "ok", "presets": [r.to_dict() for r in rows]})


@bp.route("/api/scheduler/manual/presets", methods=["POST"])
def save_preset():
    Preset, _ = _models()
    db = _db()
    body = request.get_json(silent=True) or {}

    name = str(body.get("name") or "").strip()[:120]
    if not name:
        return _fail("Name this day so you can find it again.", 400, field="name")

    blocks, errors = normalize_blocks(body.get("blocks"))
    if errors:
        return _fail("Fix these before saving.", 400, errors=errors)
    if not blocks:
        return _fail("A preset needs at least one block.", 400, field="blocks")

    # Saving the same name twice is almost always an edit, not a second
    # routine. Overwriting keeps the list from filling with "Evening",
    # "Evening (2)", "Evening (3)".
    existing = _owner_filter(Preset.query, Preset).filter(Preset.name == name).first()
    if existing is not None:
        existing.blocks_json = json.dumps(blocks)
        existing.last_used_at = utcnow()
        db.session.commit()
        return jsonify({"status": "ok", "replaced": True, "preset": existing.to_dict()})

    if _owner_filter(Preset.query, Preset).count() >= MAX_PRESETS:
        return _fail(f"You already have {MAX_PRESETS} saved days. Delete one first.", 409)

    row = Preset(name=name, blocks_json=json.dumps(blocks),
                 created_at=utcnow(), last_used_at=utcnow(), **_owner_kwargs())
    db.session.add(row)
    db.session.commit()
    return jsonify({"status": "ok", "replaced": False, "preset": row.to_dict()}), 201


@bp.route("/api/scheduler/manual/presets/<int:preset_id>", methods=["DELETE"])
def delete_preset(preset_id: int):
    Preset, _ = _models()
    db = _db()
    row = _owner_filter(Preset.query, Preset).filter(Preset.id == preset_id).first()
    if row is None:
        return _fail("No such saved day.", 404)
    db.session.delete(row)
    db.session.commit()
    return jsonify({"status": "ok"})


# ── Building a plan ───────────────────────────────────────────────────
@bp.route("/api/scheduler/manual/build", methods=["POST"])
def build_manual():
    """Turn hand-placed blocks into a schedule the rest of the app renders.

    Body: {days: [{date: "2026-08-04", blocks: [...]}, ...], name?, save?,
           preset_id?}

    `preset_id` applies a saved routine to every date given, which is the
    common case — one routine, five weekdays.
    """
    Preset, SavedSchedule = _models()
    db = _db()
    body = request.get_json(silent=True) or {}

    days_in = body.get("days")
    if not isinstance(days_in, list) or not days_in:
        return _fail("Pick at least one day.", 400, field="days")
    if len(days_in) > MAX_DAYS:
        return _fail(f"At most {MAX_DAYS} days at a time.", 400, field="days")

    preset_blocks = None
    preset_row = None
    if body.get("preset_id"):
        preset_row = (_owner_filter(Preset.query, Preset)
                      .filter(Preset.id == body["preset_id"]).first())
        if preset_row is None:
            return _fail("That saved day no longer exists.", 404, field="preset_id")
        preset_blocks = preset_row.blocks()

    schedule, all_errors = [], []
    seen_dates = set()

    for i, raw_day in enumerate(days_in):
        if not isinstance(raw_day, dict):
            all_errors.append({"day": i, "message": "Not a day."})
            continue

        day_date = _parse_date(raw_day.get("date"))
        if day_date is None:
            all_errors.append({"day": i, "field": "date",
                               "message": "That is not a date."})
            continue
        if day_date in seen_dates:
            all_errors.append({"day": i, "field": "date",
                               "message": f"{day_date.isoformat()} is listed twice."})
            continue
        seen_dates.add(day_date)

        source = raw_day.get("blocks")
        if source is None:
            source = preset_blocks
        if not source:
            all_errors.append({"day": i, "field": "blocks",
                               "message": f"{day_date.isoformat()} has no blocks."})
            continue

        blocks, errors = normalize_blocks(source, day_date)
        for e in errors:
            e["day"] = i
            e["date"] = day_date.isoformat()
        all_errors.extend(errors)
        if errors:
            continue

        for n, b in enumerate(blocks):
            # Stable within a day and unique across the plan — the
            # interactive view keys progress off this, so a regenerated id
            # would lose every tick the student had made.
            b["block_id"] = f"m{day_date.isoformat()}-{n}"

        schedule.append({
            "day_name": day_date.strftime("%A"),
            "date": day_date.isoformat(),
            "blocks": blocks,
        })

    if all_errors:
        return _fail("Some of this cannot be scheduled.", 400, errors=all_errors)

    schedule.sort(key=lambda d: d["date"])
    schedule_data = {
        "schedule": schedule,
        "source": "manual",
        "generated_at": utcnow().isoformat(),
        "summary": _summary(schedule),
    }

    from App import _recompute_day_totals
    _recompute_day_totals(schedule_data, hours_per_day=2)

    saved_id = None
    if body.get("save"):
        name = str(body.get("name") or "").strip()[:200] or (
            f"Manual plan {schedule[0]['date']}")
        owner = _owner_kwargs()
        # Only one active plan at a time — the dashboard reads "the active
        # one", so leaving two would make which plan you see arbitrary.
        _owner_filter(SavedSchedule.query, SavedSchedule).update(
            {"is_active": False}, synchronize_session=False)
        row = SavedSchedule(name=name, schedule_data=json.dumps(schedule_data),
                            is_active=True, **owner)
        db.session.add(row)
        db.session.commit()
        saved_id = row.id

    if preset_row is not None:
        preset_row.times_used = (preset_row.times_used or 0) + 1
        preset_row.last_used_at = utcnow()
        db.session.commit()

    return jsonify({
        "status": "ok",
        "saved_id": saved_id,
        "data": schedule_data,
    })


def _summary(schedule):
    work = sum(1 for d in schedule for b in d["blocks"] if not b.get("is_break"))
    minutes = sum(int(b.get("duration_minutes") or 0)
                  for d in schedule for b in d["blocks"] if not b.get("is_break"))
    days = len(schedule)
    return (f"{work} block{'' if work == 1 else 's'} across "
            f"{days} day{'' if days == 1 else 's'} — "
            f"{minutes // 60}h {minutes % 60:02d}m of work, exactly where you put it.")
