"""Shared SQL/native LMS-profile contract and encrypted snapshot compatibility."""
import json
import uuid
from datetime import datetime

from cryptography.fernet import Fernet
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
import mongomock
import pytest

import secret_box
from intelliplan.persistence.canvas import SQLCanvas, canvas_integrations
from intelliplan.persistence.mongo_canvas import MongoCanvas
from intelliplan.persistence.codec import pack_document
from intelliplan.persistence.linked_accounts import SQLLinkedAccounts, linked_accounts
from intelliplan.persistence.mongo import MongoStore
from intelliplan.persistence.mongo_linked_accounts import MongoLinkedAccounts


@pytest.fixture(params=['sql', 'mongo'])
def profiles(request, monkeypatch):
    monkeypatch.setenv('DATA_ENCRYPTION_KEY', Fernet.generate_key().decode())
    secret_box.reset_cache()
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
    with app.app_context():
        if request.param == 'mongo':
            client = mongomock.MongoClient()
            store = MongoStore('mongodb://test', 'profiles', client_factory=lambda *a, **k: client)
            store.transaction = lambda work: work(store.database, None)
            store.database.users.insert_many([{'id': 1}, {'id': 2}])
            repo = MongoLinkedAccounts(store)
            repo.ensure_indexes()
            app.extensions['mongo_store'] = store
            canvas = MongoCanvas(store)
            canvas.ensure_indexes()
        else:
            db = SQLAlchemy(app)
            class User(db.Model):
                __tablename__ = 'users'
                id = db.Column(db.Integer, primary_key=True)
            class Profile(db.Model):
                __tablename__ = 'linked_accounts'
                id = db.Column(db.Integer, primary_key=True)
                user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
                profile_id = db.Column(db.String(16), unique=True, default=lambda: uuid.uuid4().hex[:16])
                name = db.Column(db.String)
                login_type = db.Column(db.String)
                is_active = db.Column(db.Boolean)
                credentials = db.Column(db.Text)
                created_at = db.Column(db.DateTime, default=datetime.utcnow)
                def get_credentials(self):
                    return json.loads(secret_box.decrypt(self.credentials))
                def set_credentials(self, values):
                    self.credentials = secret_box.encrypt(json.dumps(values))
            class Canvas(db.Model):
                __tablename__ = 'canvas_integrations'
                id = db.Column(db.Integer, primary_key=True)
                user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
                canvas_base = db.Column(db.String, nullable=False)
                access_token = db.Column(secret_box.EncryptedText, nullable=False)
                refresh_token = db.Column(secret_box.EncryptedText)
                token_expires_at = db.Column(db.DateTime)
                canvas_user_id = db.Column(db.String)
                canvas_user_name = db.Column(db.String)
                connected_at = db.Column(db.DateTime)
            db.create_all()
            db.session.add_all([User(id=1), User(id=2)])
            db.session.commit()
            repo = SQLLinkedAccounts(db, Profile, User)
            canvas = SQLCanvas(db, Canvas, Profile, User)
        app.extensions['canvas_repository'] = canvas
        app.extensions['linked_accounts_repository'] = repo
        yield app, repo
    secret_box.reset_cache()


def test_owner_scoping_activation_and_encryption(profiles):
    app, repo = profiles
    first = repo.create_active(1, 'School', 'canvas', {'token': 'synthetic-secret'})
    second = repo.create_active(1, 'Other', 'studentvue', {'password': 'synthetic-password'})
    peer = repo.create_active(2, 'Peer', 'canvas', {'token': 'peer-secret'})
    assert repo.active(1).profile_id == second.profile_id
    assert len(repo.list(1, active=True)) == 1
    assert repo.by_type(1, 'canvas').profile_id == first.profile_id
    assert secret_box.is_encrypted(repo.list(1)[0].credentials)
    assert first.get_credentials() == {'token': 'synthetic-secret'}
    assert not repo.switch(1, peer.profile_id)
    assert not repo.rename(1, peer.profile_id, 'Wrong')
    assert not repo.delete(1, peer.profile_id)
    assert repo.active(1).profile_id == second.profile_id
    assert repo.switch(1, first.profile_id)
    assert repo.active(1).profile_id == first.profile_id
    assert len(repo.list(1, active=True)) == 1
    assert repo.rename(1, first.profile_id, 'Renamed')
    assert repo.by_type(1, 'canvas').name == 'Renamed'
    assert repo.delete(1, first.profile_id)
    assert repo.active(1) is None
    assert repo.active(2).profile_id == peer.profile_id
    assert linked_accounts() is repo


@pytest.mark.parametrize('invalid', [None, '', {'$ne': None}, ['profile']])
def test_profile_selector_requires_a_scalar(profiles, invalid):
    _, repo = profiles
    row = repo.create_active(1, 'School', 'canvas', {'token': 'synthetic'})
    assert not repo.switch(1, invalid)
    assert not repo.rename(1, invalid, 'Wrong')
    assert not repo.delete(1, invalid)
    assert repo.active(1).profile_id == row.profile_id


def test_missing_account_cannot_create_profiles(profiles):
    _, repo = profiles
    with pytest.raises(LookupError):
        repo.create_active(999, 'Deleted', 'canvas', {})
    assert repo.list(999) == []


def test_native_reads_migrated_plaintext_and_compressed_ciphertext(profiles):
    app, repo = profiles
    if not isinstance(repo, MongoLinkedAccounts):
        return
    db = repo.store.database
    raw = json.dumps({'password': 'legacy-synthetic', 'padding': 'x' * 18000})
    db.linked_accounts.insert_many([
        pack_document({'id': 90, 'user_id': 1, 'profile_id': 'legacy', 'login_type': 'canvas',
                       'name': 'Legacy', 'is_active': True, 'credentials': raw,
                       'created_at': '2026-10-18T01:02:03.123456', 'unknown': 'keep'}),
        pack_document({'id': 91, 'user_id': 1, 'profile_id': 'sealed', 'login_type': 'canvas',
                       'name': 'Sealed', 'is_active': False, 'credentials': secret_box.encrypt(raw)}),
    ])
    assert repo.active(1).created_at.microsecond == 123456
    assert repo.by_type(1, 'canvas').profile_id == 'legacy'
    assert repo.list(1)[1].get_credentials() == json.loads(raw)
    assert repo.rename(1, 'legacy', 'Updated')
    assert repo.active(1).get_credentials() == json.loads(raw)
    assert db.linked_accounts.find_one({'id': 90})['unknown'] == 'keep'
    app.extensions.pop('linked_accounts_repository')
    assert isinstance(linked_accounts(), MongoLinkedAccounts)


def connect_canvas(canvas, user_id=1, token='access', refresh='refresh'):
    canvas.connect(user_id, canvas_base='https://school.instructure.com',
                   access_token=token, refresh_token=refresh, expires_at=datetime(2026, 10, 18, 1),
                   canvas_user_id='school-user', canvas_user_name='Student', profile_name='School',
                   credentials={'canvas_url': 'https://school.instructure.com', 'canvas_token': token,
                                'canvas_refresh_token': refresh, 'canvas_oauth': True})


def test_canvas_tokens_and_profile_are_saved_and_rotated_together(profiles):
    app, linked = profiles
    canvas = canvas_integrations()
    connect_canvas(canvas)
    before = canvas.get(1)
    assert before.access_token == 'access'
    assert before.refresh_token == 'refresh'
    assert linked.active(1).get_credentials()['canvas_token'] == 'access'
    expires = datetime(2026, 10, 19, 12, 30, 0, 123456)
    assert canvas.refresh(1, before, access_token='fresh', refresh_token='rotated', expires_at=expires)
    assert canvas.get(1).refresh_token == 'rotated'
    assert canvas.get(1).token_expires_at == expires
    assert linked.active(1).get_credentials()['canvas_token'] == 'fresh'
    assert linked.active(1).get_credentials()['canvas_refresh_token'] == 'rotated'
    assert not canvas.refresh(1, before, access_token='stale', refresh_token=None, expires_at=expires)
    assert canvas.get(1).access_token == 'fresh'
    if isinstance(canvas, MongoCanvas):
        raw = linked.store.database.canvas_integrations.find_one({'user_id': 1})
        assert secret_box.is_encrypted(raw['access_token'])
        assert secret_box.is_encrypted(raw['refresh_token'])
        app.extensions.pop('canvas_repository')
        assert isinstance(canvas_integrations(), MongoCanvas)


def test_late_canvas_refresh_and_disconnect_do_not_overwrite_reconnect(profiles):
    _, linked = profiles
    canvas = canvas_integrations()
    connect_canvas(canvas)
    stale = canvas.get(1)
    connect_canvas(canvas, token='new-connection', refresh='new-refresh')
    assert not canvas.refresh(1, stale, access_token='late-result', refresh_token=None, expires_at=None)
    assert not canvas.disconnect(1, stale)
    assert canvas.get(1).access_token == 'new-connection'
    assert linked.active(1).get_credentials()['canvas_token'] == 'new-connection'


def test_canvas_disconnect_strips_all_owned_profiles_and_preserves_peer(profiles):
    _, linked = profiles
    canvas = canvas_integrations()
    connect_canvas(canvas)
    connect_canvas(canvas, token='second')
    connect_canvas(canvas, user_id=2, token='peer')
    expected = canvas.get(1)
    assert canvas.disconnect(1, expected)
    assert canvas.get(1) is None
    for profile in linked.list(1):
        assert not profile.is_active
        assert profile.get_credentials() == {'canvas_url': 'https://school.instructure.com'}
    assert canvas.get(2).access_token == 'peer'
    assert linked.active(2).get_credentials()['canvas_token'] == 'peer'
    assert not canvas.refresh(1, expected, access_token='late', refresh_token=None, expires_at=None)
    assert canvas.get(1) is None


def test_canvas_refresh_leaves_pasted_connection_credentials_unchanged(profiles):
    _, linked = profiles
    canvas = canvas_integrations()
    connect_canvas(canvas)
    expected = canvas.get(1)
    pasted = linked.create_active(1, 'Pasted', 'canvas', {'canvas_token': 'paste',
                                                      'canvas_url': 'https://other.instructure.com'})
    assert canvas.refresh(1, expected, access_token='fresh', refresh_token=None, expires_at=None)
    assert linked.active(1).profile_id == pasted.profile_id
    assert linked.active(1).get_credentials()['canvas_token'] == 'paste'


def test_canvas_missing_owner_cannot_create_connection(profiles):
    with pytest.raises(LookupError):
        connect_canvas(canvas_integrations(), user_id=999)
    assert canvas_integrations().get(999) is None


def test_native_profile_and_canvas_web_routes_use_injected_repositories(profiles, monkeypatch):
    _, linked = profiles
    if not isinstance(linked, MongoLinkedAccounts):
        return
    import App
    from types import SimpleNamespace
    monkeypatch.setattr(App, 'current_user', SimpleNamespace(id=1, email='synthetic@example.com', is_authenticated=True))
    monkeypatch.setitem(App.app.extensions, 'linked_accounts_repository', linked)
    monkeypatch.setitem(App.app.extensions, 'canvas_repository', canvas_integrations())
    first = linked.create_active(1, 'School', 'studentvue', {'sv_username': 'synthetic'})
    second = linked.create_active(1, 'Other', 'hac', {'hac_username': 'synthetic'})
    with App.app.test_request_context('/profiles/list'):
        assert len(App.profiles_list().get_json()['profiles']) == 2
        assert App.get_active_account()['login_type'] == 'hac'
    with App.app.test_request_context('/profiles/switch', method='POST', json={'id': first.profile_id}):
        assert App.profiles_switch().get_json()['status'] == 'ok'
    with App.app.test_request_context('/profiles/rename', method='POST', json={'id': first.profile_id, 'name': 'Renamed'}):
        assert App.profiles_rename().get_json()['status'] == 'ok'
    assert linked.active(1).name == 'Renamed'
    with App.app.test_request_context('/profiles/delete', method='POST', json={'id': second.profile_id}):
        assert App.profiles_delete().get_json()['status'] == 'ok'
    canvas = App.app.extensions['canvas_repository']
    connect_canvas(canvas)
    monkeypatch.setattr(App, 'CANVAS_OAUTH_AVAILABLE', True)
    monkeypatch.setattr(App, 'refresh_canvas_token', lambda *args: {'access_token': 'web-fresh', 'refresh_token': 'web-rotated', 'expires_in': 3600})
    with App.app.test_request_context('/'):
        assert App.get_active_account()['canvas_token'] == 'web-fresh'
        assert canvas.get(1).refresh_token == 'web-rotated'
        assert App.oauth_canvas_status().get_json()['connected']
    monkeypatch.setattr(App, 'revoke_canvas_token', lambda *args: None)
    with App.app.test_request_context('/oauth/canvas/disconnect', method='POST'):
        assert App.oauth_canvas_disconnect().get_json()['status'] == 'ok'
    assert canvas.get(1) is None
