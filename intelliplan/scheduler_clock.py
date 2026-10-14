"""One student-local clock for a complete planning request."""
from datetime import datetime

from flask import g, has_request_context, request
from flask_login import current_user

from time_utils import to_local, utcnow, valid_timezone


def timezone_for(user_id=None):
    zone, offset = '', 0
    own_request = has_request_context() and (
        user_id is None or current_user.is_authenticated and current_user.id == user_id)
    if own_request:
        body = request.get_json(silent=True) if request.is_json else {}
        sent = request.headers.get('X-Timezone') or request.args.get('tz') or (
            body.get('timezone') if isinstance(body, dict) else None)
        zone = valid_timezone(sent) or ''
    if user_id is not None:
        from App import User, UserStreak
        user = User.query.populate_existing().filter_by(id=user_id).first()
        row = UserStreak.query.populate_existing().filter_by(user_id=user_id).first()
        zone = zone or (valid_timezone(row.timezone) if row else '') or ''
        try:
            offset = max(-720, min(840, int(user.utc_offset_minutes or 0))) if user else 0
        except (TypeError, ValueError):
            offset = 0
    return zone, offset


def now_for(user_id=None, *, utc: datetime | None = None):
    if utc is None and has_request_context():
        if not hasattr(g, '_scheduler_utc_now'):
            g._scheduler_utc_now = utcnow()
        utc = g._scheduler_utc_now
    zone, offset = timezone_for(user_id)
    return to_local(utc if utc is not None else utcnow(), zone, offset)
