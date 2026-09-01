"""Keeping an OAuth Canvas connection alive.

Canvas access tokens last about an hour. The OAuth callback stored one and
mirrored it onto the linked account, and nothing refreshed it -- the helper
was imported and never called. An hour after connecting, every sync failed
with a 401 that the page reported as "no assignments found", which reads as
a broken integration rather than an aged-out token.

Every Canvas read goes through get_active_account(), so that is where the
refresh belongs and what these tests exercise. Nothing here touches the
network: the token endpoint is stubbed throughout.
"""

from datetime import timedelta

import pytest

import App
from App import (CanvasIntegration, LinkedAccount, User, bcrypt, db, utcnow)

PASSWORD = "canvas-refresh-pw"


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            db.create_all()
            _wipe()
        yield c
        with App.app.app_context():
            _wipe()
    App.limiter.enabled = True


def _wipe():
    ids = [u.id for u in User.query.filter(User.email.like("cx+%")).all()]
    if ids:
        CanvasIntegration.query.filter(
            CanvasIntegration.user_id.in_(ids)).delete(synchronize_session=False)
        LinkedAccount.query.filter(
            LinkedAccount.user_id.in_(ids)).delete(synchronize_session=False)
    User.query.filter(User.email.like("cx+%")).delete(synchronize_session=False)
    db.session.commit()


def _connected_student(expires_in_seconds, access_token="old-token"):
    """A student with a Canvas OAuth connection whose token expires when told."""
    with App.app.app_context():
        user = User(email="cx+a@example.com",
                    password_hash=bcrypt.generate_password_hash(PASSWORD).decode())
        db.session.add(user)
        db.session.commit()
        expires = (utcnow() + timedelta(seconds=expires_in_seconds)
                   if expires_in_seconds is not None else None)
        db.session.add(CanvasIntegration(
            user_id=user.id, canvas_base="https://school.instructure.com",
            access_token=access_token, refresh_token="refresh-abc",
            token_expires_at=expires, canvas_user_name="Sam"))
        acct = LinkedAccount(user_id=user.id, login_type="canvas", is_active=True)
        acct.set_credentials({
            "canvas_token": access_token,
            "canvas_url": "https://school.instructure.com",
            "canvas_oauth": True,
        })
        db.session.add(acct)
        db.session.commit()
        return user.id


def _sign_in(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


@pytest.fixture
def refreshes(monkeypatch):
    calls = []

    def fake(refresh_token, canvas_base):
        calls.append((refresh_token, canvas_base))
        return {"access_token": "fresh-token", "token_type": "Bearer", "expires_in": 3600}

    monkeypatch.setattr(App, "refresh_canvas_token", fake)
    monkeypatch.setattr(App, "CANVAS_OAUTH_AVAILABLE", True)
    return calls


@pytest.fixture
def refresh_rejected(monkeypatch):
    def fake(refresh_token, canvas_base):
        raise Exception("Canvas token refresh failed: {'error': 'invalid_grant'}")

    monkeypatch.setattr(App, "refresh_canvas_token", fake)
    monkeypatch.setattr(App, "CANVAS_OAUTH_AVAILABLE", True)


# ── Refresh ───────────────────────────────────────────────────────

def test_an_expiring_token_is_refreshed_before_it_is_used(client, refreshes):
    uid = _connected_student(expires_in_seconds=30)
    _sign_in(client, uid)
    with App.app.test_request_context("/"):
        from flask_login import login_user
        login_user(User.query.get(uid))
        creds = App.get_active_account()
    assert creds["canvas_token"] == "fresh-token"
    assert len(refreshes) == 1


def test_a_healthy_token_is_left_alone(client, refreshes):
    """Refreshing on every read would burn the rate limit for nothing."""
    uid = _connected_student(expires_in_seconds=3000)
    with App.app.test_request_context("/"):
        from flask_login import login_user
        login_user(User.query.get(uid))
        creds = App.get_active_account()
    assert creds["canvas_token"] == "old-token"
    assert refreshes == []


def test_a_token_with_no_expiry_recorded_is_refreshed(client, refreshes):
    """An unknown expiry is not evidence of freshness."""
    uid = _connected_student(expires_in_seconds=None)
    with App.app.test_request_context("/"):
        from flask_login import login_user
        login_user(User.query.get(uid))
        creds = App.get_active_account()
    assert creds["canvas_token"] == "fresh-token"


def test_the_refreshed_token_is_persisted_where_the_fetch_paths_read_it(client, refreshes):
    """Updating only the integration row would leave every sync on the stale
    token: the fetch paths read the linked account."""
    uid = _connected_student(expires_in_seconds=10)
    with App.app.test_request_context("/"):
        from flask_login import login_user
        login_user(User.query.get(uid))
        App.get_active_account()
    with App.app.app_context():
        row = CanvasIntegration.query.filter_by(user_id=uid).first()
        acct = LinkedAccount.query.filter_by(user_id=uid, is_active=True).first()
        assert row.access_token == "fresh-token"
        assert row.token_expires_at > utcnow() + timedelta(minutes=50)
        assert acct.get_credentials()["canvas_token"] == "fresh-token"


def test_a_revoked_grant_is_marked_for_reconnect_rather_than_retried(client, refresh_rejected):
    uid = _connected_student(expires_in_seconds=10)
    _sign_in(client, uid)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(uid)
    with App.app.test_request_context("/"):
        from flask import session
        from flask_login import login_user
        login_user(User.query.get(uid))
        creds = App.get_active_account()
        assert session.get("canvas_needs_reconnect") is True
    # The stale token is returned rather than None: the caller decides what to
    # show, and a missing account looks like "never connected".
    assert creds["canvas_token"] == "old-token"


def test_a_pasted_token_connection_is_never_refreshed(client, refreshes):
    """The legacy paste flow has no refresh token and no expiry."""
    with App.app.app_context():
        user = User(email="cx+paste@example.com",
                    password_hash=bcrypt.generate_password_hash(PASSWORD).decode())
        db.session.add(user)
        db.session.commit()
        acct = LinkedAccount(user_id=user.id, login_type="canvas", is_active=True)
        acct.set_credentials({"canvas_token": "pasted", "canvas_url": "https://x.instructure.com"})
        db.session.add(acct)
        db.session.commit()
        uid = user.id
    with App.app.test_request_context("/"):
        from flask_login import login_user
        login_user(User.query.get(uid))
        creds = App.get_active_account()
    assert creds["canvas_token"] == "pasted"
    assert refreshes == []


# ── Status ────────────────────────────────────────────────────────

def test_status_distinguishes_never_connected_from_revoked(client, refreshes):
    assert client.get("/oauth/canvas/status").get_json()["connected"] is False

    uid = _connected_student(expires_in_seconds=3000)
    _sign_in(client, uid)
    body = client.get("/oauth/canvas/status").get_json()
    assert body["connected"] is True
    assert body["canvas_base"] == "https://school.instructure.com"
    assert body["needs_reconnect"] is False
