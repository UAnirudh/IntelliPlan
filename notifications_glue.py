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
import time
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


class DeliveryFailed(Exception):
    """A retryable failure that knows why it failed.

    Returning a bare ``False`` from a provider is also retryable, but the
    dispatcher can only record it as "provider reported failure" — which
    tells whoever is looking at a stuck queue nothing at all. Raising this
    instead puts the provider's own words in ``last_error``, where they
    are visible from /api/notifications/recent and the admin views.

    Deliberately local to this module rather than added to the
    notifications package: the subsystem's contract is "True, raise
    PermanentDeliveryError, or raise anything else to retry", and this is
    just a well-named instance of "anything else".
    """


def _push_ttl_for(row: Any) -> int | None:
    """How long a push service should hold this message.

    The outbox already knows when a message stops being worth delivering —
    ``expires_at`` exists precisely so a "starts in 15 minutes" reminder is
    not delivered at midnight. Handing that same deadline to the push
    service extends the guarantee past our own queue: if the phone is off
    until after the deadline, the message is dropped by the service rather
    than arriving stale.

    Returns None for rows with no expiry, letting the caller's default
    stand.
    """
    from App import PUSH_MIN_TTL
    from time_utils import utcnow

    if not getattr(row, "expires_at", None):
        return None
    remaining = int((row.expires_at - utcnow()).total_seconds())
    return max(PUSH_MIN_TTL, remaining)


def _send_push(row: Any) -> bool:
    from App import _send_push_to_user

    delivered = _send_push_to_user(
        row.user_id,
        {"title": row.title, "body": row.body, "url": row.url},
        ttl=_push_ttl_for(row),
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
    # The "will never work" family. Retrying a malformed number or an
    # opted-out recipient just burns attempts until the row goes dead.
    if any(w in text for w in ("invalid", "not a valid", "unsubscribed",
                               "blacklist", "opted out", "must be 10 digits",
                               "no phone")):
        raise PermanentDeliveryError(str(detail)[:200])
    # A misconfigured sender is retryable — the key can be added without a
    # redeploy — but it is not a transient blip, and returning a bare
    # False recorded it in the outbox as "provider reported failure". That
    # is the least useful sentence available: the real reason ("No email
    # sender configured", "Resend 403: domain not verified") went to
    # stdout and nowhere a person looking at the queue would find it.
    raise DeliveryFailed(str(detail or "provider reported failure")[:400])


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


# ── In-process ticker ─────────────────────────────────────────────────
#
# /cron/notifications below sweeps and flushes, and it works. It was just
# never called. There is no cron entry, no scheduled job, and nothing in
# the Procfile but the web process — so the outbox was only ever swept by
# a manual request, which in practice meant never. Every part of the
# reminder pipeline was correct and the whole thing was inert: preferences
# saved, phone numbers verified, events never raised, nothing delivered.
#
# Rather than make working reminders depend on someone remembering to
# configure a scheduler, the app now drives its own timer. The HTTP
# endpoint stays, so a real external cron can still drive it (and should,
# at scale) — this is the floor, not the ceiling.

#: Seconds between ticks. A reminder is scheduled to the minute, so a
#: minute of granularity is the most precision that can matter.
TICK_SECONDS = int(os.getenv("NOTIFICATIONS_TICK_SECONDS", "60"))

#: How long a tick may hold the lease. Comfortably longer than a tick
#: takes, comfortably shorter than the gap between ticks is unimportant —
#: what matters is that a dead holder's lease expires promptly enough that
#: delivery resumes without an operator.
_LEASE_SECONDS = max(TICK_SECONDS * 3, 180)

_ticker_started = False


def _tick_once(app: Any) -> dict | None:
    """One sweep + flush, if this process wins the lease."""
    from App import db
    from intelliplan.notifications.models import register_lease

    CronLease = register_lease(db)
    now = utcnow()
    holder = f"{os.getpid()}"

    # Ensure the row exists. A losing race here raises on the unique/PK
    # constraint, which simply means someone else created it first.
    try:
        if db.session.get(CronLease, "notifications") is None:
            db.session.add(CronLease(name="notifications", holder="",
                                     expires_at=now))
            db.session.commit()
    except Exception:
        db.session.rollback()

    # Atomic acquire: only rows whose lease has already expired match, so
    # exactly one caller can transition it.
    try:
        claimed = (
            db.session.query(CronLease)
            .filter(CronLease.name == "notifications",
                    CronLease.expires_at <= now)
            .update(
                {"holder": holder,
                 "expires_at": now + timedelta(seconds=_LEASE_SECONDS),
                 "last_run_at": now},
                synchronize_session=False,
            )
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.warning("notification lease acquire failed: %s", exc)
        return None

    if not claimed:
        return None  # another worker owns this tick

    swept = sweep_all()
    delivered = get_dispatcher().flush()
    return {"swept": swept, "delivered": delivered.as_dict()}


def start_ticker(app: Any) -> bool:
    """Start the background timer. Safe to call more than once.

    Returns True if this call started it.
    """
    global _ticker_started

    if _ticker_started:
        return False
    if os.getenv("NOTIFICATIONS_INPROCESS_CRON", "1") == "0":
        logger.info("in-process notification ticker disabled by env")
        return False
    # Flask's reloader runs the module twice; only the child serves.
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return False

    import threading

    def _loop() -> None:
        # Stagger the first tick. Every worker booting at once and
        # immediately hitting the database is a thundering herd on the one
        # moment the process is already busiest.
        import random
        time.sleep(random.uniform(5, TICK_SECONDS))
        while True:
            try:
                with app.app_context():
                    result = _tick_once(app)
                if result and (result["delivered"].get("sent")
                               or result["swept"].get("queued")):
                    logger.info("notification tick: %s", result)
            except Exception as exc:
                # This thread must outlive every possible failure. If it
                # dies, reminders stop and nothing says so.
                logger.warning("notification tick failed: %s", exc)
            time.sleep(TICK_SECONDS)

    threading.Thread(target=_loop, name="ip-notifications",
                     daemon=True).start()
    _ticker_started = True
    logger.info("in-process notification ticker started (every %ss)", TICK_SECONDS)
    return True


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
            # Marketing consent rides along on this endpoint for the settings
            # page's convenience, but it is NOT a notification channel and is
            # never derived from `channels`. See _apply_preferences.
            "marketing_emails_opt_in": bool(
                getattr(current_user, "marketing_emails_opt_in", False)
            ),
        }
    )


def _apply_preferences(user: Any, body: dict) -> None:
    """Write validated preference fields. Unknown keys are ignored."""
    channels = body.get("channels")
    if isinstance(channels, list):
        wanted = {str(c).lower() for c in channels}
        user.push_reminders_opt_in = "push" in wanted
        user.sms_reminders_opt_in = "sms" in wanted
        # "email" here is the transactional deadline reminder the student
        # asked for. It is deliberately not read as marketing consent — see
        # the comment on User.marketing_emails_opt_in. The marketing flag is
        # only ever set from its own explicit key below.
        user.email_reminders_opt_in = "email" in wanted

    if "marketing_emails_opt_in" in body:
        from datetime import datetime

        wants_marketing = bool(body["marketing_emails_opt_in"])
        if wants_marketing and not getattr(user, "marketing_emails_opt_in", False):
            # Stamp the date only on the off→on edge. Re-saving the settings
            # page must not keep moving the consent date forward: the whole
            # value of the timestamp is that it records when they actually
            # agreed, and a date that drifts evidences nothing.
            user.marketing_opt_in_at = datetime.utcnow()
            # Clear any suppression for this address. Without this the
            # toggle silently does nothing for anyone who ever clicked
            # unsubscribe — the flag would flip on and the gate would keep
            # refusing. A signed-in person asking for the mail again is
            # stronger, fresher evidence than the old unsubscribe.
            _clear_suppression(getattr(user, "email", None))
        user.marketing_emails_opt_in = wants_marketing
        # Turning it off leaves marketing_opt_in_at alone on purpose. It is
        # the record of a consent that did happen; erasing it destroys the
        # evidence trail for the period when mail was legitimately sent.

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


def _clear_suppression(address: str | None) -> None:
    """Remove an address from the marketing suppression list.

    Only ever called for a signed-in user re-enabling marketing on their own
    settings page. There is no other route back off the suppression list,
    which is deliberate: it must not be possible to un-suppress someone
    without them asking, in their own account.
    """
    if not address:
        return
    from App import EmailSuppression, db

    try:
        rows = EmailSuppression.query.filter_by(email=address.strip().lower()).all()
        for row in rows:
            db.session.delete(row)
    except Exception as exc:
        logger.warning("could not clear suppression for %s: %s", address, exc)
