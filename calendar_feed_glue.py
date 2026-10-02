"""Live plan calendar feed: a secret URL Apple/Google/Outlook subscribe to.

Routes
------
* ``GET  /calendar/<token>.ics``      -- the feed. No cookie, no header: the
  token in the path is the whole credential, because that is all a calendar
  client can send.
* ``GET  /api/calendar/feed``         -- the student's link, if any.
* ``POST /api/calendar/feed``         -- create it (or change privacy mode).
* ``POST /api/calendar/feed/rotate``  -- new link; the old one stops working.
* ``POST /api/calendar/feed/revoke``  -- turn the feed off.

The ICS is built per request from the student's active saved plan, so it is
always the current plan: every reflow, autopilot run, "can't study today"
and Grade Pulse insertion is in the next fetch with nothing to invalidate.
Rendering is in ``intelliplan/services/plan_feed.py``.

Failure is a 404, always
------------------------
An unknown token, a malformed one and a revoked one all answer the same
plain 404. Telling them apart would let someone probing URLs learn which
tokens once existed, and a calendar client does nothing different with a
403 than with a 404 anyway.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from flask import Blueprint, Response, jsonify, request
from flask_login import current_user

from time_utils import utcnow

from intelliplan.services import plan_feed

logger = logging.getLogger(__name__)

calendar_feed_bp = Blueprint("calendar_feed", __name__)

#: 32 random bytes, URL-safe base64: 43 characters, 256 bits. Guessing one is
#: not a thing rate limits need to defend against; they are there for cost.
_TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
#: Don't write last_fetched_at on every poll -- Google alone may fetch a
#: popular feed several times an hour.
_TOUCH_EVERY = timedelta(minutes=30)
DETAILS = ("full", "private")


def _model():
    from App import CalendarFeedToken

    return CalendarFeedToken


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _base_url() -> str:
    from App import APP_BASE_URL

    base = (APP_BASE_URL or "").rstrip("/")
    if not base:
        base = request.url_root.rstrip("/")
    return base


def feed_urls(token: str) -> dict[str, str]:
    """Every form of the link a student might need.

    ``webcal://`` is what makes iOS and macOS offer "Subscribe" instead of
    downloading a one-off file. Google Calendar has no webcal handler of its
    own, but its ``?cid=`` page accepts the feed URL and opens the
    subscribe dialog.
    """
    https = f"{_base_url()}/calendar/{token}.ics"
    webcal = re.sub(r"^https?://", "webcal://", https)
    return {
        "https": https,
        "webcal": webcal,
        "google": "https://calendar.google.com/calendar/render?cid=" + quote(webcal, safe=""),
        "outlook": (
            "https://outlook.live.com/calendar/0/addfromweb?url="
            + quote(https, safe="") + "&name=" + quote(plan_feed.CAL_NAME, safe="")
        ),
    }


def _status(row: Any) -> dict[str, Any]:
    if row is None or row.revoked_at is not None or not row.token:
        return {"status": "ok", "active": False}
    return {
        "status": "ok",
        "active": True,
        "detail": row.detail or "full",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_fetched_at": row.last_fetched_at.isoformat() if row.last_fetched_at else None,
        "urls": feed_urls(row.token),
    }


# ── The feed ──────────────────────────────────────────────────────────


def _not_found() -> Response:
    resp = Response("Not found\n", status=404, mimetype="text/plain")
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _due_items(user_id: int) -> list[dict[str, str]]:
    """Due dates the server already holds, without calling an LMS.

    A calendar client polls this hourly for every subscriber; fanning that
    out to Canvas and StudentVUE would be slow, would hit their rate limits,
    and would make the feed fail whenever they do. So: the student's own
    and imported tasks, the plan's own due dates (added by the renderer),
    and the LMS list if a recent page load left it in the cache.
    """
    from App import ManualTask, _lms_cache_get

    items: list[dict[str, str]] = []
    try:
        for t in ManualTask.query.filter_by(user_id=user_id, done=False).limit(500).all():
            if t.due_date:
                items.append({"title": t.title, "course": t.course or "", "due_date": t.due_date})
    except Exception as exc:
        logger.info("calendar feed: manual tasks unavailable: %s", exc)
    try:
        for t in _lms_cache_get(user_id) or []:
            if isinstance(t, dict) and t.get("due_date"):
                items.append({"title": str(t.get("title") or ""),
                              "course": str(t.get("course") or ""),
                              "due_date": str(t.get("due_date"))})
    except Exception:
        pass
    return items


@calendar_feed_bp.route("/calendar/<string:token>.ics", methods=["GET", "HEAD"])
def plan_feed_ics(token: str):
    if not _TOKEN_SHAPE.match(token or ""):
        return _not_found()
    Model = _model()
    row = Model.query.filter_by(token_hash=hash_token(token)).first()
    if row is None or row.revoked_at is not None:
        return _not_found()

    import notifications_glue

    plan = notifications_glue._plan_for(row.user_id) or {}
    now = utcnow()
    body = plan_feed.build_plan_calendar(
        plan,
        _due_items(row.user_id),
        tz_name=notifications_glue._timezone_for(row.user_id),
        detail=row.detail or "full",
        now=now,
        link=f"{_base_url()}/scheduler",
    )
    etag = plan_feed.content_etag(body)

    if row.last_fetched_at is None or row.last_fetched_at < now - _TOUCH_EVERY:
        from App import db

        try:
            row.last_fetched_at = now
            db.session.commit()
        except Exception:
            db.session.rollback()

    headers = {
        "ETag": etag,
        # Private: the URL is a credential, so no shared cache may keep a
        # copy. Short: a reflow should reach the phone within the hour.
        "Cache-Control": "private, max-age=900",
        "Content-Disposition": 'inline; filename="intelliplan-plan.ics"',
        "X-Robots-Tag": "noindex, nofollow",
        "Referrer-Policy": "no-referrer",
    }
    if request.if_none_match.contains(etag.strip('"')):
        return Response(status=304, headers=headers)
    return Response(body, status=200, mimetype="text/calendar",
                    headers={**headers, "Content-Type": "text/calendar; charset=utf-8"})


# ── Managing the link ─────────────────────────────────────────────────


def _login_required():
    if not current_user.is_authenticated:
        return jsonify({"status": "error", "message": "login required"}), 401
    return None


def _current_row():
    return _model().query.filter_by(user_id=current_user.id).first()


def _issue(row: Any, detail: str | None) -> Any:
    """Mint a fresh token onto ``row`` (creating it if needed)."""
    from App import db

    token = _new_token()
    if row is None:
        row = _model()(user_id=current_user.id, created_at=utcnow())
        db.session.add(row)
    elif row.token:
        row.rotated_at = utcnow()
    row.token = token
    row.token_hash = hash_token(token)
    row.revoked_at = None
    row.last_fetched_at = None
    if detail in DETAILS:
        row.detail = detail
    elif not row.detail:
        row.detail = "full"
    return row


def _commit_and_report(row: Any):
    from App import db

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.warning("calendar feed save failed: %s", exc)
        return jsonify({"status": "error", "message": "Could not save. Try again."}), 500
    return jsonify(_status(row))


@calendar_feed_bp.route("/api/calendar/feed", methods=["GET", "POST"])
def calendar_feed_settings():
    denied = _login_required()
    if denied:
        return denied
    row = _current_row()
    if request.method == "GET":
        return jsonify(_status(row))
    body = request.get_json(silent=True) or {}
    detail = body.get("detail")
    if row is not None and row.revoked_at is None and row.token:
        # Already live: only the privacy mode can change here. Changing the
        # link is what /rotate is for, and doing it implicitly would break a
        # subscription the student set up on another device.
        if detail in DETAILS:
            row.detail = detail
        return _commit_and_report(row)
    return _commit_and_report(_issue(row, detail))


@calendar_feed_bp.route("/api/calendar/feed/rotate", methods=["POST"])
def calendar_feed_rotate():
    denied = _login_required()
    if denied:
        return denied
    return _commit_and_report(_issue(_current_row(), None))


@calendar_feed_bp.route("/api/calendar/feed/revoke", methods=["POST"])
def calendar_feed_revoke():
    denied = _login_required()
    if denied:
        return denied
    row = _current_row()
    if row is None:
        return jsonify({"status": "ok", "active": False})
    row.revoked_at = utcnow()
    # Drop the readable copy too. The hash stays (unique) so the old URL
    # keeps answering 404 rather than ever matching a future row.
    row.token = None
    return _commit_and_report(row)
