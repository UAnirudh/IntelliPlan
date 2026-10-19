"""Native LMS connection routes and stale-refresh isolation."""
from datetime import timedelta

from cryptography.fernet import Fernet
from flask import Flask
from flask_login import LoginManager, login_user
import mongomock
import pytest

import App
import secret_box
from intelliplan.persistence.codec import pack_document
from intelliplan.persistence.lms_grants import SCHEMAS
from intelliplan.persistence.mongo import MongoStore
from intelliplan.persistence.mongo_accounts import MongoAccounts
from intelliplan.persistence.mongo_lms_grants import MongoLMSGrants
from time_utils import utcnow


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setenv('DATA_ENCRYPTION_KEY', Fernet.generate_key().decode())
    secret_box.reset_cache()
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-lms')
    client = mongomock.MongoClient()
    store = MongoStore('mongodb://test', 'lms', client_factory=lambda *a, **k: client)
    store.transaction = lambda work: work(store.database, None)
    store.database.users.insert_many([{'id': 1, 'email': 'owner@example.invalid'},
                                     {'id': 2, 'email': 'peer@example.invalid'}])
    app.extensions['mongo_store'] = store
    login = LoginManager(app)
    login.user_loader(MongoAccounts(store).get)
    repos = {name: MongoLMSGrants(store, name) for name in SCHEMAS}
    for repository in repos.values():
        repository.ensure_indexes()
    for provider, name in [('google_classroom', 'classroom'), ('blackboard', 'blackboard'), ('moodle', 'moodle')]:
        app.add_url_rule('/status/' + provider, endpoint='status_' + provider,
            view_func=getattr(App, 'api_' + name + '_status'))
        app.add_url_rule('/disconnect/' + provider, endpoint='disconnect_' + provider,
            view_func=getattr(App, 'api_' + name + '_disconnect'), methods=['POST'])
    app.add_url_rule('/callback/<provider>', view_func=App.api_lms_callback)
    app.add_url_rule('/moodle/connect', view_func=App.api_lms_connect_moodle_manual, methods=['POST'])
    yield app, store, repos
    secret_box.reset_cache()


def sign_in(app, identity=1):
    client = app.test_client()
    with client.session_transaction() as cookie:
        cookie['_user_id'] = str(identity)
        cookie['_fresh'] = True
    return client


def connect(repo, identity=1, **values):
    if repo.provider == 'moodle':
        fields = {'ws_token': 'synthetic-ws', 'moodle_url': 'https://moodle.example.invalid'}
    else:
        fields = {'access_token': 'synthetic-access', 'refresh_token': 'synthetic-refresh',
            'token_expires_at': utcnow() - timedelta(minutes=1)}
        if repo.provider == 'blackboard':
            fields['institution_url'] = 'https://blackboard.example.invalid'
    return repo.connect(identity, **{**fields, **values})


@pytest.mark.parametrize('provider', list(SCHEMAS))
def test_status_disconnect_isolation_and_source_metadata(environment, provider):
    app, store, repos = environment
    repo = repos[provider]
    grant = connect(repo)
    collection = store.database[repo.collection_name]
    collection.update_one({'id': grant.id}, {'$set': {'future_field': 'retain'}})
    updated = connect(repo)
    assert updated.id == grant.id
    assert collection.find_one({'id': grant.id})['future_field'] == 'retain'
    assert all(secret_box.is_encrypted(collection.find_one({'id': grant.id})[key]) for key in repo.secrets)
    assert 'synthetic-access' not in repr(updated) and 'synthetic-ws' not in repr(updated)
    assert sign_in(app).get('/status/' + provider).get_json()['connected'] is True
    peer = sign_in(app, 2)
    assert peer.get('/status/' + provider).get_json()['connected'] is False
    assert peer.post('/disconnect/' + provider).status_code == 200
    assert repo.get(1) is not None
    assert sign_in(app).post('/disconnect/' + provider).status_code == 200
    assert repo.get(1) is None
    assert 'sqlalchemy' not in app.extensions


@pytest.mark.parametrize('provider', ['google_classroom', 'blackboard'])
def test_native_callback_and_wrong_state(environment, monkeypatch, provider):
    app, store, repos = environment
    monkeypatch.setattr(App, '_classroom_exchange_code', lambda code: {
        'access_token': 'synthetic-access', 'refresh_token': 'synthetic-refresh', 'expires_in': 3600})
    monkeypatch.setattr(App, '_classroom_get_userinfo', lambda token: {'email': 'school@example.invalid', 'name': 'Student'})
    monkeypatch.setattr(App, '_blackboard_exchange_code', lambda *a: {
        'access_token': 'synthetic-access', 'refresh_token': 'synthetic-refresh', 'expires_in': 3600, 'user_id': 'student'})
    monkeypatch.setattr(App, '_blackboard_get_userinfo', lambda *a: {'userName': 'synthetic-student'})
    client = sign_in(app)
    with client.session_transaction() as cookie:
        cookie['lms_oauth_state'] = 'synthetic-state'
        cookie['blackboard_institution_url'] = 'https://school.example.invalid'
    assert client.get('/callback/' + provider + '?code=synthetic-code&state=wrong').status_code == 302
    assert repos[provider].get(1) is None
    with client.session_transaction() as cookie:
        cookie['lms_oauth_state'] = 'synthetic-state'
    response = client.get('/callback/' + provider + '?code=synthetic-code&state=synthetic-state')
    assert 'lms_connected=' + provider in response.location
    assert repos[provider].get(1) is not None


def test_moodle_manual_connect_uses_verified_identity(environment, monkeypatch):
    app, store, repos = environment
    monkeypatch.setattr(App, '_moodle_call', lambda *a: {
        'userid': 42, 'username': 'synthetic-user', 'fullname': 'Synthetic student', 'sitename': 'Synthetic school'})
    response = sign_in(app).post('/moodle/connect', json={
        'moodle_url': 'school.example.invalid/', 'ws_token': 'synthetic-ws'})
    assert response.status_code == 200
    row = repos['moodle'].get(1)
    assert row.moodle_user_id == '42' and row.moodle_url == 'https://school.example.invalid'


@pytest.mark.parametrize('provider', ['google_classroom', 'blackboard'])
def test_token_refresh_rotation_and_disconnect_during_refresh(environment, monkeypatch, provider):
    app, store, repos = environment
    repo = repos[provider]
    original = connect(repo)
    function = App._classroom_access_token_for if provider == 'google_classroom' else App._blackboard_access_token_for
    refresh_name = '_classroom_refresh_token' if provider == 'google_classroom' else '_blackboard_refresh_token'
    monkeypatch.setattr(App, refresh_name, lambda *a: {
        'access_token': 'renewed-access', 'refresh_token': 'rotated-refresh', 'expires_in': 3600})
    with app.test_request_context():
        login_user(MongoAccounts(store).get(1))
        token, grant = function(1)
        assert token == 'renewed-access' and grant.refresh_token == 'rotated-refresh'
    assert not repo.refresh(1, original, access_token='stale')
    assert not repo.refresh(2, grant, access_token='foreign')
    connect(repo)
    def revoke(*args):
        repo.disconnect(1)
        return {'access_token': 'must-not-return', 'expires_in': 3600}
    monkeypatch.setattr(App, refresh_name, revoke)
    with app.test_request_context():
        login_user(MongoAccounts(store).get(1))
        assert function(1) == (None, None)
    assert repo.get(1) is None


@pytest.mark.parametrize('provider', list(SCHEMAS))
def test_restored_duplicate_owners_select_newest_and_respect_sequences(environment, provider):
    app, store, repos = environment
    repo = repos[provider]
    collection = store.database[repo.collection_name]
    for identity in (100, 101):
        raw = {'id': identity, 'user_id': 1, 'connected_at': '2026-10-19T00:00:00.123456'}
        raw.update({'ws_token': 'legacy', 'moodle_url': 'https://school.example.invalid'} if provider == 'moodle' else
            {'access_token': 'legacy', 'refresh_token': 'legacy', 'institution_url': 'https://school.example.invalid'})
        collection.insert_one(pack_document(raw))
    store.seed_id(repo.collection_name, 101)
    assert repo.get(1).id == 101 and repo.get(1).connected_at.microsecond == 123456
    assert connect(repo, 2).id == 102
    store.database.users.delete_one({'id': 1})
    with pytest.raises(LookupError):
        connect(repo)


@pytest.mark.parametrize('provider,origin', [('google_classroom', 'account_email'), ('blackboard', 'institution_url')])
def test_different_provider_identity_does_not_inherit_old_refresh(environment, provider, origin):
    app, store, repos = environment
    repo = repos[provider]
    original = connect(repo, **{origin: 'original@example.invalid' if provider == 'google_classroom' else 'https://first.example.invalid'})
    values = {'access_token': 'new-account', origin: 'next@example.invalid' if provider == 'google_classroom' else 'https://next.example.invalid'}
    changed = repo.connect(1, **values)
    assert changed.id == original.id and changed.refresh_token is None
    assert not repo.refresh(1, original, access_token='stale')


@pytest.mark.parametrize('identity', ['bb_user_id', 'bb_username'])
def test_blackboard_different_user_at_same_school_does_not_inherit_refresh(environment, identity):
    app, store, repos = environment
    repo = repos['blackboard']
    original = connect(repo, **{identity: 'first-student'})
    changed = repo.connect(1, institution_url=original.institution_url, access_token='other-student',
        **{identity: 'next-student'})
    assert changed.refresh_token is None
    assert getattr(changed, identity) == 'next-student'
    assert not repo.refresh(1, original, access_token='stale')
