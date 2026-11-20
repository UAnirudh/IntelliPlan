"""Publish events the signed-in user has reviewed."""
import json
import logging

from flask import g
from flask_login import current_user

logger = logging.getLogger(__name__)


def publish_google(events, user_id):
    from App import _calendar_policy_allowed, get_google_token
    from google_calendar_helper import add_reviewed_events
    from intelliplan.persistence.google import google_accounts

    if not current_user.is_authenticated or current_user.id != user_id:
        return {'message': 'Sign in to export events.'}, 401
    if not _calendar_policy_allowed(user_id):
        return {'message': 'Calendar export is unavailable for this workspace.'}, 403
    try:
        token = get_google_token(user_id=user_id)
        if not token:
            return {'message': 'Connect Google Calendar in Settings first.'}, 409
        created, skipped, refreshed = add_reviewed_events(token, events)
        proof = getattr(g, '_google_grant', None)
        if proof and refreshed and refreshed != token.get('token'):
            google_accounts().refresh(user_id, proof[1], json.dumps({**token, 'token': refreshed}))
        return {'created': created, 'skipped': skipped}, 200
    except Exception as exc:
        logger.warning('Reviewed calendar write failed (%s)', type(exc).__name__)
        return {'message': 'Google Calendar could not finish. Retry to add any remaining events.'}, 502
