"""Authenticated two-device video pairing. Media never traverses this API."""
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import hmac
import secrets

from flask import Blueprint, jsonify, render_template, request

import secret_box
from time_utils import utcnow

SETUP_SECONDS = 120
LEASE_SECONDS = 30
MAX_SECONDS = 2 * 60 * 60
MAX_SDP = 48 * 1024


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class CameraPairDeps:
    db: object
    model: type
    user_model: type
    current_user_id: object
    allowed: object
    clock: object = utcnow


def create_study_camera_blueprint(deps):
    bp = Blueprint('study_camera', __name__)

    @bp.after_request
    def private(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    def body():
        if request.content_length and request.content_length > MAX_SDP + 2048:
            return None
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else None

    def guard():
        uid = deps.current_user_id()
        if uid is None:
            return None, (jsonify(error='Sign in on both devices to connect a camera.'), 401)
        # Reload the owner before resolving school and parental restrictions.
        user = deps.user_model.query.populate_existing().filter_by(id=uid).first()
        if user is None or not deps.allowed():
            deps.model.query.filter_by(user_id=uid).delete()
            deps.db.session.commit()
            return None, (jsonify(error='Camera sharing is unavailable under your current settings.'), 403)
        return uid, None

    def pair(uid, role):
        row = deps.model.query.filter_by(id=request.view_args['pair_id'], user_id=uid).with_for_update().first()
        _, denied = guard()
        if denied:
            return None, denied
        now = deps.clock()
        key = request.headers.get('X-Camera-Key', '')
        expected = getattr(row, role + '_hash', None) if row else None
        if not expected or len(key) > 128 or not hmac.compare_digest(expected, _digest(key)):
            return None, (jsonify(error='This camera connection is unavailable.'), 404)
        if (row.expires_at <= now or role == 'invite' and row.created_at + timedelta(seconds=SETUP_SECONDS) <= now
                or row.receiver_seen_at < now - timedelta(seconds=LEASE_SECONDS)
                or row.connected and row.sender_seen_at is not None
                and row.sender_seen_at < now - timedelta(seconds=LEASE_SECONDS)):
            deps.db.session.delete(row)
            deps.db.session.commit()
            return None, (jsonify(error='This camera connection expired. Start a new connection.'), 410)
        return row, None

    @bp.get('/study-camera')
    def phone_page():
        # The fragment is read only by the standalone first-party script.
        # No analytics, chat widgets, service worker, or third-party scripts.
        return render_template('study_camera_phone.html')

    @bp.post('/api/active/camera-pairs')
    def create():
        uid, denied = guard()
        if denied:
            return denied
        data = body()
        if not data or data.get('share_video') is not True:
            return jsonify(error='Confirm sharing video with your study browser first.'), 400
        if not secret_box.is_enabled():
            return jsonify(error='Secure camera pairing is not configured yet.'), 503
        # A user-row lock serializes replacement across workers; the unique
        # owner index is the independent storage invariant.
        deps.user_model.query.filter_by(id=uid).with_for_update().first()
        _, denied = guard()
        if denied:
            return denied
        deps.model.query.filter_by(user_id=uid).delete()
        receiver, invite = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = deps.clock()
        row = deps.model(id=secrets.token_hex(16), user_id=uid,
                         receiver_hash=_digest(receiver), invite_hash=_digest(invite),
                         created_at=now, receiver_seen_at=now,
                         expires_at=now + timedelta(seconds=SETUP_SECONDS))
        deps.db.session.add(row)
        _, denied = guard()
        if denied:
            return denied
        deps.db.session.commit()
        return jsonify(id=row.id, receiver_key=receiver, invite_key=invite,
                       setup_seconds=SETUP_SECONDS), 201

    @bp.post('/api/active/camera-pairs/<pair_id>/claim')
    def claim(pair_id):
        uid, denied = guard()
        if denied:
            return denied
        row, denied = pair(uid, 'invite')
        if denied:
            return denied
        data = body()
        if not data or data.get('share_video') is not True:
            return jsonify(error='Confirm sharing your camera with your study browser.'), 400
        sender = secrets.token_urlsafe(32)
        _, denied = guard()
        if denied:
            return denied
        changed = deps.model.query.filter_by(id=row.id, invite_hash=row.invite_hash).update(
            {'invite_hash': None, 'sender_hash': _digest(sender), 'sender_seen_at': deps.clock()})
        deps.db.session.commit()
        if changed != 1:
            return jsonify(error='This invitation has already been used.'), 409
        return jsonify(sender_key=sender)

    @bp.post('/api/active/camera-pairs/<pair_id>/sync')
    def sync(pair_id):
        uid, denied = guard()
        if denied:
            return denied
        data = body()
        if data is None or data.get('role') not in ('receiver', 'sender'):
            return jsonify(error='Invalid camera connection request.'), 400
        role = data['role']
        row, denied = pair(uid, role)
        if denied:
            return denied
        now = deps.clock()
        if role == 'receiver':
            row.receiver_seen_at = now
        else:
            row.sender_seen_at = now
        description = data.get('description')
        if description is not None:
            expected = 'offer' if role == 'receiver' else 'answer'
            sdp = description.get('sdp') if isinstance(description, dict) else None
            if (not isinstance(sdp, str) or len(sdp.encode()) > MAX_SDP
                    or description.get('type') != expected or not sdp.startswith('v=0')
                    or 'm=video ' not in sdp or 'm=audio ' in sdp or 'm=application ' in sdp):
                deps.db.session.rollback()
                return jsonify(error='Only a video camera connection is supported.'), 400
            if not secret_box.is_enabled():
                deps.db.session.rollback()
                return jsonify(error='Secure camera pairing is unavailable.'), 503
            old = getattr(row, expected)
            encrypted = secret_box.encrypt(sdp)
            if not secret_box.is_encrypted(encrypted):
                deps.db.session.rollback()
                return jsonify(error='Secure camera pairing is unavailable.'), 503
            # Idempotent retries may repeat the same SDP; no renegotiation
            # or silent replacement of an already selected sender is allowed.
            if row.connected or old and secret_box.decrypt(old) != sdp:
                deps.db.session.rollback()
                return jsonify(error='Start a new connection to change cameras.'), 409
            setattr(row, expected, encrypted)
        if data.get('connected') is True:
            if not row.connected and not (row.sender_hash and row.offer and row.answer):
                deps.db.session.rollback()
                return jsonify(error='Finish connecting both devices first.'), 409
            row.connected = True
            row.expires_at = row.created_at + timedelta(seconds=MAX_SECONDS)
            row.offer = row.answer = None
        remote = row.answer if role == 'receiver' else row.offer
        result = {'claimed': row.sender_hash is not None, 'connected': row.connected}
        if remote:
            text = secret_box.decrypt(remote)
            if text and not secret_box.is_encrypted(text):
                result['description'] = {'type': 'answer' if role == 'receiver' else 'offer', 'sdp': text}
        _, denied = guard()
        if denied:
            return denied
        deps.db.session.commit()
        return jsonify(result)

    @bp.post('/api/active/camera-pairs/<pair_id>/stop')
    def stop(pair_id):
        # Revocation remains usable after policy changes or expiry.
        uid = deps.current_user_id()
        data = body() or {}
        if uid is None or data.get('role') not in ('receiver', 'sender'):
            return jsonify(error='This camera connection is unavailable.'), 404
        row = deps.model.query.filter_by(id=pair_id, user_id=uid).with_for_update().first()
        expected = getattr(row, data['role'] + '_hash', None) if row else None
        key = request.headers.get('X-Camera-Key', '')
        if not expected or len(key) > 128 or not hmac.compare_digest(expected, _digest(key)):
            return jsonify(error='This camera connection is unavailable.'), 404
        deps.db.session.delete(row)
        deps.db.session.commit()
        return jsonify(stopped=True)

    return bp
