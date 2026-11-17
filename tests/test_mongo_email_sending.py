"""Native lifecycle mail renders real templates and claims before external calls."""
from datetime import datetime
from pathlib import Path

from flask import Flask
import mongomock
import pytest

from intelliplan.email import sender, templates
from intelliplan.persistence.mongo import MongoStore
from intelliplan.persistence.mongo_accounts import MongoAccounts
from intelliplan.persistence.mongo_email import MongoEmailState


@pytest.fixture
def environment():
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / 'Main_Project' / 'templates'))
    app.config.update(SECRET_KEY='synthetic-lifecycle-signing', APP_BASE_URL='https://example.invalid')
    client = mongomock.MongoClient()
    store = MongoStore('mongodb://test', 'lifecycle', client_factory=lambda *a, **k: client)
    store.transaction = lambda work: work(store.database, None)
    store.database.users.insert_one({'id': 1, 'email': 'student@example.invalid', 'name': 'Sam',
        'birth_year': 1990, 'email_reminders_opt_in': True})
    repo = MongoEmailState(store)
    repo.ensure_indexes()
    app.extensions['mongo_store'] = store
    calls = []
    app.extensions['email_sender'] = lambda *args, **kwargs: calls.append((args, kwargs)) or 'synthetic-provider-id'
    return app, store, repo, calls, MongoAccounts(store).get(1)


def send(user, **kwargs):
    return sender.send_lifecycle_email(user, 'synthetic-welcome', 'welcome', 'Welcome', marketing=False, **kwargs)


def test_native_lifecycle_renders_headers_and_records_once(environment):
    app, store, repo, calls, user = environment
    with app.app_context():
        assert send(user) == (True, 'ok')
        assert send(user) == (False, 'already_sent')
        assert sender.parse_unsubscribe_token(sender.make_unsubscribe_token(user.email))['email'] == user.email
        assert sender.parse_unsubscribe_token('bad-token') is None
    assert len(calls) == 1
    args, options = calls[0]
    assert args[0] == user.email
    assert '<html' in options['html'].lower()
    assert 'https://example.invalid/email/unsubscribe/' in options['headers']['List-Unsubscribe']
    assert options['headers']['List-Unsubscribe-Post'] == 'List-Unsubscribe=One-Click'
    assert store.database.email_sends.find_one({})['provider_message_id'] == 'synthetic-provider-id'
    assert 'sqlalchemy' not in app.extensions


def test_explicit_provider_failure_can_retry_but_stale_result_cannot_overwrite_retry(environment):
    app, store, repo, calls, user = environment
    app.extensions['email_sender'] = lambda *args, **kwargs: False
    with app.app_context():
        assert send(user) == (False, 'provider_failed')
        old = repo.claim_send(1, 'synthetic-welcome')
        assert repo.record_send(old, 'failed')
        current = repo.claim_send(1, 'synthetic-welcome')
        assert not repo.record_send(old, 'sent', 'stale-result')
        assert repo.record_send(current, 'sent', 'current-result')
        assert send(user) == (False, 'already_sent')


def test_unknown_provider_outcome_stays_pending_without_automatic_resend(environment):
    app, store, repo, calls, user = environment
    def uncertain(*args, **kwargs):
        calls.append('attempt')
        raise TimeoutError('Synthetic unknown acceptance')
    app.extensions['email_sender'] = uncertain
    with app.app_context():
        assert send(user) == (False, 'provider_unknown')
        assert send(user) == (False, 'already_sent')
    assert calls == ['attempt']
    assert store.database.email_sends.find_one({})['status'] == 'pending'


@pytest.mark.parametrize('change', ['suppressed', 'child', 'address', 'deleted', 'school'])
def test_eligibility_is_rechecked_after_render_before_sending(environment, monkeypatch, change):
    app, store, repo, calls, user = environment
    original = templates.render
    def render(*args, **kwargs):
        rendered = original(*args, **kwargs)
        if change == 'suppressed':
            repo.suppress(user.email)
        elif change == 'child':
            store.database.users.update_one({'id': 1}, {'$set': {'birth_year': datetime.utcnow().year - 8}})
        elif change == 'address':
            store.database.users.update_one({'id': 1}, {'$set': {'email': 'different@example.invalid'}})
        elif change == 'school':
            store.database.learning_organizations.insert_one({'id': 1, 'policy': {'notifications': False}})
            store.database.learning_memberships.insert_one({'id': 1, 'user_id': 1, 'organization_id': 1, 'role': 'student'})
        else:
            store.database.users.delete_one({'id': 1})
        return rendered
    monkeypatch.setattr(templates, 'render', render)
    with app.app_context():
        assert send(user, gate='reminder').sent is False
    assert calls == []


def test_missing_sender_does_not_consume_claim_and_deleted_account_is_not_mailed(environment):
    app, store, repo, calls, user = environment
    app.extensions.pop('email_sender')
    with app.app_context():
        assert send(user).reason == 'provider_unavailable'
        assert store.database.email_sends.count_documents({}) == 0
        store.database.users.delete_one({'id': 1})
        assert send(user).reason == 'no_user'
