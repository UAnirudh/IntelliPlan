"""Replay actual signed remember cookies with the session cookie removed."""
from flask import Flask
from flask_login import UserMixin, current_user, login_user
from flask_login.utils import encode_cookie

from intelliplan.persistence.login import EpochLoginManager


class User(UserMixin):
    id = 42
    session_epoch = 0


def make_app():
    app = Flask(__name__)
    app.secret_key = 'synthetic-remember-cookie-test'
    manager = EpochLoginManager(app)
    user = User()
    manager.user_loader(lambda user_id: user if user_id == '42' else None)

    @app.get('/signin')
    def signin():
        login_user(user, remember=True)
        return 'ok'

    @app.get('/who')
    def who():
        return {'authenticated': current_user.is_authenticated}

    return app, user


def test_remember_restoration_retains_numeric_business_id():
    app, _ = make_app()
    client = app.test_client()
    client.get('/signin')
    client.delete_cookie('session')
    assert client.get('/who').json['authenticated'] is True
    with client.session_transaction() as cookie:
        assert cookie['_user_id'] == '42'
        assert cookie['session_epoch'] == 0


def test_replaying_pre_reset_remember_cookie_cannot_restore_authentication():
    app, user = make_app()
    client = app.test_client()
    client.get('/signin')
    remembered = client.get_cookie('remember_token').value
    user.session_epoch = 1
    replay = app.test_client()
    replay.set_cookie('remember_token', remembered)
    assert replay.get('/who').json['authenticated'] is False
    assert replay.get_cookie('remember_token') is None


def test_legacy_remember_cookie_is_rejected_after_a_credential_change():
    app, user = make_app()
    with app.app_context():
        remembered = encode_cookie('42')
    client = app.test_client()
    client.set_cookie('remember_token', remembered)
    assert client.get('/who').json['authenticated'] is True
    user.session_epoch = 1
    replay = app.test_client()
    replay.set_cookie('remember_token', remembered)
    assert replay.get('/who').json['authenticated'] is False
