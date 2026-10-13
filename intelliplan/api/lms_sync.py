"""LMS / SIS connection management API.

Endpoints:
  GET  /api/lms/providers      — list available providers + configured flag
  GET  /api/lms/status         — which providers the user is connected to
  POST /api/lms/<key>/sync     — pull assignments now (returns count)
  POST /api/lms/<key>/disconnect — remove stored tokens

OAuth callback handling for each provider lives in App.py routes (one per
provider). This blueprint owns the read/write API surface around the
LMSToken model so the settings UI has one shape to talk to.
"""

from __future__ import annotations

import json
import logging
from time_utils import utcnow

from flask import Blueprint, current_app, jsonify
from flask_login import current_user, login_required

from intelliplan.integrations.lms import (
    available_providers,
    get_provider,
)
from intelliplan.learning import policy

logger = logging.getLogger(__name__)
bp = Blueprint("lms_sync_bp", __name__)


def _lms_token_model():
    """Lazy resolver — the model is defined on App.py and attached to the
    Flask app object."""
    return current_app.intelliplan_lms_token_model  # type: ignore[attr-defined]


def _db():
    return current_app.intelliplan_db  # type: ignore[attr-defined]


def _get_token_row(user_id: int, provider_key: str):
    LMSToken = _lms_token_model()
    return LMSToken.query.filter_by(user_id=user_id, provider=provider_key).first()


@bp.route("/api/lms/providers", methods=["GET"])
def api_providers():
    """Public — used by the marketing page too."""
    return jsonify({"providers": available_providers()})


@bp.route("/api/lms/status", methods=["GET"])
@login_required
def api_status():
    LMSToken = _lms_token_model()
    rows = LMSToken.query.filter_by(user_id=current_user.id).all()
    connected = {
        r.provider: {
            "connected": True,
            "last_synced_at": r.last_synced_at.isoformat() if r.last_synced_at else None,
            "last_sync_count": r.last_sync_count or 0,
        }
        for r in rows
    }
    status = []
    for p in available_providers():
        c = connected.get(p["key"], {"connected": False})
        status.append({**p, **c})
    return jsonify({"providers": status})


@bp.route("/api/lms/<key>/sync", methods=["POST"])
@login_required
def api_sync(key: str):
    provider = get_provider(key)
    if provider is None:
        return jsonify({"error": "unknown_provider"}), 404
    capability = policy.source_capability(key)
    def denied():
        policy.invalidate_cache()
        allowed, reason = policy.decision(current_user, capability) if capability else (False, 'unknown_source')
        if not allowed:
            _db().session.rollback()
            return jsonify(error=reason, feature=capability), 403
        return None
    denial = denied()
    if denial:
        return denial
    row = _get_token_row(current_user.id, key)
    if row is None:
        return jsonify({"error": "not_connected"}), 409

    try:
        tokens = json.loads(row.tokens_json or "{}")
    except (json.JSONDecodeError, TypeError):
        tokens = {}

    # Refresh first if the provider supports it.
    try:
        new_tokens = provider.refresh_tokens(tokens=tokens)
        if new_tokens != tokens:
            tokens = new_tokens
    except Exception as exc:
        logger.warning("token refresh failed for %s/%s: %s", current_user.id, key, exc)

    denial = denied()
    if denial:
        return denial

    try:
        assignments = provider.sync_all(tokens=tokens)
    except Exception as exc:
        logger.exception("sync failed for %s/%s: %s", current_user.id, key, exc)
        return jsonify({"error": "sync_failed", "message": "The connection could not be synced. Try again later."}), 502

    denial = denied()
    if denial:
        return denial

    row.tokens_json = json.dumps(tokens)
    row.last_synced_at = utcnow()
    row.last_sync_count = len(assignments)
    _db().session.commit()
    return jsonify({"synced": len(assignments)})


@bp.route("/api/lms/<key>/disconnect", methods=["POST"])
@login_required
def api_disconnect(key: str):
    row = _get_token_row(current_user.id, key)
    if row is None:
        return jsonify({"ok": True})
    db = _db()
    db.session.delete(row)
    db.session.commit()
    return jsonify({"ok": True})
