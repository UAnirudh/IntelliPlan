"""Run the real bearer-auth blueprint with either repository and no App import."""
from datetime import datetime
import time

import mongomock
import pytest
from flask import Flask, g
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin
from flask_sqlalchemy import SQLAlchemy

import auth_api
from intelliplan.persistence.accounts import AccountExists, PasswordConflict, SQLAccounts
from intelliplan.persistence.mongo import MongoStore
from intelliplan.persistence.mongo_accounts import MongoAccounts


@pytest.fixture(params=["mongo", "sql"])
def account_app(request, monkeypatch):
    app = Flask(__name__)
    app.config.update(SECRET_KEY="repository-test-only", TESTING=True,
                      SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
    passwords = Bcrypt(app)
    if request.param == "mongo":
        client = mongomock.MongoClient()
        store = MongoStore("mongodb://test", "accounts", client_factory=lambda *a, **k: client)
        monkeypatch.setattr(store, 'transaction', lambda work: work(store.database, None))
        store.seed_id("users", 1000)
        repo = MongoAccounts(store)
        repo.ensure_indexes()
    else:
        db = SQLAlchemy(app)

        class User(UserMixin, db.Model):
            __tablename__ = "users"
            id = db.Column(db.Integer, primary_key=True)
            email = db.Column(db.String, nullable=False, unique=True)
            name = db.Column(db.String)
            password_hash = db.Column(db.String, default="")
            created_at = db.Column(db.DateTime, default=datetime.utcnow)
            session_epoch = db.Column(db.Integer, default=0)
            failed_login_count = db.Column(db.Integer, default=0)
            login_locked_until = db.Column(db.DateTime)

        class ResetToken(db.Model):
            __tablename__ = 'password_reset_tokens'
            id = db.Column(db.Integer, primary_key=True)
            user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
            token_hash = db.Column(db.String, unique=True, nullable=False)
            expires_at = db.Column(db.DateTime, nullable=False)
            used_at = db.Column(db.DateTime)
            created_at = db.Column(db.DateTime, nullable=False)
            requested_ip = db.Column(db.String)

        with app.app_context():
            db.create_all()
        repo = SQLAccounts(db, User, reset_model=ResetToken)
    app.extensions["accounts_repository"] = repo
    app.extensions["account_passwords"] = passwords
    manager = LoginManager(app)
    manager.user_loader(repo.get)
    app.register_blueprint(auth_api.auth_bp)

    @app.before_request
    def clear_cached_login():
        # The repository tests retain an app context for direct SQL assertions;
        # real requests have independent contexts and independent login caches.
        g.pop("_login_user", None)

    def no_legacy_models():
        raise AssertionError("Native account routes imported the legacy app")

    monkeypatch.setattr(auth_api, "_models", no_legacy_models)
    with app.app_context():
        yield app, repo, passwords


def test_register_login_me_and_duplicate_identity(account_app):
    app, repo, _ = account_app
    client = app.test_client()
    response = client.post("/api/auth/register", json={
        "email": " Student@Example.test ", "password": "a-strong-password", "name": "Student"})
    assert response.status_code == 201
    data = response.json
    user_id = data["user"]["id"]
    assert repo.get(user_id).email == "student@example.test"
    assert "password_hash" not in data["user"]
    assert client.post("/api/auth/register", json={
        "email": "student@example.test", "password": "a-strong-password"}).status_code == 409
    login = client.post("/api/ext/login", json={
        "email": "STUDENT@example.test", "password": "a-strong-password"})
    assert login.status_code == 200
    me = client.get("/api/ext/me", headers={"Authorization": f"Bearer {login.json['token']}"})
    assert me.status_code == 200
    assert me.json["user"]["id"] == user_id
    assert repo.count() == 1


def test_passwordless_and_unknown_accounts_cannot_get_password_tokens(account_app):
    app, repo, _ = account_app
    repo.create(email="oauth@example.test", name="OAuth", password_hash="")
    for email in ("oauth@example.test", "unknown@example.test"):
        result = app.test_client().post("/api/auth/login", json={"email": email, "password": "guess"})
        assert result.status_code == 401


def test_password_compare_and_swap_revokes_other_cookie_sessions(account_app):
    app, repo, passwords = account_app
    user = repo.create(email="password@example.test", name="Student",
                       password_hash=passwords.generate_password_hash("old-password").decode())
    old_hash = user.password_hash
    changed = repo.change_password(user.id, expected_hash=old_hash, password_hash="new-hash")
    assert changed.session_epoch == 1
    with pytest.raises(PasswordConflict):
        repo.change_password(user.id, expected_hash=old_hash, password_hash="stale-overwrite")
    assert repo.get(user.id).password_hash == "new-hash"
    assert repo.get(user.id).session_epoch == 1


def test_oauth_password_requires_recent_cookie_authentication(account_app):
    app, repo, _ = account_app
    user = repo.create(email="oauth@example.test", name="Student", password_hash="")
    client = app.test_client()
    assert client.post("/api/auth/password", json={"new_password": "strong-password"}).status_code == 401
    with client.session_transaction() as cookie:
        cookie["_user_id"] = str(user.id)
        cookie["_fresh"] = True
    assert client.post("/api/auth/password", json={"new_password": "strong-password"}).status_code == 401
    with client.session_transaction() as cookie:
        cookie["auth_time"] = time.time()
    assert client.post("/api/auth/password", json={"new_password": "strong-password"}).status_code == 200
    with client.session_transaction() as cookie:
        assert cookie["session_epoch"] == 1


def test_duplicate_create_is_a_repository_error(account_app):
    _, repo, _ = account_app
    repo.create(email="same@example.test", name="One", password_hash="hash")
    with pytest.raises(AccountExists):
        repo.create(email="SAME@example.test", name="Two", password_hash="hash")


def test_reset_is_single_use_and_changes_password_and_epoch_together(account_app):
    _, repo, _ = account_app
    user = repo.create(email='reset@example.test', name='Reset', password_hash='original')
    repo.issue_reset(user.id, token_hash='hashed-token', requested_ip='127.0.0.1')
    assert repo.recent_resets(user.id) == 1
    assert repo.reset_usable('hashed-token')
    changed = repo.complete_reset('hashed-token', password_hash='reset-password')
    assert changed.user_id == user.id
    assert changed.session_epoch == 1
    assert repo.get(user.id).password_hash == 'reset-password'
    assert not repo.reset_usable('hashed-token')
    assert repo.complete_reset('hashed-token', password_hash='replayed-password') is None
    assert repo.get(user.id).password_hash == 'reset-password'
    assert repo.get(user.id).session_epoch == 1


def test_new_reset_invalidates_older_links_and_expired_tokens_do_not_write(account_app):
    _, repo, _ = account_app
    user = repo.create(email='reset@example.test', name='Reset', password_hash='original')
    repo.issue_reset(user.id, token_hash='first-hash', requested_ip='127.0.0.1')
    repo.issue_reset(user.id, token_hash='second-hash', requested_ip='127.0.0.1')
    assert repo.recent_resets(user.id) == 2
    assert not repo.reset_usable('first-hash')
    assert repo.reset_usable('second-hash')
    assert repo.complete_reset('first-hash', password_hash='stale') is None
    repo.issue_reset(user.id, token_hash='expired-hash', requested_ip='127.0.0.1', ttl_minutes=-1)
    assert not repo.reset_usable('second-hash')
    assert not repo.reset_usable('expired-hash')
    assert repo.complete_reset('expired-hash', password_hash='expired') is None
    assert repo.get(user.id).password_hash == 'original'


def test_later_reset_cannot_restamp_stale_browser(account_app, monkeypatch):
    app, repo, _ = account_app
    user = repo.create(email="race@example.test", name="Student", password_hash="")
    original = repo.change_password

    def concurrent_reset(user_id, *, expected_hash, password_hash):
        first = original(user_id, expected_hash=expected_hash, password_hash=password_hash)
        original(user_id, expected_hash=password_hash, password_hash="later-reset-hash")
        return first

    monkeypatch.setattr(repo, "change_password", concurrent_reset)
    client = app.test_client()
    with client.session_transaction() as cookie:
        cookie["_user_id"] = str(user.id)
        cookie["_fresh"] = True
        cookie["auth_time"] = time.time()
    assert client.post("/api/auth/password", json={"new_password": "strong-password"}).status_code == 200
    with client.session_transaction() as cookie:
        assert cookie["session_epoch"] == 1
    assert repo.get(user.id).session_epoch == 2


def test_migrated_account_keeps_all_fields_and_precise_dates():
    client = mongomock.MongoClient()
    store = MongoStore("mongodb://test", "accounts", client_factory=lambda *a, **k: client)
    store.database.users.insert_one({"id": 42, "email": "migrated@example.test",
        "password_hash": "original-hash", "session_epoch": None,
        "created_at": "2026-01-01T12:34:56.123456", "marketing_emails_opt_in": True,
        "marketing_consent_version": "original-version", "custom_legacy_field": "keep"})
    repo = MongoAccounts(store)
    repo.ensure_indexes()
    user = repo.get("42")
    assert user.get_id() == "42"
    assert user.created_at.microsecond == 123456
    assert user.marketing_emails_opt_in is True
    assert user.custom_legacy_field == "keep"
    changed = repo.change_password(42, expected_hash="original-hash", password_hash="new")
    assert changed.session_epoch == 1
    assert repo.get(42).custom_legacy_field == "keep"
    assert repo.get(42).marketing_consent_version == "original-version"
