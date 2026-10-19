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
from intelliplan.persistence.mongo_outlook import MongoOneDrive, MongoOutlook


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


def test_native_onedrive_callback_and_disconnect_are_separate_from_outlook(environment, monkeypatch):
    app, store, outlook = environment
    drive = MongoOneDrive(store)
    drive.ensure_indexes()
    monkeypatch.setattr(App, 'ONEDRIVE_AVAILABLE', True)
    monkeypatch.setattr(App, '_forget_document_matches', lambda: None)
    monkeypatch.setattr(App.onedrive_helper, 'exchange_code', lambda *args: {
        'access_token': 'synthetic-drive', 'refresh_token': 'synthetic-refresh', 'expires_in': 3600})
    monkeypatch.setattr(App.onedrive_helper, 'profile', lambda *args: {'mail': 'drive@example.invalid'})
    app.add_url_rule('/oauth/onedrive/disconnect', view_func=App.onedrive_disconnect, methods=['POST'])
    connect(outlook)
    client = sign_in(app)
    with client.session_transaction() as cookie:
        cookie['microsoft_oauth_purpose'] = 'onedrive'
    result = client.get('/oauth/outlook/callback?state=synthetic-state&code=synthetic-code')
    assert result.get_json() == {'purpose': 'onedrive', 'status': 'connected'}
    assert drive.get(1).account_email == 'drive@example.invalid'
    assert secret_box.is_encrypted(store.database.onedrive_integrations.find_one({})['token_data'])
    assert client.post('/oauth/onedrive/disconnect').status_code == 200
    assert drive.get(1) is None
    assert outlook.get(1) is not None


def test_onedrive_ids_respect_restored_high_water_mark_independently_of_outlook(environment):
    app, store, outlook = environment
    drive = MongoOneDrive(store)
    drive.ensure_indexes()
    store.database.onedrive_integrations.insert_one({'id': 100, 'user_id': 1,
        'token_data': '{}', 'account_email': 'restored@example.invalid'})
    store.seed_id('onedrive_integrations', 100)
    store.seed_id('outlook_integrations', 0)
    connected = drive.connect(2, token_data='{}', account_email='new@example.invalid', account_name='Synthetic')
    assert connected.id == 101
    assert store.database.onedrive_integrations.find_one({'id': 100})['user_id'] == 1
    assert store.database['_sequences'].find_one({'_id': 'outlook_integrations'})['value'] == 0


def test_native_onedrive_getter_refuses_refresh_after_disconnect(environment, monkeypatch):
    app, store, outlook = environment
    drive = MongoOneDrive(store)
    drive.connect(1, token_data=json.dumps({'access_token': 'old', 'refresh_token': 'synthetic-refresh',
        'expires_at': 1}), account_email='drive@example.invalid', account_name='Synthetic')
    monkeypatch.setattr(App, 'ONEDRIVE_AVAILABLE', True)
    def disconnect_during_refresh(token):
        drive.disconnect(1)
        return {**token, 'access_token': 'must-not-use', 'expires_in': 3600}
    monkeypatch.setattr(App.onedrive_helper, 'refresh_token', disconnect_during_refresh)
    from flask_login import login_user
    with app.test_request_context():
        login_user(MongoAccounts(store).get(1))
        assert App.get_onedrive_token() is None
    assert drive.get(1) is None


def test_native_onedrive_document_client_stops_stale_refresh_before_provider_request(environment, monkeypatch):
    app, store, outlook = environment
    from cloud_token_client import TokenExpired
    drive = MongoOneDrive(store)
    drive.connect(1, token_data=json.dumps({'access_token': 'old', 'refresh_token': 'synthetic-refresh',
        'expires_at': 1}), account_email='drive@example.invalid', account_name='Synthetic')
    monkeypatch.setattr(App, '_cloud_docs_configured', lambda provider: True)
    monkeypatch.setattr(App.onedrive_helper.OneDriveClient, '_refresh', lambda self, token: {
        'access_token': 'synthetic-refreshed', 'expires_in': 3600})
    requested = []
    import cloud_token_client
    monkeypatch.setattr(cloud_token_client.requests, 'request', lambda *args, **kwargs: requested.append(args))
    with app.app_context():
        client = App._cloud_doc_client(1, 'onedrive')
        drive.disconnect(1)
        with pytest.raises(TokenExpired):
            client.request('GET', 'https://graph.microsoft.com/v1.0/me/drive')
        with pytest.raises(TokenExpired):
            client.request('GET', 'https://graph.microsoft.com/v1.0/me/drive')
        with pytest.raises(TokenExpired):
            client.headers()
        assert client.token == {}
    assert requested == []
    assert drive.get(1) is None
