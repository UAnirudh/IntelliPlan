"""Native Outlook callback, token lifecycle and owner boundaries."""
import json
from types import SimpleNamespace

from cryptography.fernet import Fernet
from flask import Flask
from flask_login import LoginManager
import mongomock
import pytest

import App
import secret_box
from intelliplan.persistence.mongo import MongoStore
from intelliplan.persistence.mongo_accounts import MongoAccounts
from intelliplan.persistence.mongo_outlook import MongoOutlook


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setenv('DATA_ENCRYPTION_KEY', Fernet.generate_key().decode())
    secret_box.reset_cache()
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-outlook')
    client = mongomock.MongoClient()
    store = MongoStore('mongodb://test', 'outlook', client_factory=lambda *a, **k: client)
    store.transaction = lambda work: work(store.database, None)
    store.database.users.insert_many([{'id': 1, 'email': 'owner@example.invalid'},
                                     {'id': 2, 'email': 'peer@example.invalid'}])
    repo = MongoOutlook(store)
    repo.ensure_indexes()
    app.extensions['mongo_store'] = store
    login = LoginManager(app)
    login.user_loader(MongoAccounts(store).get)
    app.add_url_rule('/oauth/outlook/callback', view_func=App.outlook_oauth_callback)
    app.add_url_rule('/oauth/outlook/disconnect', view_func=App.outlook_disconnect, methods=['POST'])
    app.add_url_rule('/calendar/connections', view_func=App.calendar_connections)
    monkeypatch.setattr(App, 'OUTLOOK_AVAILABLE', True)
    monkeypatch.setattr(App, 'GCAL_AVAILABLE', False)
    monkeypatch.setattr(App, '_microsoft_back', lambda purpose, status: {'purpose': purpose, 'status': status})
    monkeypatch.setattr(App.outlook_calendar_helper, 'configured', lambda: True)
    monkeypatch.setattr(App.outlook_calendar_helper, 'exchange_code', lambda code: {
        'access_token': 'synthetic-access', 'refresh_token': 'synthetic-refresh', 'expires_in': 3600})
    monkeypatch.setattr(App.outlook_calendar_helper, 'profile', lambda token: {
        'mail': 'school@example.invalid', 'displayName': 'Synthetic school account'})
    yield app, store, repo
    secret_box.reset_cache()


def sign_in(app, identity=1, state='synthetic-state'):
    client = app.test_client()
    with client.session_transaction() as cookie:
        cookie['_user_id'] = str(identity)
        cookie['_fresh'] = True
        cookie['outlook_oauth_state'] = state
        cookie['microsoft_oauth_purpose'] = 'outlook'
    return client


def connect(repo, identity=1, **token):
    return repo.connect(identity, token_data=json.dumps({'access_token': 'synthetic-original', **token}),
                        account_email='school@example.invalid', account_name='Synthetic')


def test_native_callback_status_and_disconnect_without_sql_registration(environment):
    app, store, repo = environment
    client = sign_in(app)
    result = client.get('/oauth/outlook/callback?state=synthetic-state&code=synthetic-code')
    assert result.get_json()['status'] == 'connected'
    raw = store.database.outlook_integrations.find_one({'user_id': 1})
    assert secret_box.is_encrypted(raw['token_data'])
    assert 'synthetic-access' not in repr(repo.get(1))
    status = client.get('/calendar/connections').get_json()
    assert status['outlook'] is True and status['outlook_account'] == 'school@example.invalid'
    assert sign_in(app, 2).get('/calendar/connections').get_json()['outlook'] is False
    assert sign_in(app, 2).post('/oauth/outlook/disconnect').status_code == 200
    assert repo.get(1) is not None
    assert client.post('/oauth/outlook/disconnect').status_code == 200
    assert repo.get(1) is None
    assert 'sqlalchemy' not in app.extensions


def test_native_callback_rejects_wrong_state_before_persisting(environment):
    app, store, repo = environment
    assert sign_in(app).get('/oauth/outlook/callback?state=wrong&code=synthetic-code').get_json()['status'] == 'error'
    assert repo.get(1) is None


def test_native_token_refresh_preserves_grant_and_current_school_permission(environment, monkeypatch):
    app, store, repo = environment
    connect(repo, refresh_token='synthetic-refresh', expires_at=0)
    monkeypatch.setattr(App.outlook_calendar_helper, 'refresh_token', lambda token: {
        **token, 'access_token': 'synthetic-new', 'refresh_token': 'synthetic-rotated', 'expires_in': 3600})
    with app.test_request_context():
        token = App.get_outlook_token(user_id=1)
        assert token['access_token'] == 'synthetic-new'
        assert json.loads(repo.get(1).token_data)['refresh_token'] == 'synthetic-rotated'
        assert App.get_outlook_token(user_id=2) is None
    connect(repo, refresh_token='synthetic-refresh', expires_at=0)
    def revoke_policy(token):
        store.database.learning_organizations.insert_one({'id': 1, 'policy': {'calendar': False}})
        store.database.learning_memberships.insert_one({'id': 1, 'user_id': 1, 'organization_id': 1})
        return {**token, 'access_token': 'must-not-save', 'expires_in': 3600}
    monkeypatch.setattr(App.outlook_calendar_helper, 'refresh_token', revoke_policy)
    with app.test_request_context():
        assert App.get_outlook_token(user_id=1) is None
    assert json.loads(repo.get(1).token_data)['access_token'] == 'synthetic-original'


def test_stale_refresh_cannot_overwrite_reconnection_or_recreate_disconnect(environment, monkeypatch):
    app, store, repo = environment
    original = connect(repo, refresh_token='synthetic-refresh', expires_at=0)
    store.database.outlook_integrations.update_one({'id': original.id}, {'$set': {'future_field': 'retain'}})
    replacement = connect(repo, access_token='synthetic-replacement')
    assert not repo.refresh(1, original, json.dumps({'access_token': 'stale'}))
    assert store.database.outlook_integrations.find_one({'id': replacement.id})['future_field'] == 'retain'
    repo.disconnect(1)
    assert not repo.refresh(1, replacement, json.dumps({'access_token': 'stale'}))
    assert repo.get(1) is None
    store.database.users.delete_one({'id': 1})
    with pytest.raises(LookupError):
        connect(repo)


def test_legacy_plaintext_grant_remains_readable_and_invalid_owner_proof_is_rejected(environment):
    app, store, repo = environment
    store.database.outlook_integrations.insert_one({'id': 100, 'user_id': 1,
        'token_data': json.dumps({'access_token': 'synthetic-legacy'}), 'connected_at': '2026-10-18T00:00:00'})
    original = repo.get(1)
    assert json.loads(original.token_data)['access_token'] == 'synthetic-legacy'
    assert not repo.refresh(2, original, '{}')
    wrong = SimpleNamespace(**{**vars(original), 'user_id': 2})
    assert not repo.refresh(1, wrong, '{}')
