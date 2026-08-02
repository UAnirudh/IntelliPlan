"""Voice rooms inside Study Groups.

Groups already had a Jitsi link. A link is not a feature: you cannot see
whether anyone is in there without leaving the page and joining, so
nobody is ever first and the room stays empty. What was missing is
presence — who is in voice right now, and who is muted.

How presence is decided:

  join      seats you, or refreshes the seat you already had
  heartbeat says "still here", every HEARTBEAT_SECONDS
  leave     removes the seat
  roster    everyone whose last heartbeat is inside VOICE_STALE_SECONDS

Leave is best-effort and always will be — a closed laptop sends nothing.
So no read trusts it: the roster is computed from `last_seen_at` on every
request, and a seat that stopped reporting is already gone from the
answer before any cleanup runs. Cleanup then deletes those rows so the
table does not grow, but correctness never depended on it having run.

The audio itself is Jitsi, embedded audio-only in the page rather than
opened in a tab, so a student can stay on the shared notes while talking.
We deliberately do not proxy or record any audio: it never touches our
servers, and there is nothing here that could store it.

Endpoints:
  GET  /api/groups/<group_id>/voice            roster + room, without joining
  POST /api/groups/<group_id>/voice/join
  POST /api/groups/<group_id>/voice/heartbeat  body: {muted?: bool}
  POST /api/groups/<group_id>/voice/mute       body: {muted: bool}
  POST /api/groups/<group_id>/voice/leave
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

from time_utils import utcnow

bp = Blueprint("group_voice_bp", __name__)

# How often the client says "still here".
HEARTBEAT_SECONDS = 12
# How long silence is tolerated before a seat stops counting. Generous
# relative to the heartbeat because a phone that backgrounds the tab
# throttles timers hard, and dropping someone mid-sentence because their
# screen locked is worse than a roster that is a few seconds stale.
VOICE_STALE_SECONDS = 45

MAX_SEATS = 25


def _db():
    return current_app.intelliplan_db  # type: ignore[attr-defined]


def _models():
    a = current_app
    return (
        a.intelliplan_study_group_model,        # type: ignore[attr-defined]
        a.intelliplan_study_group_member_model,  # type: ignore[attr-defined]
        a.intelliplan_voice_seat_model,          # type: ignore[attr-defined]
    )


def _fail(message, code=400):
    return jsonify({"status": "error", "message": message}), code


def _member_or_none(group_id):
    _, Member, _ = _models()
    return Member.query.filter_by(group_id=group_id, user_id=current_user.id).first()


def _cutoff():
    return utcnow() - timedelta(seconds=VOICE_STALE_SECONDS)


def _live_seats(group_id):
    """Seats that have reported in recently. This is the only definition of
    "in the room" anywhere in this module."""
    _, _, Seat = _models()
    return (Seat.query
            .filter(Seat.group_id == group_id, Seat.last_seen_at >= _cutoff())
            .order_by(Seat.joined_at.asc())
            .all())


def _sweep(group_id):
    """Delete seats that went quiet. Purely housekeeping — `_live_seats`
    already excludes them, so a failure here shows up as unused rows, not
    as a wrong roster."""
    _, _, Seat = _models()
    db = _db()
    try:
        (Seat.query
         .filter(Seat.group_id == group_id, Seat.last_seen_at < _cutoff())
         .delete(synchronize_session=False))
        db.session.commit()
    except Exception:
        db.session.rollback()


def _room_url(group):
    """The group's Jitsi room, created on first use.

    Rooms on meet.jit.si are public to anyone who knows the name, so the
    name has to be unguessable — a slug derived from the group id or its
    title would let anyone walk in. 12 bytes of token_urlsafe is not
    walkable.
    """
    db = _db()
    if not group.meeting_url:
        slug = secrets.token_urlsafe(12).replace("-", "").replace("_", "")
        group.meeting_url = f"https://meet.jit.si/intelliplan-{slug}"
        db.session.commit()
    return group.meeting_url


def _embed_url(room_url):
    """Audio-only embed.

    Video is disabled rather than merely muted: this is a voice room, and
    a student who joins from a bedroom should not have a camera one
    mis-click away. Everyone arrives muted so a joiner never broadcasts
    into a room by accident.
    """
    return (
        f"{room_url}#config.prejoinPageEnabled=false"
        "&config.startWithAudioMuted=true"
        "&config.startWithVideoMuted=true"
        "&config.disable1On1Mode=true"
        "&config.startAudioOnly=true"
        "&interfaceConfig.TOOLBAR_BUTTONS=%5B%22microphone%22%2C%22hangup%22%2C%22settings%22%5D"
    )


def _seat_dict(seat):
    return {
        "user_id": seat.user_id,
        "name": seat.display_name or "Member",
        "initial": (seat.display_name or "?").strip()[:1].upper() or "?",
        "muted": bool(seat.is_muted),
        "is_you": seat.user_id == current_user.id,
        "joined_at": seat.joined_at.isoformat() if seat.joined_at else None,
    }


def _roster_payload(group, seats, joined):
    return {
        "status": "ok",
        "room_url": _room_url(group) if joined else None,
        "embed_url": _embed_url(_room_url(group)) if joined else None,
        "joined": joined,
        "count": len(seats),
        "capacity": MAX_SEATS,
        "heartbeat_seconds": HEARTBEAT_SECONDS,
        "seats": [_seat_dict(s) for s in seats],
    }


@bp.route("/api/groups/<int:group_id>/voice", methods=["GET"])
@login_required
def voice_roster(group_id: int):
    """Who is in voice, for a member who has not joined yet.

    Deliberately does not return the room URL unless the caller is
    already seated — knowing the room name is enough to enter it, and the
    roster is something we want visible from the group page.
    """
    Group, _, Seat = _models()
    group = Group.query.get(group_id)
    if not group:
        return _fail("No such group.", 404)
    if not _member_or_none(group_id):
        return _fail("Join the group first.", 403)

    seats = _live_seats(group_id)
    mine = next((s for s in seats if s.user_id == current_user.id), None)
    return jsonify(_roster_payload(group, seats, joined=mine is not None))


@bp.route("/api/groups/<int:group_id>/voice/join", methods=["POST"])
@login_required
def voice_join(group_id: int):
    Group, _, Seat = _models()
    db = _db()

    group = Group.query.get(group_id)
    if not group:
        return _fail("No such group.", 404)
    if not _member_or_none(group_id):
        return _fail("Join the group first.", 403)

    _sweep(group_id)
    seats = _live_seats(group_id)
    mine = next((s for s in seats if s.user_id == current_user.id), None)

    if mine is None and len(seats) >= MAX_SEATS:
        return _fail(f"This room is full ({MAX_SEATS} people).", 409)

    name = (getattr(current_user, "name", None)
            or (current_user.email or "").split("@")[0]
            or "Member")[:120]

    if mine is not None:
        # Already seated — rejoining from a second tab must not create a
        # second seat, it just refreshes this one.
        mine.last_seen_at = utcnow()
        mine.display_name = name
    else:
        existing = Seat.query.filter_by(group_id=group_id, user_id=current_user.id).first()
        if existing is not None:
            # A stale seat the sweep has not removed yet. Reuse it rather
            # than inserting and tripping the uniqueness constraint.
            existing.last_seen_at = utcnow()
            existing.joined_at = utcnow()
            existing.display_name = name
            existing.is_muted = True
        else:
            db.session.add(Seat(
                group_id=group_id,
                user_id=current_user.id,
                display_name=name,
                is_muted=True,
                joined_at=utcnow(),
                last_seen_at=utcnow(),
            ))
    try:
        db.session.commit()
    except Exception:
        # Two tabs racing the same join both pass the check above and one
        # loses on the unique constraint. That is not an error worth
        # showing anyone: the seat exists either way.
        db.session.rollback()

    return jsonify(_roster_payload(group, _live_seats(group_id), joined=True))


@bp.route("/api/groups/<int:group_id>/voice/heartbeat", methods=["POST"])
@login_required
def voice_heartbeat(group_id: int):
    """Refresh the seat and return the current roster in the same round
    trip — the client needs both, and asking twice doubles the traffic for
    a poll that runs every twelve seconds."""
    Group, _, Seat = _models()
    db = _db()

    group = Group.query.get(group_id)
    if not group:
        return _fail("No such group.", 404)

    seat = Seat.query.filter_by(group_id=group_id, user_id=current_user.id).first()
    if seat is None:
        # Dropped — either swept out or never joined. Say so rather than
        # silently reseating: the client should show the join button again.
        return jsonify(_roster_payload(group, _live_seats(group_id), joined=False))

    body = request.get_json(silent=True) or {}
    if "muted" in body:
        seat.is_muted = bool(body.get("muted"))
    seat.last_seen_at = utcnow()
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()

    _sweep(group_id)
    return jsonify(_roster_payload(group, _live_seats(group_id), joined=True))


@bp.route("/api/groups/<int:group_id>/voice/mute", methods=["POST"])
@login_required
def voice_mute(group_id: int):
    """Record what the student's own microphone is doing.

    This does not mute anyone — Jitsi owns the audio. It mirrors the state
    so the rest of the group's roster shows it, which is the only reason a
    server needs to know.
    """
    Group, _, Seat = _models()
    db = _db()

    group = Group.query.get(group_id)
    if not group:
        return _fail("No such group.", 404)

    seat = Seat.query.filter_by(group_id=group_id, user_id=current_user.id).first()
    if seat is None:
        return _fail("You are not in this voice room.", 409)

    body = request.get_json(silent=True) or {}
    seat.is_muted = bool(body.get("muted"))
    seat.last_seen_at = utcnow()
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
    return jsonify(_roster_payload(group, _live_seats(group_id), joined=True))


@bp.route("/api/groups/<int:group_id>/voice/leave", methods=["POST"])
@login_required
def voice_leave(group_id: int):
    Group, _, Seat = _models()
    db = _db()

    group = Group.query.get(group_id)
    if not group:
        return _fail("No such group.", 404)

    try:
        Seat.query.filter_by(group_id=group_id, user_id=current_user.id).delete(
            synchronize_session=False)
        db.session.commit()
    except Exception:
        db.session.rollback()

    return jsonify(_roster_payload(group, _live_seats(group_id), joined=False))
