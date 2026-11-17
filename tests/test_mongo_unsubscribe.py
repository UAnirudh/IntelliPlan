"""Logged-out unsubscribe links persist the suppression and visible setting."""
from flask import Flask
import mongomock
import pytest

from intelliplan.email.sender import make_unsubscribe_token
from intelliplan.email.unsubscribe import native_unsubscribe
from intelliplan.persistence.mongo import MongoStore
from intelliplan.persistence.mongo_email import MongoEmailState


@pytest.fixture
def environment():
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-unsubscribe-signing')
    client = mongomock.MongoClient()
    store = MongoStore('mongodb://test', 'unsubscribe', client_factory=lambda *a, **k: client)
    store.transaction = lambda work: work(store.database, None)
    MongoEmailState(store).ensure_indexes()
    app.extensions['mongo_store'] = store
    store.database.users.insert_many([
        {'id': 1, 'email': 'Student+tag@Example.Invalid', 'marketing_emails_opt_in': True,
         'email_reminders_opt_in': True, 'marketing_opt_in_at': '2026-10-01T00:00:00', 'future_field': 'retain'},
        {'id': 2, 'email': 'studenttag@example.invalid', 'marketing_emails_opt_in': True},
    ])
    app.add_url_rule('/email/unsubscribe/<token>', view_func=lambda token: native_unsubscribe(
        token, lambda title, body: body), methods=['GET', 'POST'])
    return app, store


def test_signed_logged_out_get_and_one_click_post_update_only_the_exact_address(environment):
    app, store = environment
    with app.app_context():
        token = make_unsubscribe_token('student+tag@example.invalid')
    client = app.test_client()
    assert client.get('/email/unsubscribe/' + token).status_code == 200
    assert client.post('/email/unsubscribe/' + token).get_json() == {'status': 'ok', 'unsubscribed': True}
    assert store.database.email_suppressions.count_documents({}) == 1
    user = store.database.users.find_one({'id': 1})
    assert user['marketing_emails_opt_in'] is False
    assert user['email_reminders_opt_in'] is True
    assert user['marketing_opt_in_at'] == '2026-10-01T00:00:00'
    assert user['future_field'] == 'retain'
    assert store.database.users.find_one({'id': 2})['marketing_emails_opt_in'] is True
    assert 'sqlalchemy' not in app.extensions


def test_bad_signature_and_malformed_signed_payload_do_not_change_preferences(environment):
    app, store = environment
    with app.app_context():
        token = make_unsubscribe_token('not-an-email')
    assert app.test_client().post('/email/unsubscribe/invalid').status_code == 400
    assert app.test_client().post('/email/unsubscribe/' + token).status_code == 400
    assert store.database.email_suppressions.count_documents({}) == 0
    assert store.database.users.find_one({'id': 1})['marketing_emails_opt_in'] is True


def test_storage_failure_is_not_reported_as_success(environment, monkeypatch):
    app, store = environment
    with app.app_context():
        token = make_unsubscribe_token('student+tag@example.invalid')
    def fail(*args, **kwargs):
        raise RuntimeError('Synthetic storage failure')
    monkeypatch.setattr(store, 'transaction', fail)
    response = app.test_client().post('/email/unsubscribe/' + token)
    assert response.status_code == 503
    assert 'unsubscribed' not in response.get_data(as_text=True).lower()
    assert store.database.email_suppressions.count_documents({}) == 0
