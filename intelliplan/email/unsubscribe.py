"""Signed, logged-out native unsubscribe handler shared by app factories."""
from flask import current_app, jsonify, request
from markupsafe import escape

from intelliplan.email.eligibility import looks_like_email
from intelliplan.email.sender import parse_unsubscribe_token
from intelliplan.persistence.mongo_email import MongoEmailState


def native_unsubscribe(token, page):
    payload = parse_unsubscribe_token(token or '')
    address = payload.get('email') if isinstance(payload, dict) else None
    if not isinstance(address, str) or not looks_like_email(address):
        return page('Link not recognised', '<h1>We could not read that link</h1>'
                    '<p>Please use the complete unsubscribe link in your email.</p>'), 400
    address = address.strip().lower()
    try:
        saved = MongoEmailState(current_app.extensions['mongo_store']).unsubscribe(address)
    except Exception:
        saved = False
    if not saved:
        return page('Please try again', '<h1>We could not save your request</h1>'
                    '<p>Please try this link again shortly.</p>'), 503
    if request.method == 'POST':
        return jsonify(status='ok', unsubscribed=True)
    return page('Unsubscribed', '<h1>You are unsubscribed</h1>'
        f'<p>We will not send marketing email to <strong>{escape(address)}</strong> again.</p>'
        '<p>You can manage your other notification preferences in '
        '<a href="/settings">Settings</a>.</p>')
