"""Google grant lifecycle runs without a registered SQL database."""
from decimal import Decimal
import json
from types import SimpleNamespace

from cryptography.fernet import Fernet
from flask import Flask, session
from flask_login import LoginManager, login_user
import mongomock
import pytest

import App
import secret_box
from intelliplan.persistence.codec import pack_document
from intelliplan.persistence.json_values import loads_exact
from intelliplan.persistence.mongo import MongoStore
from intelliplan.persistence.mongo_accounts import MongoAccounts
from intelliplan.persistence.mongo_google import MongoGoogle


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setenv('DATA_ENCRYPTION_KEY', Fernet.generate_key().decode())
    secret_box.reset_cache()
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-google')
    client = mongomock.MongoClient()
    store = MongoStore('mongodb://test', 'google', client_factory=lambda *a, **k: client)
    store.transaction = lambda work: work(store.database, None)
    store.database.users.insert_many([{'id': 1, 'email': 'owner@example.invalid'},
                                     {'id': 2, 'email': 'peer@example.invalid'}])
    repo, drive = MongoGoogle(store), MongoGoogle(store, 'drive')
    repo.ensure_indexes()
    drive.ensure_indexes()
    app.extensions['mongo_store'] = store
    login = LoginManager(app)
    login.user_loader(MongoAccounts(store).get)
    for path, function, methods in [
        ('/status', App.gcal_status, ['GET']),
        ('/activate', App.gcal_activate, ['POST']),
        ('/remove', App.gcal_remove, ['POST']),
        ('/disconnect', App.google_disconnect, ['POST']),
        ('/drive/disconnect', App.google_drive_disconnect, ['POST']),
        ('/events', App.calendar_events, ['GET']),
        ('/export', App.calendar_export, ['POST']),
        ('/callback', App._handle_google_callback, ['GET']),
    ]:
        app.add_url_rule(path, view_func=function, methods=methods)
    monkeypatch.setattr(App, 'GCAL_AVAILABLE', True)
    monkeypatch.setattr(App, '_outlook_upcoming_events', lambda: None)
    monkeypatch.setattr(App, '_forget_document_matches', lambda: None)
    yield app, store, repo, drive
    secret_box.reset_cache()


def sign_in(app, identity=1):
    client = app.test_client()
    with client.session_transaction() as cookie:
        cookie['_user_id'] = str(identity)
        cookie['_fresh'] = True
    return client


def connect(repo, identity=1, email='school@example.invalid', **token):
    return repo.connect(identity, token={'token': 'synthetic-token', **token},
                        account_email=email, account_name='Synthetic')


def test_calendar_accounts_switch_remove_and_owner_boundaries(environment):
    app, store, repo, drive = environment
    first = connect(repo)
    second = connect(repo, email='other@example.invalid')
    client = sign_in(app)
    status = client.get('/status').get_json()
    assert status['active_id'] == second.id and len(status['accounts']) == 2
    assert sign_in(app, 2).post('/activate', json={'id': first.id}).status_code == 404
    assert sign_in(app, 2).post('/remove', json={'id': first.id}).status_code == 404
    assert client.post('/activate', json={'id': first.id}).status_code == 200
    assert repo.get(1).id == first.id
    assert client.post('/remove', json={'id': first.id}).status_code == 200
    assert repo.get(1).id == second.id and repo.get(1).is_active
    connect(drive)
    assert client.post('/disconnect').status_code == 200
    assert repo.list(1) == [] and drive.get(1) is not None
    assert 'sqlalchemy' not in app.extensions


def test_restored_token_precision_unknown_fields_and_sequence_isolation(environment):
    app, store, repo, drive = environment
    previous = '{"token":"old","refresh_token":"retain","future":0.1234567890123456789}'
    store.database.google_integrations.insert_one(pack_document({'id': 100, 'user_id': 1,
        'account_email': 'school@example.invalid', 'account_name': 'School',
        'token_data': previous, 'is_active': True, 'future_column': 'retain'}))
    store.seed_id('google_integrations', 100)
    updated = connect(repo)
    assert updated.id == 100
    token = loads_exact(updated.token_data)
    assert token['refresh_token'] == 'retain'
    assert token['future'] == Decimal('0.1234567890123456789')
    raw = store.database.google_integrations.find_one({'id': 100})
    assert raw['future_column'] == 'retain' and secret_box.is_encrypted(raw['token_data'])
    assert 'synthetic-token' not in repr(updated)
    assert connect(repo, email='new@example.invalid').id == 101
    assert connect(drive).id == 1
    assert len(repo.list(1)) == 2


def test_refresh_proof_rejects_reconnect_disconnect_and_wrong_owner(environment):
    app, store, repo, drive = environment
    original = connect(repo)
    current = connect(repo, token='replacement')
    assert not repo.refresh(1, original, '{}')
    assert not repo.refresh(2, current, '{}')
    repo.disconnect(1)
    assert not repo.refresh(1, current, '{}')
    assert repo.list(1) == []
    store.database.users.delete_one({'id': 1})
    with pytest.raises(LookupError):
        connect(repo)


def test_restored_duplicate_drive_email_selects_newest_in_both_backends(environment):
    app, store, repo, drive = environment
    from intelliplan.persistence.google import SQLGoogle
    for identity in (101, 102):
        store.database.google_drive_integrations.insert_one({'id': identity, 'user_id': 1,
            'account_email': 'same@example.invalid', 'token_data': '{}'})
    assert drive.get(1, 'same@example.invalid').id == 102
    sql = SQLGoogle(None, None, None, 'drive')
    sql.list = drive.list
    assert sql.get(1, 'same@example.invalid').id == 102


def test_disconnected_authenticated_user_cannot_use_cookie_token(environment):
    app, store, repo, drive = environment
    with app.test_request_context():
        login_user(MongoAccounts(store).get(1))
        session['google_token'] = {'token': 'stale'}
        assert App.get_google_token() is None
    with app.test_request_context():
        session['google_token'] = {'token': 'guest'}
        assert App.get_google_token()['token'] == 'guest'


def test_provider_failure_preserves_all_saved_calendar_accounts(environment, monkeypatch):
    app, store, repo, drive = environment
    connect(repo)
    connect(repo, email='other@example.invalid')
    def unavailable(token):
        raise RuntimeError('Synthetic provider failure')
    monkeypatch.setattr(App, 'get_upcoming_events', unavailable)
    assert sign_in(app).get('/events').get_json()['connected'] is False
    assert len(repo.list(1)) == 2


@pytest.mark.parametrize('purpose', ['calendar', 'drive'])
def test_verified_native_oauth_callback(environment, monkeypatch, purpose):
    app, store, repo, drive = environment
    monkeypatch.setattr(App, 'exchange_code_for_token', lambda *a, **k: {
        'token': 'synthetic-access', 'refresh_token': 'synthetic-refresh',
        'scopes': [App.google_drive_helper.DRIVE_SCOPE]})
    monkeypatch.setattr(App, 'has_calendar_scope', lambda token: True)
    monkeypatch.setattr(App.requests, 'get', lambda *a, **k: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {'sub': 'synthetic-sub',
            'email': 'owner@example.invalid', 'email_verified': True, 'name': 'Owner'}))
    client = sign_in(app)
    with client.session_transaction() as cookie:
        cookie['oauth_state'] = 'synthetic-state'
        cookie['oauth_purpose'] = purpose
    response = client.get('/callback?state=synthetic-state&code=synthetic-code')
    assert response.status_code == 302
    assert (repo if purpose == 'calendar' else drive).get(1) is not None
    assert (drive if purpose == 'calendar' else repo).get(1) is None
    if purpose == 'calendar':
        assert store.database.users.find_one({'id': 1})['google_id'] == 'synthetic-sub'


def test_drive_stale_refresh_permanently_stops_provider_requests(environment, monkeypatch):
    app, store, repo, drive = environment
    from cloud_token_client import TokenExpired
    import cloud_token_client
    connect(drive, refresh_token='synthetic-refresh', expires_at=1)
    monkeypatch.setattr(App, '_cloud_docs_configured', lambda provider: True)
    monkeypatch.setattr(App.google_drive_helper.DriveClient, '_refresh', lambda self, token: {
        'token': 'synthetic-refreshed', 'expires_in': 3600})
    requested = []
    monkeypatch.setattr(cloud_token_client.requests, 'request', lambda *a, **k: requested.append(a))
    with app.app_context():
        client = App._cloud_doc_client(1, 'google_drive')
        drive.disconnect(1)
        for _ in range(2):
            with pytest.raises(TokenExpired):
                client.request('GET', 'https://www.googleapis.com/drive/v3/files')
        assert client.token == {}
    assert requested == []


def test_drive_getter_does_not_return_refresh_from_revoked_grant(environment, monkeypatch):
    app, store, repo, drive = environment
    connect(drive, refresh_token='synthetic-refresh', expires_at=1,
            scopes=[App.google_drive_helper.DRIVE_SCOPE])
    def revoke(token):
        drive.disconnect(1)
        return {**token, 'token': 'must-not-return'}
    monkeypatch.setattr(App, 'refresh_access_token', revoke)
    with app.test_request_context():
        login_user(MongoAccounts(store).get(1))
        assert App.get_google_drive_token() is None
    assert drive.get(1) is None


def test_export_refresh_updates_selected_account_and_cannot_restore_disconnect(environment, monkeypatch):
    app, store, repo, drive = environment
    first = connect(repo)
    selected = connect(repo, email='second@example.invalid', token='selected')
    monkeypatch.setattr(App, 'get_outlook_token', lambda: None)
    monkeypatch.setattr(App, 'add_schedule_to_calendar', lambda *a: (['synthetic-event'], 'renewed', 0))
    client = sign_in(app)
    assert client.post('/export', json={'schedule_data': {'synthetic': True}}).get_json()['status'] == 'ok'
    assert json.loads(repo.get(1).token_data)['token'] == 'renewed'
    assert json.loads(next(row for row in repo.list(1) if row.id == first.id).token_data)['token'] == 'synthetic-token'
    assert repo.get(1).id == selected.id
    def disconnect_during_export(*args):
        repo.disconnect(1)
        return ['synthetic-event'], 'must-not-persist', 0
    monkeypatch.setattr(App, 'add_schedule_to_calendar', disconnect_during_export)
    assert client.post('/export', json={'schedule_data': {'synthetic': True}}).status_code == 200
    assert repo.list(1) == []
    with app.test_request_context():
        login_user(MongoAccounts(store).get(1))
        session['google_token'] = {'token': 'must-not-persist'}
        assert App.get_google_token() is None
