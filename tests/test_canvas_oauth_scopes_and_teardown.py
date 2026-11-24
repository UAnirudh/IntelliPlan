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
import os
from urllib.parse import parse_qs

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
    for name in list(os.environ):
        if name.startswith("CANVAS_CLIENT_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CANVAS_CLIENT_ID", "1000")
    monkeypatch.setenv("CANVAS_CLIENT_SECRET", "secret")
    monkeypatch.delenv("CANVAS_SCOPES", raising=False)


@pytest.fixture
def school_keys(keys, monkeypatch):
    suffix = canvas_oauth._host_key_suffix(BASE)
    monkeypatch.setenv(f"CANVAS_CLIENT_ID_ORIGIN_{suffix}", "2000")
    monkeypatch.setenv(f"CANVAS_CLIENT_SECRET_ORIGIN_{suffix}", "school-secret")


# ── Scopes ────────────────────────────────────────────────────────

def test_an_unscoped_key_is_asked_unscoped(school_keys):
    """Canvas rejects a scope param on a key with enforcement off, so the
    default must stay silent about scopes."""
    url = canvas_oauth.get_canvas_auth_url("st", canvas_base=BASE)
    assert "scope=" not in url


def test_turning_scopes_on_asks_for_the_endpoints_we_call(school_keys, monkeypatch):
    monkeypatch.setenv("CANVAS_SCOPES", "default")
    url = canvas_oauth.get_canvas_auth_url("st", canvas_base=BASE)
    assert "scope=" in url
    for endpoint in ("/api/v1/courses", "assignments", "enrollments"):
        assert endpoint.replace("/", "%2F") in url or endpoint in url


def test_an_explicit_scope_list_is_sent_verbatim(keys, monkeypatch):
    monkeypatch.setenv("CANVAS_SCOPES", "url:GET|/api/v1/courses, url:GET|/api/v1/users/:id")
    scopes = canvas_oauth.configured_scopes()
    assert scopes == ("url:GET|/api/v1/courses", "url:GET|/api/v1/users/:id")


def test_one_school_can_enforce_scopes_while_the_public_canvas_does_not(school_keys, monkeypatch):
    """Enforcement is a per-instance decision, so the override has to be too."""
    monkeypatch.setattr(canvas_oauth, "DEFAULT_CANVAS_BASE", "https://canvas.instructure.com")
    monkeypatch.setenv(
        f"CANVAS_SCOPES_ORIGIN_{canvas_oauth._host_key_suffix(BASE)}", "default")
    scoped = canvas_oauth.get_canvas_auth_url("st", canvas_base=BASE)
    public = canvas_oauth.get_canvas_auth_url("st", canvas_base="https://canvas.instructure.com")
    assert "scope=" in scoped
    assert "scope=" not in public


def test_an_explicit_argument_still_wins_over_the_environment(school_keys, monkeypatch):
    monkeypatch.setenv("CANVAS_SCOPES", "default")
    url = canvas_oauth.get_canvas_auth_url("st", canvas_base=BASE, scopes=["url:GET|/api/v1/only"])
    assert "only" in url
    assert "assignment_groups" not in url


def test_global_credentials_are_reserved_for_the_default_canvas(keys):
    assert canvas_oauth._client_credentials(canvas_oauth.DEFAULT_CANVAS_BASE) == (
        "1000", "secret")
    assert canvas_oauth.oauth_is_configured(canvas_oauth.DEFAULT_CANVAS_BASE)


def test_code_exchange_uses_pinned_https_with_encoded_form(keys, monkeypatch):
    captured = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"access_token": "access", "refresh_token": "refresh"}

    def fake_request(url, method, **kwargs):
        captured.update(url=url, method=method, **kwargs)
        return Response()

    monkeypatch.setattr(canvas_oauth.net_guard, "pinned_https_request", fake_request)
    tokens = canvas_oauth.exchange_canvas_code("code-value", canvas_oauth.DEFAULT_CANVAS_BASE)

    assert tokens["access_token"] == "access"
    assert captured["method"] == "POST"
    assert captured["url"] == f"{canvas_oauth.DEFAULT_CANVAS_BASE}/login/oauth2/token"
    assert captured["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert parse_qs(captured["data"])["client_secret"] == ["secret"]
    assert parse_qs(captured["data"])["code"] == ["code-value"]


def test_refresh_and_revoke_use_pinned_https(keys, monkeypatch):
    calls = []

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"access_token": "new-access"}

    def fake_request(url, method, **kwargs):
        calls.append((url, method, kwargs))
        return Response()

    monkeypatch.setattr(canvas_oauth.net_guard, "pinned_https_request", fake_request)
    refreshed = canvas_oauth.refresh_canvas_token("refresh", canvas_oauth.DEFAULT_CANVAS_BASE)
    canvas_oauth.revoke_canvas_token("access", canvas_oauth.DEFAULT_CANVAS_BASE)

    assert refreshed["access_token"] == "new-access"
    assert [call[1] for call in calls] == ["POST", "DELETE"]
    assert parse_qs(calls[0][2]["data"])["client_secret"] == ["secret"]
    assert calls[1][2]["headers"]["Authorization"] == "Bearer access"


def test_global_credentials_do_not_configure_an_unregistered_custom_host(keys):
    assert canvas_oauth._client_credentials(BASE) == (None, None)
    assert not canvas_oauth.oauth_is_configured(BASE)


def test_a_complete_custom_host_pair_is_used_instead_of_global(keys, monkeypatch):
    suffix = canvas_oauth._host_key_suffix(BASE)
    monkeypatch.setenv(f"CANVAS_CLIENT_ID_ORIGIN_{suffix}", "2000")
    monkeypatch.setenv(f"CANVAS_CLIENT_SECRET_ORIGIN_{suffix}", "school-secret")
    assert canvas_oauth._client_credentials(BASE) == ("2000", "school-secret")


@pytest.mark.parametrize("partial_name,partial_value", [
    ("CANVAS_CLIENT_ID_ORIGIN_", "2000"),
    ("CANVAS_CLIENT_SECRET_ORIGIN_", "school-secret"),
])
def test_a_partial_custom_pair_never_borrows_global_credentials(
        keys, monkeypatch, partial_name, partial_value):
    monkeypatch.setenv(partial_name + canvas_oauth._host_key_suffix(BASE), partial_value)
    cid, secret = canvas_oauth._client_credentials(BASE)
    assert (cid, secret) != ("2000", "secret")
    assert not canvas_oauth.oauth_is_configured(BASE)


def test_origin_credential_names_do_not_collide_for_dot_and_hyphen_hosts(keys, monkeypatch):
    configured = "https://canvas.school.edu"
    impostor = "https://canvas-school.edu"
    configured_suffix = canvas_oauth._host_key_suffix(configured)
    impostor_suffix = canvas_oauth._host_key_suffix(impostor)
    monkeypatch.setenv(f"CANVAS_CLIENT_ID_ORIGIN_{configured_suffix}", "school-id")
    monkeypatch.setenv(f"CANVAS_CLIENT_SECRET_ORIGIN_{configured_suffix}", "school-secret")
    assert configured_suffix != impostor_suffix
    assert canvas_oauth._client_credentials(configured) == ("school-id", "school-secret")
    assert canvas_oauth._client_credentials(impostor) == (None, None)


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
