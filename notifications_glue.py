"""Glue between App.py and the notification subsystem.

Imported by App.py after model setup. Every App import is LAZY (inside a
function), matching ``command_center_glue.py`` and ``active_glue.py``.

This module owns three things the pure subsystem deliberately does not:

* **Providers.** The actual WebPush / SMTP / Twilio calls, wrapped so that
  a permanent failure is reported as one — retrying a wrong phone number
  five times only costs money.
* **The sweep.** Walking students, building their events from the plan they
  can actually see, and enqueuing.
* **The routes.** A cron endpoint to flush the outbox, and a preferences
  API.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import Any

from flask import Blueprint, abort, jsonify, request
from flask_login import current_user

from time_utils import utcnow

from intelliplan.notifications import (
    Channel,
    Dispatcher,
    PermanentDeliveryError,
    preferences_from_user,
)
from intelliplan.notifications.events import EventKind
from intelliplan.notifications import sources

logger = logging.getLogger(__name__)

notifications_bp = Blueprint("notifications", __name__)


# ── Providers ─────────────────────────────────────────────────────────
#
# Each returns True on success, raises PermanentDeliveryError when the
# destination will never work, and lets anything else propagate so the
# dispatcher can retry it.


def _send_push(row: Any) -> bool:
    from App import _send_push_to_user

    delivered = _send_push_to_user(
        row.user_id,
        {"title": row.title, "body": row.body, "url": row.url},
    )
    if delivered and delivered > 0:
        return True
    # Zero endpoints means the student has no live subscription — a browser
    # they cleared, or a device they no longer use. That is not a transient
    # fault and will not fix itself on a retry.
    raise PermanentDeliveryError("no active push subscriptions")


def _send_sms(row: Any) -> bool:
    from App import User, _sms_send_for_user

    user = User.query.get(row.user_id)
    if user is None or not getattr(user, "phone", None):
        raise PermanentDeliveryError("no phone number on file")
    ok, detail = _sms_send_for_user(user, row.body[:300])
    if ok:
        return True
    text = str(detail or "").lower()
    # Twilio's "not a valid phone number" / "unsubscribed" family. Retrying
    # these is billable and pointless.
    if any(w in text for w in ("invalid", "not a valid", "unsubscribed", "blacklist", "opted out")):
        raise PermanentDeliveryError(str(detail)[:200])
    return False


def _send_email(row: Any) -> bool:
    from App import User, _send_email

    user = User.query.get(row.user_id)
    address = getattr(user, "email", None) if user else None
    if not address:
        raise PermanentDeliveryError("no email address on file")
    body = f"{row.body}\n\n{_base_url()}{row.url}\n\n— IntelliPlan"
    return bool(_send_email(address, row.title or "IntelliPlan", body))


def _base_url() -> str:
    from App import APP_BASE_URL

    return (APP_BASE_URL or "").rstrip("/")


def _senders() -> dict[Channel, Any]:
    return {
        Channel.PUSH: _send_push,
        Channel.SMS: _send_sms,
        Channel.EMAIL: _send_email,
    }


# ── Wiring ────────────────────────────────────────────────────────────


def get_dispatcher() -> Dispatcher:
    from App import NotificationOutbox, db

    return Dispatcher(NotificationOutbox, db.session, _senders())


def _preferences_for(user: Any):
    from App import PushSubscription

    subscribed = False
    try:
        subscribed = (
            PushSubscription.query.filter(PushSubscription.user_id == user.id).count() > 0
        )
    except Exception:
        pass
    return preferences_from_user(user, push_subscribed=subscribed)


def _plan_for(user_id: int) -> dict | None:
    """The student's active plan, in the shape the sources module expects."""
    import json

    from App import SavedSchedule, db

    try:
        row = (
            SavedSchedule.query.filter(
                SavedSchedule.user_id == user_id,
                SavedSchedule.is_active.is_(True),
            )
            .order_by(SavedSchedule.created_at.desc())
            .first()
        )
        if row is None:
            return None
        data = json.loads(row.schedule_data) if isinstance(row.schedule_data, str) else row.schedule_data
        if not isinstance(data, dict):
            return None
        progress = row.progress_json
        if isinstance(progress, str):
            try:
                progress = json.loads(progress)
            except (TypeError, ValueError):
                progress = None
        if isinstance(progress, dict):
            for day in data.get("schedule", []) or []:
                for block in day.get("blocks", []) or []:
                    entry = progress.get(str(block.get("id")))
                    if entry is True or (isinstance(entry, dict) and entry.get("done")):
                        block["done"] = True
        return data
    except Exception as exc:
        logger.warning("plan load failed for user %s: %s", user_id, exc)
        try:
            db.session.rollback()
        except Exception:
            pass
        return None


def _started_block_ids(user_id: int) -> list[str]:
    """Blocks the student has actually opened a session against today."""
    from App import ActiveSession

    try:
        since = utcnow() - timedelta(days=1)
        rows = (
            ActiveSession.query.filter(
                ActiveSession.user_id == user_id,
                ActiveSession.started_at >= since,
            )
            .limit(200)
            .all()
        )
        return [str(r.block_id) for r in rows if r.block_id]
    except Exception:
        return []


def sweep_user(user: Any, now: datetime | None = None) -> int:
    """Raise every event this student's current plan warrants. Returns count."""
    now = now or utcnow()
    prefs = _preferences_for(user)
    if not prefs.channels:
        return 0  # nothing enabled — do not build events nobody will read

    plan = _plan_for(user.id)
    if not plan:
        return 0

    events = sources.events_for_plan(
        plan,
        user_id=user.id,
        now=now,
        lead_minutes=prefs.lead_minutes,
        started_block_ids=_started_block_ids(user.id),
    )
    if not events:
        return 0
    return get_dispatcher().enqueue_many(events, prefs)


def sweep_all(limit: int = 500, now: datetime | None = None) -> dict[str, int]:
    """Sweep every student who could receive something.

    Filtered in SQL rather than in Python: walking every row and asking
    "do they want notifications?" is a full table scan that grows with
    signups, and the answer is no for most of them.
    """
    from App import User, db

    now = now or utcnow()
    queued = 0
    swept = 0
    try:
        users = (
            User.query.filter(
                db.or_(
                    User.push_reminders_opt_in.is_(True),
                    User.sms_reminders_opt_in.is_(True),
                    User.email_reminders_opt_in.is_(True),
                )
            )
            .limit(max(1, limit))
            .all()
        )
    except Exception as exc:
        logger.warning("notification sweep query failed: %s", exc)
        return {"users": 0, "queued": 0}

    for user in users:
        swept += 1
        try:
            queued += sweep_user(user, now=now)
        except Exception as exc:
            # One student's broken plan must never stop the sweep.
            logger.warning("sweep failed for user %s: %s", user.id, exc)
    return {"users": swept, "queued": queued}


# ── Event hooks called from elsewhere ─────────────────────────────────


def on_session_completed(session_row: Any) -> None:
    from App import User

    user_id = getattr(session_row, "user_id", None)
    if not user_id:
        return
    try:
        user = User.query.get(user_id)
        if user is None:
            return
        event = sources.session_completed_event(
            user_id=user_id,
            session_id=session_row.id,
            title=session_row.title or "Session",
            actual_minutes=session_row.active_minutes,
        )
        get_dispatcher().enqueue(event, _preferences_for(user))
    except Exception as exc:
        logger.warning("session-completed notification failed: %s", exc)


def on_plan_rescheduled(user_id: int, moved_count: int, reason: str) -> None:
    from App import User

    event = sources.reschedule_event(user_id, moved_count, reason, date.today())
    if event is None:
        return
    try:
        user = User.query.get(user_id)
        if user is not None:
            get_dispatcher().enqueue(event, _preferences_for(user))
    except Exception as exc:
        logger.warning("reschedule notification failed: %s", exc)


# ── Routes ────────────────────────────────────────────────────────────


def _cron_authorised() -> bool:
    expected = os.getenv("CRON_SECRET") or os.getenv("CRON_TOKEN") or ""
    if not expected:
        return False
    supplied = request.headers.get("X-Cron-Token") or request.args.get("secret") or ""
    return bool(supplied) and supplied == expected


@notifications_bp.route("/cron/notifications", methods=["GET", "POST"])
def cron_notifications():
    """Sweep for new events, then deliver what is due.

    Both halves in one endpoint so a single cron entry keeps the whole
    thing moving. Safe to call more often than needed: the sweep dedupes
    and the flush claims.
    """
    if not _cron_authorised():
        abort(403)
    swept = sweep_all()
    delivered = get_dispatcher().flush()
    return jsonify({"status": "ok", "swept": swept, "delivered": delivered.as_dict()})


@notifications_bp.route("/api/notifications/preferences", methods=["GET", "POST"])
def notification_preferences():
    from App import db

    if not current_user.is_authenticated:
        return jsonify({"error": "login required"}), 401

    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        _apply_preferences(current_user, body)
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.warning("preference save failed: %s", exc)
            return jsonify({"error": "Could not save preferences."}), 500

    prefs = _preferences_for(current_user)
    return jsonify(
        {
            "channels": sorted(c.value for c in prefs.channels),
            "kinds": sorted(k.value for k in prefs.kinds),
            "lead_minutes": prefs.lead_minutes,
            "utc_offset_minutes": prefs.utc_offset_minutes,
            "quiet_hours": {
                "enabled": prefs.quiet_hours.enabled,
                "start": prefs.quiet_hours.start_hour,
                "end": prefs.quiet_hours.end_hour,
            },
            "all_kinds": [k.value for k in EventKind],
        }
    )


def _apply_preferences(user: Any, body: dict) -> None:
    """Write validated preference fields. Unknown keys are ignored."""
    channels = body.get("channels")
    if isinstance(channels, list):
        wanted = {str(c).lower() for c in channels}
        user.push_reminders_opt_in = "push" in wanted
        user.sms_reminders_opt_in = "sms" in wanted
        user.email_reminders_opt_in = "email" in wanted

    kinds = body.get("kinds")
    if isinstance(kinds, list):
        valid = []
        for k in kinds:
            try:
                valid.append(EventKind(str(k)).value)
            except ValueError:
                continue
        user.notification_kinds = ",".join(valid)

    for field, attr, lo, hi in (
        ("lead_minutes", "reminder_lead_minutes", 5, 24 * 60),
        ("utc_offset_minutes", "utc_offset_minutes", -12 * 60, 14 * 60),
    ):
        if field in body:
            try:
                setattr(user, attr, max(lo, min(hi, int(body[field]))))
            except (TypeError, ValueError):
                pass

    quiet = body.get("quiet_hours")
    if isinstance(quiet, dict):
        if "enabled" in quiet:
            user.quiet_hours_enabled = bool(quiet["enabled"])
        for key, attr in (("start", "quiet_hours_start"), ("end", "quiet_hours_end")):
            if key in quiet:
                try:
                    hour = int(quiet[key])
                except (TypeError, ValueError):
                    continue
                if 0 <= hour <= 23:
                    setattr(user, attr, hour)


@notifications_bp.route("/api/notifications/recent", methods=["GET"])
def recent_notifications():
    if not current_user.is_authenticated:
        return jsonify({"error": "login required"}), 401
    dispatcher = get_dispatcher()
    return jsonify(
        {
            "pending": dispatcher.pending_count(current_user.id),
            "recent": [r.to_dict() for r in dispatcher.recent(current_user.id, 20)],
        }
    )


@notifications_bp.route("/api/notifications/test", methods=["POST"])
def send_test_notification():
    """Queue a real message through the real pipeline.

    Deliberately not a shortcut that calls the provider directly: a test
    button that bypasses the queue proves the provider works and nothing
    else, which is exactly the part that was never broken.
    """
    if not current_user.is_authenticated:
        return jsonify({"error": "login required"}), 401

    from intelliplan.notifications.events import NotificationEvent

    prefs = _preferences_for(current_user)
    if not prefs.channels:
        return jsonify({"error": "Turn on at least one channel first."}), 400

    event = NotificationEvent(
        kind=EventKind.SESSION_UPCOMING,
        user_id=current_user.id,
        dedupe_key=f"test:{utcnow().isoformat(timespec='seconds')}",
        context={"title": "Test notification", "minutes_until": 0, "planned_minutes": 25},
        url="/active",
    )
    dispatcher = get_dispatcher()
    rows = dispatcher.enqueue(event, prefs)
    result = dispatcher.flush(limit=10)
    return jsonify(
        {
            "status": "ok",
            "queued": [r.channel for r in rows],
            "delivered": result.as_dict(),
        }
    )
