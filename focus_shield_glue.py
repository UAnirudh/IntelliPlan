"""Focus Shield: the browser extension's plan-aware site blocking.

Imported by App.py after model setup; every App import is lazy, matching
the other ``*_glue.py`` modules.

The extension blocks the student's distractor list *only* while a planned
study block or an Active session is under way, and asks this module when
that is. It never reports what the student browsed -- not to us, not to a
parent, not to a teacher. The only things that cross the network are "is
there a block now?" (read) and "I took a break" / "I'm done early" (write).

Routes
------
``GET  /extension/focus/current``  current + next block, blocklist, break state
``POST /extension/focus/break``    spend one of this block's 5-minute breaks
``POST /extension/focus/done``     "I'm done early": stop blocking this block
``GET/POST /api/focus-shield/settings``  the web settings card

The ``/extension/`` routes take the extension token (``X-Extension-Token``
or ``Authorization: Bearer``) and also a signed-in web session, so the
settings page can preview what the extension will see.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from flask import Blueprint, jsonify, make_response, request
from flask_login import current_user

from intelliplan.services import focus_shield as shield
from time_utils import to_local, to_utc, utcnow, zone_for

logger = logging.getLogger(__name__)

focus_shield_bp = Blueprint("focus_shield", __name__)


# ── identity ──────────────────────────────────────────────────────────


def _request_user() -> Any | None:
    """The extension token's owner, else the signed-in web user."""
    from App import _extension_bearer_token, get_extension_user

    token = _extension_bearer_token()
    if token:
        # A token that is present but wrong is a 401, not a fall-through to
        # the cookie: the extension needs to know to sign in again.
        return get_extension_user(token)
    try:
        if current_user.is_authenticated:
            return current_user._get_current_object()
    except Exception:
        pass
    return None


def _cors(resp: Any) -> Any:
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Extension-Token, Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _json(data: dict[str, Any], status: int = 200) -> Any:
    resp = jsonify(data)
    resp.status_code = status
    return _cors(resp)


def _preflight() -> Any:
    return _cors(make_response("", 204))


# ── settings row ──────────────────────────────────────────────────────


def _settings_for(user_id: int, create: bool = False) -> Any | None:
    from App import FocusShieldSettings, db

    row = FocusShieldSettings.query.filter_by(user_id=user_id).first()
    if row is None and create:
        row = FocusShieldSettings(user_id=user_id)
        db.session.add(row)
        db.session.commit()
    return row


def _blocklist(row: Any | None) -> list[str]:
    if row is None or row.blocklist_json is None:
        return list(shield.DEFAULT_BLOCKLIST)
    try:
        return shield.clean_blocklist(json.loads(row.blocklist_json))
    except (TypeError, ValueError):
        return list(shield.DEFAULT_BLOCKLIST)


def _breaks_allowed(row: Any | None) -> int:
    value = shield.DEFAULT_BREAKS_PER_BLOCK if row is None else row.breaks_per_block
    try:
        return max(0, min(shield.MAX_BREAKS_PER_BLOCK, int(value)))
    except (TypeError, ValueError):
        return shield.DEFAULT_BREAKS_PER_BLOCK


def _settings_payload(row: Any | None) -> dict[str, Any]:
    return {
        "enabled": True if row is None else bool(row.enabled),
        "blocklist": _blocklist(row),
        "default_blocklist": list(shield.DEFAULT_BLOCKLIST),
        "uses_defaults": row is None or row.blocklist_json is None,
        "breaks_per_block": _breaks_allowed(row),
        "break_minutes": shield.BREAK_MINUTES,
    }


# ── timezone ──────────────────────────────────────────────────────────


def _timezone(user: Any) -> tuple[str, int]:
    """``(tz_name, utc_offset_minutes)`` for reading this student's plan.

    The extension sends the device's IANA zone as ``?tz=``. It wins when it
    is a real zone, because the device clock is the one the student is
    looking at -- a student on a trip is studying in the zone they are in.
    Otherwise the zone the streak engine captured from the browser, then
    the legacy fixed offset, then UTC.
    """
    sent = (request.args.get("tz") or request.headers.get("X-Timezone") or "").strip()[:64]
    if sent and zone_for(sent) is not None:
        return sent, 0
    try:
        from App import UserStreak

        row = UserStreak.query.filter_by(user_id=user.id).first()
        if row is not None and row.timezone and zone_for(row.timezone) is not None:
            return row.timezone, 0
    except Exception:
        pass
    try:
        offset = int(getattr(user, "utc_offset_minutes", 0) or 0)
    except (TypeError, ValueError):
        offset = 0
    return "", max(-12 * 60, min(14 * 60, offset))


# ── plan + active session ─────────────────────────────────────────────


def _plan_for(user_id: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None, Any | None]:
    """``(schedule_data, progress, row)`` for the student's live plan."""
    from App import SavedSchedule

    row = (
        SavedSchedule.query.filter_by(user_id=user_id, is_active=True)
        .order_by(SavedSchedule.created_at.desc())
        .first()
    )
    if row is None:
        return None, None, None
    try:
        data = json.loads(row.schedule_data) if isinstance(row.schedule_data, str) else row.schedule_data
    except (TypeError, ValueError):
        data = None
    try:
        progress = json.loads(row.progress_json) if row.progress_json else {}
    except (TypeError, ValueError):
        progress = {}
    return (data if isinstance(data, dict) else None), (progress if isinstance(progress, dict) else {}), row


def _active_row(user_id: int) -> Any | None:
    from App import ActiveFocusSample, ActiveSession, db
    from intelliplan.repositories.active_sessions import ActiveSessionRepository

    repo = ActiveSessionRepository(ActiveSession, ActiveFocusSample, db.session)
    try:
        repo.expire_stale(user_id=user_id, guest_id=None)
        return repo.active(user_id=user_id, guest_id=None)
    except Exception as exc:
        logger.warning("focus shield: active lookup failed: %s", exc)
        try:
            db.session.rollback()
        except Exception:
            pass
        return None


def current_state(user: Any, now_utc: datetime | None = None) -> dict[str, Any]:
    """Everything the extension needs, for one student. Owner-scoped."""
    now_utc = now_utc or utcnow()
    tz_name, offset = _timezone(user)

    def _local(moment: datetime) -> datetime:
        return to_local(moment, tz_name, offset)

    def _utc(moment: datetime) -> datetime:
        return to_utc(moment, tz_name, offset)

    local_now = _local(now_utc)
    plan, progress, _ = _plan_for(user.id)
    windows = shield.plan_windows(plan, progress)
    active = shield.active_window(_active_row(user.id), now_utc, _local)
    resolved = shield.resolve(windows, local_now, _utc, active=active)

    row = _settings_for(user.id)
    settings = _settings_payload(row)
    current = resolved["current"]

    breaks_used = 0
    break_until = None
    released = False
    if current is not None and row is not None:
        if row.break_block_key == current["key"]:
            breaks_used = int(row.breaks_used or 0)
            if row.break_until and row.break_until > now_utc:
                break_until = row.break_until
        if row.released_block_key == current["key"] and row.released_until and row.released_until > now_utc:
            released = True
    allowed = settings["breaks_per_block"]

    return {
        "status": "ok",
        **settings,
        "server_now": shield._z(now_utc),
        "timezone": tz_name or f"UTC{offset:+d}m",
        "local_date": local_now.date().isoformat(),
        "current": current,
        "next": resolved["next"],
        "upcoming": resolved["upcoming"],
        "break_until": shield._z(break_until) if break_until else None,
        "breaks_used": breaks_used,
        "breaks_left": max(0, allowed - breaks_used),
        "released": released,
        # The one answer the extension acts on.
        "blocking": bool(
            settings["enabled"] and current is not None and not released and break_until is None
        ),
    }


# ── routes: extension ─────────────────────────────────────────────────


@focus_shield_bp.route("/extension/focus/current", methods=["GET", "OPTIONS"])
def focus_current():
    if request.method == "OPTIONS":
        return _preflight()
    user = _request_user()
    if user is None:
        return _json({"status": "error", "message": "Not authenticated"}, 401)
    try:
        return _json(current_state(user))
    except Exception as exc:
        logger.warning("focus shield: current failed: %s", exc)
        return _json({"status": "error", "message": "Could not read your plan."}, 500)


def _body() -> dict[str, Any]:
    data = request.get_json(force=True, silent=True)
    return data if isinstance(data, dict) else {}


@focus_shield_bp.route("/extension/focus/break", methods=["POST", "OPTIONS"])
def focus_break():
    """Spend one break on the current block. Limited per block, server-side,
    so reinstalling the extension does not refill the allowance."""
    if request.method == "OPTIONS":
        return _preflight()
    user = _request_user()
    if user is None:
        return _json({"status": "error", "message": "Not authenticated"}, 401)
    from App import db

    now = utcnow()
    state = current_state(user, now)
    current = state["current"]
    if current is None:
        return _json({"status": "error", "message": "No study block is running."}, 409)
    row = _settings_for(user.id, create=True)
    if row.break_block_key != current["key"]:
        row.break_block_key = current["key"][:80]
        row.breaks_used = 0
        row.break_until = None
    if row.break_until and row.break_until > now:
        return _json({"status": "ok", "break_until": shield._z(row.break_until),
                      "breaks_left": max(0, _breaks_allowed(row) - int(row.breaks_used or 0))})
    if int(row.breaks_used or 0) >= _breaks_allowed(row):
        return _json({"status": "error", "message": "No breaks left in this block.",
                      "breaks_left": 0}, 429)
    row.breaks_used = int(row.breaks_used or 0) + 1
    row.break_until = now + timedelta(minutes=shield.BREAK_MINUTES)
    db.session.commit()
    return _json({
        "status": "ok",
        "break_until": shield._z(row.break_until),
        "breaks_left": max(0, _breaks_allowed(row) - row.breaks_used),
    })


@focus_shield_bp.route("/extension/focus/done", methods=["POST", "OPTIONS"])
def focus_done():
    """"I'm done early." Unblocks until this block's planned end, and checks
    the block off in the plan so the dashboard agrees. An Active session is
    left for the student to finish in Active, which is where the minutes and
    the "did you finish?" answer are recorded."""
    if request.method == "OPTIONS":
        return _preflight()
    user = _request_user()
    if user is None:
        return _json({"status": "error", "message": "Not authenticated"}, 401)
    from App import db

    now = utcnow()
    state = current_state(user, now)
    current = state["current"]
    if current is None:
        return _json({"status": "ok", "released": False, "message": "No study block is running."})
    row = _settings_for(user.id, create=True)
    row.released_block_key = current["key"][:80]
    try:
        ends = datetime.fromisoformat(current["ends_at"].rstrip("Z"))
    except ValueError:
        ends = now + timedelta(hours=1)
    row.released_until = max(ends, now + timedelta(minutes=1))

    if current["source"] == "plan":
        _, progress, plan_row = _plan_for(user.id)
        if plan_row is not None:
            entry = progress.get(current["key"])
            entry = dict(entry) if isinstance(entry, dict) else {}
            entry["done"] = True
            entry.setdefault("done_via", "focus_shield")
            progress[current["key"]] = entry
            plan_row.progress_json = json.dumps(progress)
    db.session.commit()
    if current["source"] == "plan":
        try:
            from App import invalidate_schedule_cache

            invalidate_schedule_cache(user.id, None)
        except Exception:
            pass
    return _json({"status": "ok", "released": True, "released_until": shield._z(row.released_until),
                  "source": current["source"]})


# ── routes: web settings ──────────────────────────────────────────────


@focus_shield_bp.route("/api/focus-shield/settings", methods=["GET", "POST"])
def shield_settings():
    if not current_user.is_authenticated:
        return jsonify({"status": "error", "message": "Sign in to change Focus Shield."}), 401
    from App import db

    if request.method == "GET":
        return jsonify({"status": "ok", **_settings_payload(_settings_for(current_user.id))})

    body = _body()
    row = _settings_for(current_user.id, create=True)
    if "enabled" in body:
        row.enabled = bool(body.get("enabled"))
    if "blocklist" in body:
        raw = body.get("blocklist")
        if isinstance(raw, str):
            raw = raw.replace(",", "\n").splitlines()
        if not isinstance(raw, list):
            return jsonify({"status": "error", "message": "blocklist must be a list of sites."}), 400
        cleaned = shield.clean_blocklist(raw)
        rejected = [str(x)[:80] for x in raw if str(x).strip() and shield.normalize_domain(x) is None]
        row.blocklist_json = json.dumps(cleaned)
    else:
        rejected = []
    if body.get("reset_blocklist"):
        row.blocklist_json = None
    if "breaks_per_block" in body:
        try:
            row.breaks_per_block = max(0, min(shield.MAX_BREAKS_PER_BLOCK, int(body.get("breaks_per_block"))))
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "breaks_per_block must be a number."}), 400
    db.session.commit()
    return jsonify({"status": "ok", **_settings_payload(row), "rejected": rejected})


def install(app: Any) -> None:
    app.register_blueprint(focus_shield_bp)
