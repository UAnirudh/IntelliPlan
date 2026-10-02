"""Study Buddies: invite up to five friends, share a streak, nudge each other.

Imported by App.py after model setup; every App import is lazy, matching
the other ``*_glue.py`` modules. The rules (who may join, the friend-streak
arithmetic, and the exact fields a buddy sees) live in
``intelliplan/services/buddies.py``; this module owns the tables and routes.

The flow
--------
1. A student turns Study Buddies on and copies their invite link,
   ``/buddies/join/<referral code>``. It is the same code the referral
   programme uses, so an invite that brings in a new student is also a
   referral.
2. The friend opens it and taps "Send buddy request". That is their opt-in.
3. The inviter confirms. That is theirs. Nothing is shared until both have
   said yes -- a leaked link can produce requests, never a buddy.

Either side can remove the other at any time, or block them, which also
stops a fresh request from the same person. Under-13 accounts, accounts
with no known age, and accounts waiting on parental consent cannot use any
of it (see ``buddies.eligibility``).

What is shared: studied-today, focus minutes today, the pair's streak.
Never grades, assignments, courses, or email addresses.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

from flask import Blueprint, jsonify, redirect, render_template, request, session
from flask_login import current_user

from intelliplan.services import buddies as rules
from time_utils import to_local, utcnow, zone_for

logger = logging.getLogger(__name__)

buddies_bp = Blueprint("buddies", __name__)

#: How long "nudged you" stays on the recipient's card.
NUDGE_VISIBLE_HOURS = 18
#: History read when working out which days a student studied. Matches the
#: 30 days the streak engine keeps, plus a margin.
HISTORY_DAYS = 35

_REASON_TEXT = {
    "under_13": "Study Buddies is not available for students under 13.",
    "unknown_age": "Add your birth year to use Study Buddies.",
    "awaiting_parent_consent": "Study Buddies unlocks once your parent approves your account.",
    "students_only": "Study Buddies is for student accounts.",
    "no_user": "Sign in to use Study Buddies.",
}


# ── small helpers ─────────────────────────────────────────────────────


def _err(message: str, status: int = 400, **extra: Any):
    return jsonify({"status": "error", "message": message, **extra}), status


def _me() -> Any | None:
    try:
        if current_user.is_authenticated:
            return current_user._get_current_object()
    except Exception:
        pass
    return None


def _eligible(user: Any) -> tuple[bool, str]:
    return rules.eligibility(user, utcnow())


def _sharing(user: Any) -> bool:
    """Opted in *and* still eligible. Age facts can change after opting in."""
    return bool(getattr(user, "buddies_opt_in", False)) and _eligible(user)[0]


def _tz(user: Any) -> tuple[str, int]:
    from App import UserStreak

    try:
        row = UserStreak.query.filter_by(user_id=user.id).first()
        if row is not None and row.timezone and zone_for(row.timezone) is not None:
            return row.timezone, 0
    except Exception:
        pass
    try:
        return "", int(getattr(user, "utc_offset_minutes", 0) or 0)
    except (TypeError, ValueError):
        return "", 0


def _local_today(user: Any, now: datetime) -> date:
    tz_name, offset = _tz(user)
    return to_local(now, tz_name, offset).date()


def _pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def _link_between(a: int, b: int) -> Any | None:
    from App import StudyBuddy

    low, high = _pair(int(a), int(b))
    return StudyBuddy.query.filter_by(user_low_id=low, user_high_id=high).first()


def _active_count(user_id: int) -> int:
    from App import StudyBuddy

    return StudyBuddy.query.filter(
        StudyBuddy.status == "active",
        (StudyBuddy.user_low_id == user_id) | (StudyBuddy.user_high_id == user_id),
    ).count()


def _pending_incoming_count(user_id: int) -> int:
    from App import StudyBuddy

    return StudyBuddy.query.filter(
        StudyBuddy.status == "pending",
        StudyBuddy.requested_by_id != user_id,
        (StudyBuddy.user_low_id == user_id) | (StudyBuddy.user_high_id == user_id),
    ).count()


def _own_link(link_id: int, me: Any) -> Any | None:
    """A link row, only if ``me`` is one of its two students."""
    from App import StudyBuddy, db

    row = db.session.get(StudyBuddy, int(link_id))
    if row is None or not row.involves(me.id):
        return None
    return row


# ── activity: the only data a buddy ever sees ─────────────────────────


def studied_days(user: Any, now: datetime) -> tuple[set[date], int]:
    """``(local dates studied, focus minutes today)`` for one student.

    A day counts if the streak engine recorded a qualifying action on it, or
    the student logged focus time in Active. Both are already the student's
    own local dates; nothing about *what* they studied is read.
    """
    from App import ActiveSession, UserStreak

    tz_name, offset = _tz(user)
    today = to_local(now, tz_name, offset).date()
    days: set[date] = set()
    try:
        row = UserStreak.query.filter_by(user_id=user.id).first()
        for raw in json.loads((row.qualified_dates_json if row else None) or "[]"):
            try:
                days.add(date.fromisoformat(str(raw)[:10]))
            except ValueError:
                continue
    except Exception:
        pass

    minutes_today = 0
    try:
        since = now - timedelta(days=HISTORY_DAYS)
        sessions = ActiveSession.query.filter(
            ActiveSession.user_id == user.id,
            ActiveSession.started_at >= since,
        ).with_entities(ActiveSession.started_at, ActiveSession.active_seconds).all()
        per_day: dict[date, int] = {}
        for started_at, seconds in sessions:
            if started_at is None:
                continue
            d = to_local(started_at, tz_name, offset).date()
            per_day[d] = per_day.get(d, 0) + int(seconds or 0)
        for d, seconds in per_day.items():
            if seconds >= rules.MIN_FOCUS_MINUTES * 60:
                days.add(d)
        minutes_today = per_day.get(today, 0) // 60
    except Exception as exc:
        logger.warning("buddies: session history failed: %s", exc)
    return days, int(minutes_today)


def refresh_streak(link: Any, me: Any, other: Any, now: datetime,
                   mine: set[date], theirs: set[date]) -> rules.PairStreak:
    """Fold new joint days into the stored pair streak, persisting changes."""
    today = min(_local_today(me, now), _local_today(other, now))
    last = None
    if link.streak_last_date:
        try:
            last = date.fromisoformat(link.streak_last_date)
        except ValueError:
            last = None
    streak = rules.advance_pair_streak(
        count=link.streak_count or 0,
        longest=link.streak_longest or 0,
        last_joint=last,
        days_a=mine,
        days_b=theirs,
        today=today,
    )
    new_last = streak.last_joint.isoformat() if streak.last_joint else ""
    if (streak.count, streak.longest, new_last) != (link.streak_count, link.streak_longest, link.streak_last_date or ""):
        link.streak_count = streak.count
        link.streak_longest = streak.longest
        link.streak_last_date = new_last
    return streak


def _nudges_sent_today(sender_id: int, local_date: str, recipient_id: int | None = None) -> int:
    from App import BuddyNudge

    q = BuddyNudge.query.filter_by(sender_id=sender_id, local_date=local_date)
    if recipient_id is not None:
        q = q.filter_by(recipient_id=recipient_id)
    return q.count()


def _invite_url(user: Any) -> str | None:
    from App import APP_BASE_URL, _ensure_referral_code

    try:
        code = _ensure_referral_code(user)
    except Exception:
        code = None
    if not code:
        return None
    base = (APP_BASE_URL or request.host_url or "").rstrip("/")
    return f"{base}/buddies/join/{code}"


def overview(me: Any, now: datetime | None = None) -> dict[str, Any]:
    """The whole /api/buddies payload for ``me``."""
    from App import BuddyNudge, StudyBuddy, User, db

    now = now or utcnow()
    eligible, reason = _eligible(me)
    out: dict[str, Any] = {
        "status": "ok",
        "eligible": eligible,
        "reason": reason,
        "reason_text": _REASON_TEXT.get(reason, ""),
        "enabled": bool(getattr(me, "buddies_opt_in", False)) and eligible,
        "max_buddies": rules.MAX_BUDDIES,
        "buddies": [],
        "incoming": [],
        "outgoing": [],
        "blocked": [],
        "pending_invite": None,
    }
    code = session.get("pending_buddy_code")
    if code:
        inviter = User.query.filter_by(referral_code=str(code)[:16]).first()
        if inviter is not None and inviter.id != me.id and _sharing(inviter):
            out["pending_invite"] = {"code": inviter.referral_code, "name": rules.display_name(inviter)}
    if not out["enabled"]:
        return out

    out["invite_url"] = _invite_url(me)
    my_today = _local_today(me, now).isoformat()
    mine, my_minutes = studied_days(me, now)
    out["me"] = {"studied_today": _local_today(me, now) in mine, "focus_minutes_today": my_minutes}
    sent_today = _nudges_sent_today(me.id, my_today)
    out["nudges_left_today"] = max(0, rules.NUDGES_PER_DAY - sent_today)
    recent_from = {
        n.sender_id for n in BuddyNudge.query.filter(
            BuddyNudge.recipient_id == me.id,
            BuddyNudge.created_at >= now - timedelta(hours=NUDGE_VISIBLE_HOURS),
        ).all()
    }

    links = StudyBuddy.query.filter(
        (StudyBuddy.user_low_id == me.id) | (StudyBuddy.user_high_id == me.id)
    ).order_by(StudyBuddy.created_at.asc()).all()
    dirty = False
    for link in links:
        other = db.session.get(User, link.other(me.id))
        if other is None:
            continue
        if link.status == "blocked":
            # Only the student who blocked sees the row, so they can undo it.
            if link.blocked_by_id == me.id:
                out["blocked"].append({"id": link.id, "name": rules.display_name(other)})
            continue
        if link.status == "pending":
            entry = {"id": link.id, "name": rules.display_name(other)}
            (out["outgoing"] if link.requested_by_id == me.id else out["incoming"]).append(entry)
            continue
        sharing = _sharing(other)
        theirs, their_minutes = (studied_days(other, now) if sharing else (set(), 0))
        before = (link.streak_count, link.streak_longest, link.streak_last_date)
        streak = refresh_streak(link, me, other, now, mine, theirs) if sharing else None
        if streak is not None and before != (link.streak_count, link.streak_longest, link.streak_last_date):
            dirty = True
        studied_today = _local_today(other, now) in theirs
        can_nudge = (
            sharing
            and not studied_today
            and sent_today < rules.NUDGES_PER_DAY
            and _nudges_sent_today(me.id, my_today, other.id) < rules.NUDGES_PER_BUDDY_PER_DAY
        )
        out["buddies"].append(rules.public_card(
            link_id=link.id,
            name=rules.display_name(other),
            sharing=sharing,
            studied_today=studied_today,
            focus_minutes_today=their_minutes,
            streak=streak,
            can_nudge=can_nudge,
            nudged_you_today=other.id in recent_from,
        ))
    if dirty:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
    return out


# ── pages ─────────────────────────────────────────────────────────────


@buddies_bp.route("/buddies")
def buddies_page():
    if not current_user.is_authenticated:
        return redirect("/login?next=/buddies")
    invite = (request.args.get("invite") or "").strip().lower()[:16]
    if invite:
        session["pending_buddy_code"] = invite
        session.modified = True
    return render_template("buddies.html", active_page="buddies")


@buddies_bp.route("/buddies/join/<code>")
def buddies_join(code):
    """Invite landing. Reuses the referral code, so it also attributes a
    signup the way ``/ref/<code>`` does."""
    from App import User

    code = (code or "").strip().lower()[:16]
    inviter = User.query.filter_by(referral_code=code).first() if code else None
    if inviter is not None:
        session["pending_buddy_code"] = code
        if not current_user.is_authenticated and not session.get("pending_referral"):
            session["pending_referral"] = inviter.id
        session.modified = True
    if current_user.is_authenticated:
        return redirect("/buddies")
    return redirect("/register?next=/buddies")


# ── API ───────────────────────────────────────────────────────────────


@buddies_bp.route("/api/buddies", methods=["GET"])
def api_buddies():
    me = _me()
    if me is None:
        return _err("Sign in to use Study Buddies.", 401)
    return jsonify(overview(me))


@buddies_bp.route("/api/buddies/settings", methods=["POST"])
def api_buddies_settings():
    from App import db

    me = _me()
    if me is None:
        return _err("Sign in to use Study Buddies.", 401)
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled"))
    if enabled:
        ok, reason = _eligible(me)
        if not ok:
            return _err(_REASON_TEXT.get(reason, "Study Buddies is not available."), 403, reason=reason)
    me.buddies_opt_in = enabled
    db.session.commit()
    return jsonify(overview(me))


@buddies_bp.route("/api/buddies/request", methods=["POST"])
def api_buddies_request():
    """Ask the owner of an invite code to be study buddies.

    Sending the request is the requester's opt-in, so it switches Study
    Buddies on for them (the button says so). The inviter still confirms.
    """
    from App import StudyBuddy, User, db

    me = _me()
    if me is None:
        return _err("Sign in to use Study Buddies.", 401)
    ok, reason = _eligible(me)
    if not ok:
        return _err(_REASON_TEXT.get(reason, "Study Buddies is not available."), 403, reason=reason)
    body = request.get_json(silent=True) or {}
    code = str(body.get("code") or session.get("pending_buddy_code") or "").strip().lower()[:16]
    inviter = User.query.filter_by(referral_code=code).first() if code else None
    # One message for every reason the other side cannot be reached, so the
    # response does not tell a stranger whether someone is under 13, has the
    # feature off, or blocked them.
    unavailable = _err("This invite is not available.", 404)
    if inviter is None or inviter.id == me.id or not _sharing(inviter):
        return unavailable
    existing = _link_between(me.id, inviter.id)
    if existing is not None:
        if existing.status == "blocked":
            return unavailable
        return jsonify({"status": "ok", "state": existing.status, **overview(me)})
    if _active_count(me.id) >= rules.MAX_BUDDIES:
        return _err(f"You already have {rules.MAX_BUDDIES} study buddies.", 409)
    if _active_count(inviter.id) >= rules.MAX_BUDDIES or _pending_incoming_count(inviter.id) >= rules.MAX_PENDING_INCOMING:
        return unavailable
    low, high = _pair(me.id, inviter.id)
    me.buddies_opt_in = True
    db.session.add(StudyBuddy(user_low_id=low, user_high_id=high, requested_by_id=me.id, status="pending"))
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()  # a double-tap racing the unique pair constraint
    session.pop("pending_buddy_code", None)
    return jsonify({"status": "ok", "state": "pending", **overview(me)})


@buddies_bp.route("/api/buddies/<int:link_id>/confirm", methods=["POST"])
def api_buddies_confirm(link_id: int):
    from App import User, db

    me = _me()
    if me is None:
        return _err("Sign in to use Study Buddies.", 401)
    link = _own_link(link_id, me)
    if link is None or link.status != "pending" or link.requested_by_id == me.id:
        return _err("That request is not available.", 404)
    ok, reason = _eligible(me)
    if not ok:
        return _err(_REASON_TEXT.get(reason, "Study Buddies is not available."), 403, reason=reason)
    other = db.session.get(User, link.other(me.id))
    if other is None or not _sharing(other):
        return _err("That request is not available.", 404)
    if _active_count(me.id) >= rules.MAX_BUDDIES:
        return _err(f"You already have {rules.MAX_BUDDIES} study buddies. Remove one first.", 409)
    if _active_count(other.id) >= rules.MAX_BUDDIES:
        return _err("They already have the maximum number of buddies.", 409)
    me.buddies_opt_in = True
    link.status = "active"
    link.accepted_at = utcnow()
    db.session.commit()
    return jsonify(overview(me))


@buddies_bp.route("/api/buddies/<int:link_id>/remove", methods=["POST"])
def api_buddies_remove(link_id: int):
    """Remove a buddy, decline or cancel a request, or undo a block."""
    from App import BuddyNudge, db

    me = _me()
    if me is None:
        return _err("Sign in to use Study Buddies.", 401)
    link = _own_link(link_id, me)
    # The student who was blocked cannot lift the block by removing the row.
    if link is None or (link.status == "blocked" and link.blocked_by_id != me.id):
        return _err("Not found.", 404)
    other_id = link.other(me.id)
    BuddyNudge.query.filter(
        ((BuddyNudge.sender_id == me.id) & (BuddyNudge.recipient_id == other_id))
        | ((BuddyNudge.sender_id == other_id) & (BuddyNudge.recipient_id == me.id))
    ).delete(synchronize_session=False)
    db.session.delete(link)
    db.session.commit()
    return jsonify(overview(me))


@buddies_bp.route("/api/buddies/<int:link_id>/block", methods=["POST"])
def api_buddies_block(link_id: int):
    """Stop sharing with this person and refuse any future request from them."""
    from App import db

    me = _me()
    if me is None:
        return _err("Sign in to use Study Buddies.", 401)
    link = _own_link(link_id, me)
    if link is None or (link.status == "blocked" and link.blocked_by_id != me.id):
        return _err("Not found.", 404)
    link.status = "blocked"
    link.blocked_by_id = me.id
    link.streak_count = 0
    db.session.commit()
    return jsonify(overview(me))


@buddies_bp.route("/api/buddies/<int:link_id>/nudge", methods=["POST"])
def api_buddies_nudge(link_id: int):
    """Nudge a buddy who has not studied yet today.

    Limited to one per buddy per day and ``NUDGES_PER_DAY`` in total, by the
    sender's local date. Delivered through the existing notification outbox,
    so the recipient's own channel choices and quiet hours apply.
    """
    from App import BuddyNudge, User, db

    me = _me()
    if me is None:
        return _err("Sign in to use Study Buddies.", 401)
    if not _sharing(me):
        return _err("Turn on Study Buddies first.", 403)
    link = _own_link(link_id, me)
    if link is None or link.status != "active":
        return _err("Not found.", 404)
    other = db.session.get(User, link.other(me.id))
    if other is None or not _sharing(other):
        return _err("They are not sharing right now.", 409)

    now = utcnow()
    local_date = _local_today(me, now).isoformat()
    if _nudges_sent_today(me.id, local_date, other.id) >= rules.NUDGES_PER_BUDDY_PER_DAY:
        return _err("You already nudged them today.", 429)
    if _nudges_sent_today(me.id, local_date) >= rules.NUDGES_PER_DAY:
        return _err("That's all the nudges for today.", 429)

    db.session.add(BuddyNudge(sender_id=me.id, recipient_id=other.id, local_date=local_date))
    db.session.commit()

    queued = 0
    try:
        from intelliplan.notifications.events import EventKind, NotificationEvent
        from notifications_glue import _preferences_for, get_dispatcher

        event = NotificationEvent(
            kind=EventKind.BUDDY_NUDGE,
            user_id=other.id,
            dedupe_key=f"{me.id}:{other.id}:{local_date}",
            context={"name": rules.display_name(me), "streak": int(link.streak_count or 0)},
            url="/buddies",
        )
        queued = len(get_dispatcher().enqueue(event, _preferences_for(other)))
    except Exception as exc:
        # The nudge still shows on their buddies card; a provider problem
        # must not turn a sent nudge into an error.
        logger.warning("buddy nudge notification failed: %s", exc)
    return jsonify({"status": "ok", "queued": queued, **overview(me, now)})


def install(app: Any) -> None:
    app.register_blueprint(buddies_bp)
