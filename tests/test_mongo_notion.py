"""Notion OAuth/manual connections, rotating tokens and owner boundaries."""
from datetime import timedelta

from cryptography.fernet import Fernet
from flask import Flask, session
from flask_login import LoginManager, login_user
import mongomock
import pytest

import App
import secret_box
from intelliplan.persistence.codec import pack_document
from intelliplan.persistence.mongo import MongoStore
from intelliplan.persistence.mongo_accounts import MongoAccounts
from intelliplan.persistence.mongo_notion import MongoNotion
from time_utils import utcnow


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setenv('DATA_ENCRYPTION_KEY', Fernet.generate_key().decode())
    secret_box.reset_cache()
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-notion')
    client = mongomock.MongoClient()
    store = MongoStore('mongodb://test', 'notion', client_factory=lambda *a, **k: client)
    store.transaction = lambda work: work(store.database, None)
    store.database.users.insert_many([{'id': 1, 'email': 'owner@example.invalid'},
                                     {'id': 2, 'email': 'peer@example.invalid'}])
    repo = MongoNotion(store)
    repo.ensure_indexes()
    app.extensions['mongo_store'] = store
    login = LoginManager(app)
    login.user_loader(MongoAccounts(store).get)
    for path, function, methods in [
        ('/connect', App.notion_connect, ['POST']),
        ('/disconnect', App.notion_disconnect, ['POST']),
        ('/database', App.notion_set_database, ['POST']),
        ('/create-database', App.notion_create_database_route, ['POST']),
        ('/status', App.notion_status, ['GET']),
        ('/callback', App.oauth_notion_callback, ['GET']),
    ]:
        app.add_url_rule(path, view_func=function, methods=methods)
    app.add_url_rule('/settings', endpoint='settings', view_func=lambda: '')
    monkeypatch.setattr(App, 'NOTION_AVAILABLE', True)
    monkeypatch.setattr(App, 'test_notion_token_detail', lambda token: (True, {
        'type': 'bot', 'id': 'synthetic-bot', 'bot': {'workspace_name': 'Synthetic workspace'}}))
    monkeypatch.setattr(App, 'get_notion_databases', lambda token: [])
    yield app, store, repo
    secret_box.reset_cache()


def sign_in(app, identity=1):
    client = app.test_client()
    with client.session_transaction() as cookie:
        cookie['_user_id'] = str(identity)
        cookie['_fresh'] = True
    return client


def oauth(repo, identity=1, token='synthetic-access', **metadata):
    return repo.connect(identity, token=token, oauth={'refresh_token': 'synthetic-refresh',
        'workspace_name': 'Synthetic workspace', 'expires_at': utcnow() - timedelta(minutes=1), **metadata})


def test_manual_connect_status_database_and_disconnect_without_sql(environment):
    app, store, repo = environment
    client = sign_in(app)
    assert client.post('/connect', json={'token': 'synthetic-manual'}).get_json()['status'] == 'ok'
    assert client.post('/database', json={'database_id': 'synthetic-database'}).status_code == 200
    assert repo.get(1).database_id == 'synthetic-database'
    assert client.get('/status').get_json()['workspace']['name'] == 'Synthetic workspace'
    raw = store.database.notion_integrations.find_one({'user_id': 1})
    assert secret_box.is_encrypted(raw['token']) and 'synthetic-manual' not in repr(repo.get(1))
    assert sign_in(app, 2).post('/database', json={'database_id': 'other'}).status_code == 409
    assert sign_in(app, 2).post('/disconnect').status_code == 200
    assert repo.get(1) is not None
    assert client.post('/disconnect').status_code == 200
    assert repo.get(1) is None
    with client.session_transaction() as cookie:
        assert 'notion_token' not in cookie
    assert 'sqlalchemy' not in app.extensions


def test_oauth_callback_preserves_existing_selection_and_template_default(environment, monkeypatch):
    app, store, repo = environment
    monkeypatch.setattr(App, 'exchange_notion_code', lambda *a, **k: {
        'access_token': 'synthetic-access', 'refresh_token': 'synthetic-refresh',
        'workspace_name': 'Synthetic workspace', 'duplicated_template_id': 'template'})
    client = sign_in(app)
    for expected_database in ('template', 'chosen'):
        with client.session_transaction() as cookie:
            cookie['notion_oauth_state'] = 'synthetic-state'
        assert client.get('/callback?state=synthetic-state&code=synthetic-code').status_code == 302
        assert repo.get(1).database_id == expected_database
        repo.select_database(1, 'chosen')
    assert secret_box.is_encrypted(store.database.notion_integrations.find_one({})['refresh_token'])


def test_refresh_rotates_both_tokens_but_cannot_overwrite_reconnection(environment, monkeypatch):
    app, store, repo = environment
    original = oauth(repo)
    fresh = {'access_token': 'renewed-access', 'refresh_token': 'renewed-refresh',
             'expires_at': utcnow() + timedelta(hours=1)}
    monkeypatch.setattr(App, 'refresh_notion_token', lambda token: fresh)
    with app.test_request_context():
        login_user(MongoAccounts(store).get(1))
        assert App.get_notion_token_and_db()[0] == 'renewed-access'
    assert repo.get(1).refresh_token == 'renewed-refresh'
    assert not repo.refresh(1, original, fresh)
    oauth(repo)
    def reconnect(token):
        repo.connect(1, token='new-manual')
        return fresh
    monkeypatch.setattr(App, 'refresh_notion_token', reconnect)
    with app.test_request_context():
        login_user(MongoAccounts(store).get(1))
        assert App.get_notion_token_and_db() == (None, None)
    assert repo.get(1).token == 'new-manual'
    assert repo.get(1).refresh_token is None and repo.get(1).token_expires_at is None


def test_refresh_disconnect_and_stale_cookie_cannot_recreate_grant(environment, monkeypatch):
    app, store, repo = environment
    oauth(repo)
    def revoke(token):
        repo.disconnect(1)
        return {'access_token': 'must-not-return', 'refresh_token': 'must-not-save'}
    monkeypatch.setattr(App, 'refresh_notion_token', revoke)
    with app.test_request_context():
        login_user(MongoAccounts(store).get(1))
        session['notion_token'] = 'stale-cookie'
        assert App.get_notion_token_and_db() == (None, None)
    client = sign_in(app)
    with client.session_transaction() as cookie:
        cookie['notion_token'] = 'stale-cookie'
    assert client.post('/database', json={'database_id': 'stale'}).status_code == 409
    assert repo.get(1) is None


def test_create_database_does_not_select_for_a_replaced_connection(environment, monkeypatch):
    app, store, repo = environment
    repo.connect(1, token='original')
    def replace(*a, **k):
        repo.connect(1, token='replacement')
        return 'created-remotely'
    monkeypatch.setattr(App, 'create_intelliplan_database', replace)
    response = sign_in(app).post('/create-database', json={'parent_page_id': 'synthetic-parent'})
    assert response.status_code == 409
    assert response.get_json()['database_id'] == 'created-remotely'
    assert repo.get(1).database_id is None


def test_source_dates_unknown_fields_ids_and_owner_guard(environment):
    app, store, repo = environment
    store.database.notion_integrations.insert_one(pack_document({'id': 100, 'user_id': 1,
        'token': 'synthetic-legacy', 'database_id': 'chosen', 'future_field': 'retain',
        'connected_at': '2026-10-19T00:00:00.123456', 'token_expires_at': '2026-10-19T01:00:00.654321'}))
    store.seed_id('notion_integrations', 100)
    original = repo.get(1)
    assert original.connected_at.microsecond == 123456
    assert original.token_expires_at.microsecond == 654321
    updated = oauth(repo, duplicated_template_id='template')
    assert updated.id == 100 and updated.database_id == 'chosen'
    assert store.database.notion_integrations.find_one({'id': 100})['future_field'] == 'retain'
    assert not repo.refresh(2, updated, {'access_token': 'forbidden'})
    assert not repo.select_database(2, 'forbidden', expected=updated)
    assert repo.connect(2, token='peer').id == 101
    store.database.users.delete_one({'id': 1})
    with pytest.raises(LookupError):
        repo.connect(1, token='must-not-create')
