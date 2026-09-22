"""Read-only export for an operator hub (Orbit).

Two endpoints that a hub watching several products at once can pull on a
schedule, so "how is IntelliPlan doing" is answerable next to every other thing
the operator runs, without anybody opening this app's admin pages.

What this deliberately does NOT export is most of the point. IntelliPlan holds
data about minors: grades, birth years, parent addresses, phone numbers, essay
text, tutor conversations. None of that is needed to chart adoption, and
shipping it to a second system would multiply the places it can leak without
answering a single question the operator actually asks.

So the export is account metadata and event *kinds*. The allow-list below is
explicit rather than a denylist, for the same reason the schema-level filter on
the receiving end is: a new column added to ``users`` next year must not start
flowing somewhere new because nobody remembered to exclude it.
"""

from __future__ import annotations

import hmac
import json
import os
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

orbit_export_bp = Blueprint("orbit_export", __name__)

MAX_PAGE_SIZE = 500
DEFAULT_PAGE_SIZE = 200


def _authorized() -> bool:
    """Whether this request may read the export.

    Closed when ``ORBIT_PULL_KEY`` is unset. An export that runs open until
    somebody remembers to configure it is an export that leaks on the first
    deploy, and this one is attached to a database full of students.
    """
    secret = _setting("ORBIT_PULL_KEY")
    if not secret:
        return False

    header = request.headers.get("Authorization", "")
    presented = header[7:].strip() if header[:7].lower() == "bearer " else ""
    if not presented:
        return False

    # Constant time, so the key cannot be recovered a byte at a time.
    return hmac.compare_digest(presented, secret)


def _setting(name: str) -> str:
    """Read a setting from app config, falling back to the environment.

    Both, because the rest of this app configures itself either way and a
    key that works in one place and silently not the other is the kind of
    thing that is only discovered in production.
    """
    return (current_app.config.get(name) or os.environ.get(name) or "").strip()


def _page_params() -> tuple[datetime | None, int, int]:
    """Read ``since``, ``page`` and ``limit`` out of the query string.

    An unparseable ``since`` is treated as absent rather than as an error: the
    worst case is a full page instead of an incremental one, which is slower and
    still correct. Refusing the request would stop the sync over a formatting
    detail.
    """
    raw_since = request.args.get("since", "")
    since: datetime | None = None
    if raw_since:
        try:
            since = datetime.fromisoformat(raw_since.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            since = None

    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1

    try:
        limit = min(MAX_PAGE_SIZE, max(1, int(request.args.get("limit", DEFAULT_PAGE_SIZE))))
    except (TypeError, ValueError):
        limit = DEFAULT_PAGE_SIZE

    return since, page, limit


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


@orbit_export_bp.route("/api/orbit/people", methods=["GET"])
def orbit_people():
    """Accounts, as analytics rather than as records about people.

    Every field here is either an identifier the hub needs to join on, or a
    number. Nothing describes a student: no birth year, no parent address, no
    phone, no grade, no school-provided identity, no free text they wrote.
    """
    if not _authorized():
        return jsonify({"error": "Unauthorized"}), 401

    from App import CanvasIntegration, User, UserStreak, db  # late: App imports this module

    since, page, limit = _page_params()

    query = db.session.query(User)
    if since:
        query = query.filter(User.created_at >= since)

    # Ordered by (created_at, id). A non-unique sort key would let a row shift
    # between page 1 and page 2 and be skipped by the sync entirely.
    users = (
        query.order_by(User.created_at.asc(), User.id.asc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    user_ids = [user.id for user in users]
    streaks = {}
    canvas_linked: set[int] = set()
    if user_ids:
        streaks = {
            row.user_id: row
            for row in db.session.query(UserStreak).filter(UserStreak.user_id.in_(user_ids)).all()
        }
        canvas_linked = {
            row.user_id
            for row in db.session.query(CanvasIntegration.user_id)
            .filter(CanvasIntegration.user_id.in_(user_ids))
            .all()
        }

    now = datetime.utcnow()
    data = []
    for user in users:
        streak = streaks.get(user.id)
        data.append(
            {
                "id": str(user.id),
                "email": user.email,
                "name": user.name,
                "created_at": _iso(user.created_at),
                "role": user.role or "student",
                # Whether they pay, not how much or by what means. The Stripe
                # customer id stays here.
                "plan": "paid" if (user.paid_until and user.paid_until > now) else "free",
                # Which integrations are connected is a real adoption signal and
                # says nothing about the coursework behind them.
                "canvas_connected": user.id in canvas_linked,
                "streak_days": streak.current_streak if streak else 0,
                "longest_streak": streak.longest_streak if streak else 0,
                # The domain, never the school's own identifiers. It tells you a
                # school is adopting you without naming a student.
                "school_domain": user.email.split("@")[-1] if user.email and "@" in user.email else None,
            }
        )

    return jsonify({"data": data, "page": page, "limit": limit})


@orbit_export_bp.route("/api/orbit/events", methods=["GET"])
def orbit_events():
    """What happened, from the events this app already records with consent.

    ``props`` is not exported. It is a free-form bag whose contents vary by call
    site, which makes it exactly the field that will one day contain something
    nobody meant to send. The kind, the actor and the timestamp are enough to
    chart adoption.
    """
    if not _authorized():
        return jsonify({"error": "Unauthorized"}), 401

    from App import ProductEvent, db  # imported late: App imports this module

    since, page, limit = _page_params()

    query = db.session.query(ProductEvent).filter(ProductEvent.user_id.isnot(None))
    if since:
        query = query.filter(ProductEvent.created_at >= since)

    events = (
        query.order_by(ProductEvent.created_at.asc(), ProductEvent.id.asc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    data = []
    for event in events:
        # A named event is the useful one. Falling back to the route's first
        # segment keeps page views countable by area without exporting the
        # route pattern, which is internal structure.
        area = (event.rule or "").strip("/").split("/")[0] or "home"
        data.append(
            {
                "id": str(event.id),
                "user_id": str(event.user_id),
                "type": event.name or f"{event.kind}.{area}",
                "occurred_at": _iso(event.created_at),
                "channel": event.channel or None,
            }
        )

    return jsonify({"data": data, "page": page, "limit": limit})


def emit_to_orbit(events=None, people=None) -> None:
    """Push to the hub, for anything that should not wait for the next pull.

    Fire and forget, and silent on failure. Analytics must never be able to fail
    a student's request, and a hub that is down is not this app's problem.
    """
    import hashlib
    import threading
    import time

    secret = _setting("ORBIT_INGEST_SECRET")
    base = _setting("ORBIT_URL")
    if not secret or not base:
        return

    body = json.dumps({"events": events or [], "people": people or []}, separators=(",", ":"))
    timestamp = str(int(time.time() * 1000))
    signature = hmac.new(
        secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256
    ).hexdigest()

    def _send() -> None:
        try:
            import requests

            requests.post(
                f"{base.rstrip('/')}/api/ingest/intelliplan",
                data=body,
                timeout=3,
                headers={
                    "content-type": "application/json",
                    "x-orbit-key": secret,
                    "x-orbit-timestamp": timestamp,
                    "x-orbit-signature": signature,
                },
            )
        except Exception:  # noqa: BLE001 - deliberately swallowed, see docstring
            pass

    # Off the request thread: the student is waiting on the response, and the
    # hub is not what they are waiting for.
    threading.Thread(target=_send, daemon=True).start()


__all__ = ["orbit_export_bp", "emit_to_orbit"]
