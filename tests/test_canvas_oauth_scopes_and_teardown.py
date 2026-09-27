"""Three gaps in the Canvas OAuth flow that each looked like something else.

1. Scoped Developer Keys. A school admin who turns on "Enforce Scopes" before
   approving IntelliPlan gets an authorization Canvas rejects, because the
   request named no scopes. The fix is opt-in: sending scopes by default would
   break the unscoped public-Canvas key, which Canvas rejects a scope param on.

2. Disconnect. The connection is recorded twice -- the refresh token in
   CanvasIntegration, the live access token mirrored onto LinkedAccount -- and
   only the first was removed. A student who pressed Disconnect stayed
   connected until the mirrored token aged out on its own.

3. Guests. A student can connect Canvas before signing up. Their refresh token
   was stored in the session and never used, so the signed-out path still had
   the exact hour-long lifetime the refresh work was meant to end.

Nothing here touches the network.
"""

from datetime import timedelta

import pytest

import App
import canvas_oauth
from App import CanvasIntegration, LinkedAccount, User, bcrypt, db, utcnow

PASSWORD = "canvas-scope-pw"
BASE = "https://school.instructure.com"


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
    ids = [u.id for u in User.query.filter(User.email.like("cs+%")).all()]
    if ids:
        CanvasIntegration.query.filter(
            CanvasIntegration.user_id.in_(ids)).delete(synchronize_session=False)
        LinkedAccount.query.filter(
            LinkedAccount.user_id.in_(ids)).delete(synchronize_session=False)
    User.query.filter(User.email.like("cs+%")).delete(synchronize_session=False)
    db.session.commit()


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("CANVAS_CLIENT_ID", "1000")
    monkeypatch.setenv("CANVAS_CLIENT_SECRET", "secret")
    monkeypatch.delenv("CANVAS_SCOPES", raising=False)


# ── Scopes ────────────────────────────────────────────────────────

def test_an_unscoped_key_is_asked_unscoped(keys):
    """Canvas rejects a scope param on a key with enforcement off, so the
    default must stay silent about scopes."""
    url = canvas_oauth.get_canvas_auth_url("st", canvas_base=BASE)
    assert "scope=" not in url


def test_turning_scopes_on_asks_for_the_endpoints_we_call(keys, monkeypatch):
    monkeypatch.setenv("CANVAS_SCOPES", "default")
    url = canvas_oauth.get_canvas_auth_url("st", canvas_base=BASE)
    assert "scope=" in url
    for endpoint in ("/api/v1/courses", "assignments", "enrollments"):
        assert endpoint.replace("/", "%2F") in url or endpoint in url


def test_an_explicit_scope_list_is_sent_verbatim(keys, monkeypatch):
    monkeypatch.setenv("CANVAS_SCOPES", "url:GET|/api/v1/courses, url:GET|/api/v1/users/:id")
    scopes = canvas_oauth.configured_scopes()
    assert scopes == ("url:GET|/api/v1/courses", "url:GET|/api/v1/users/:id")


def test_one_school_can_enforce_scopes_while_the_public_canvas_does_not(keys, monkeypatch):
    """Enforcement is a per-instance decision, so the override has to be too."""
    monkeypatch.setenv("CANVAS_SCOPES_SCHOOL_INSTRUCTURE_COM", "default")
    scoped = canvas_oauth.get_canvas_auth_url("st", canvas_base=BASE)
    public = canvas_oauth.get_canvas_auth_url("st", canvas_base="https://canvas.instructure.com")
    assert "scope=" in scoped
    assert "scope=" not in public


def test_an_explicit_argument_still_wins_over_the_environment(keys, monkeypatch):
    monkeypatch.setenv("CANVAS_SCOPES", "default")
    url = canvas_oauth.get_canvas_auth_url("st", canvas_base=BASE, scopes=["url:GET|/api/v1/only"])
    assert "only" in url
    assert "assignment_groups" not in url


# ── Disconnect ────────────────────────────────────────────────────

def _connected_student():
    with App.app.app_context():
        user = User(email="cs+a@example.com",
                    password_hash=bcrypt.generate_password_hash(PASSWORD).decode())
        db.session.add(user)
        db.session.commit()
        db.session.add(CanvasIntegration(
            user_id=user.id, canvas_base=BASE, access_token="live-token",
            refresh_token="refresh-abc",
            token_expires_at=utcnow() + timedelta(seconds=3000)))
        acct = LinkedAccount(user_id=user.id, login_type="canvas", is_active=True)
        acct.set_credentials({
            "canvas_token": "live-token", "canvas_url": BASE, "canvas_oauth": True,
            "canvas_refresh_token": "refresh-abc",
        })
        db.session.add(acct)
        db.session.commit()
        return user.id


def test_disconnecting_leaves_no_usable_token_behind(client, monkeypatch):
    monkeypatch.setattr(App, "revoke_canvas_token", lambda *a, **k: None)
    uid = _connected_student()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True

    assert client.post("/oauth/canvas/disconnect").status_code == 200

    with App.app.app_context():
        assert CanvasIntegration.query.filter_by(user_id=uid).first() is None
        for acct in LinkedAccount.query.filter_by(user_id=uid).all():
            assert acct.is_active is False
            assert "canvas_token" not in acct.get_credentials()


def test_a_disconnected_student_is_not_still_reported_as_connected(client, monkeypatch):
    """The whole point of the bug: the app kept serving Canvas data from the
    mirrored token after the student asked it to stop."""
    monkeypatch.setattr(App, "revoke_canvas_token", lambda *a, **k: None)
    uid = _connected_student()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True
    client.post("/oauth/canvas/disconnect")

    with App.app.test_request_context("/"):
        from flask_login import login_user
        login_user(db.session.get(User, uid))
        assert App.get_active_account() is None
    assert client.get("/oauth/canvas/status").get_json()["connected"] is False


# ── Guest refresh ─────────────────────────────────────────────────

@pytest.fixture
def refreshes(monkeypatch):
    calls = []

    def fake(refresh_token, canvas_base):
        calls.append((refresh_token, canvas_base))
        return {"access_token": "fresh-token", "token_type": "Bearer", "expires_in": 3600}

    monkeypatch.setattr(App, "refresh_canvas_token", fake)
    monkeypatch.setattr(App, "CANVAS_OAUTH_AVAILABLE", True)
    return calls


def _guest_session(expires_in_seconds):
    sess = {
        "login_type": "canvas",
        "canvas_token": "old-token",
        "canvas_url": BASE,
        "canvas_refresh_token": "refresh-abc",
        "canvas_oauth": True,
    }
    if expires_in_seconds is not None:
        sess["canvas_token_expires_at"] = (
            utcnow() + timedelta(seconds=expires_in_seconds)).isoformat()
    return sess


def test_a_guests_expiring_token_is_refreshed(refreshes):
    with App.app.test_request_context("/"):
        from flask import session
        session.update(_guest_session(expires_in_seconds=30))
        creds = App.get_active_account()
        assert creds["canvas_token"] == "fresh-token"
        assert session["canvas_token"] == "fresh-token"
    assert len(refreshes) == 1


def test_a_guests_healthy_token_is_left_alone(refreshes):
    with App.app.test_request_context("/"):
        from flask import session
        session.update(_guest_session(expires_in_seconds=3000))
        creds = App.get_active_account()
        assert creds["canvas_token"] == "old-token"
    assert refreshes == []


def test_a_guest_who_pasted_a_token_is_never_refreshed(refreshes):
    """The paste flow has no refresh token; trying would be a wasted call."""
    with App.app.test_request_context("/"):
        from flask import session
        session.update({"login_type": "canvas", "canvas_token": "pasted",
                        "canvas_url": BASE})
        creds = App.get_active_account()
        assert creds["canvas_token"] == "pasted"
    assert refreshes == []


def test_a_guests_revoked_grant_clears_the_connection(monkeypatch):
    """There is no reconnect banner on the signed-out path, so a dead grant
    is cleared rather than left looking connected."""
    def fake(refresh_token, canvas_base):
        raise Exception("Canvas token refresh failed: {'error': 'invalid_grant'}")

    monkeypatch.setattr(App, "refresh_canvas_token", fake)
    monkeypatch.setattr(App, "CANVAS_OAUTH_AVAILABLE", True)
    with App.app.test_request_context("/"):
        from flask import session
        session.update(_guest_session(expires_in_seconds=10))
        App.get_active_account()
        assert "canvas_refresh_token" not in session
        assert "canvas_oauth" not in session


def test_an_offset_aware_expiry_from_an_older_session_does_not_raise(refreshes):
    """utcnow() is naive; an aware value read back would raise on compare."""
    with App.app.test_request_context("/"):
        from flask import session
        sess = _guest_session(expires_in_seconds=3000)
        sess["canvas_token_expires_at"] = (
            utcnow() + timedelta(seconds=3000)).isoformat() + "+00:00"
        session.update(sess)
        creds = App.get_active_account()
    assert creds["canvas_token"] == "old-token"
