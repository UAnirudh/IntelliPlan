"""Notion OAuth: install, token rotation, and the paths that are not faults.

The install flow already worked. What it did not do was survive Notion
expiring a token: the response's ``refresh_token`` was discarded, so a
rotated credential could never be renewed and the connection died silently.
It also treated a cancelled install as a state-mismatch error page.

Nothing here touches the network — the token endpoint is patched at
``notion_helper.http_requests.post``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import App
import notion_helper
from App import NotionIntegration, User, db
from time_utils import utcnow


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("NOTION_CLIENT_ID", "client-id-123")
    monkeypatch.setenv("NOTION_CLIENT_SECRET", "client-secret-456")


@pytest.fixture
def client(creds):
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            NotionIntegration.query.delete()
            User.query.filter(User.email.like("notion+%")).delete(
                synchronize_session=False)
            db.session.commit()
        yield c
        with App.app.app_context():
            NotionIntegration.query.delete()
            User.query.filter(User.email.like("notion+%")).delete(
                synchronize_session=False)
            db.session.commit()
    App.limiter.enabled = True


@pytest.fixture
def signed_in(client):
    with App.app.app_context():
        u = User(email="notion+a@example.com",
                 password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
                 name="Notion Tester")
        db.session.add(u)
        db.session.commit()
        uid = u.id
    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
    return uid


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._payload


@pytest.fixture
def token_endpoint(monkeypatch):
    """Capture token-endpoint calls; return whatever the test queues."""
    calls = []
    queue = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers or {}, "body": json or {}})
        return queue.pop(0) if queue else FakeResponse({
            "access_token": "tok_access", "refresh_token": "tok_refresh",
            "expires_in": 3600, "workspace_id": "ws_1",
            "workspace_name": "Sam's Workspace", "bot_id": "bot_1",
        })

    monkeypatch.setattr(notion_helper.http_requests, "post", fake_post)
    return {"calls": calls, "queue": queue}


# ── The authorization URL ─────────────────────────────────────────────


def test_the_authorize_url_carries_everything_notion_requires(creds):
    url = notion_helper.get_notion_auth_url(
        "state-abc", redirect_uri="https://intelliplan.tech/oauth/notion/callback")
    assert url.startswith("https://api.notion.com/v1/oauth/authorize?")
    for required in ("client_id=client-id-123", "response_type=code",
                     "owner=user", "state=state-abc"):
        assert required in url, f"missing {required} in {url}"
    assert "redirect_uri=https%3A%2F%2Fintelliplan.tech%2Foauth%2Fnotion%2Fcallback" in url


def test_starting_the_flow_without_credentials_is_a_clear_refusal(monkeypatch):
    monkeypatch.delenv("NOTION_CLIENT_ID", raising=False)
    with pytest.raises(RuntimeError, match="NOTION_CLIENT_ID"):
        notion_helper.get_notion_auth_url("state")


# ── The token exchange ────────────────────────────────────────────────


def test_the_exchange_uses_basic_auth_over_the_client_credentials(creds, token_endpoint):
    import base64

    notion_helper.exchange_notion_code("code-1", redirect_uri="https://x.test/cb")
    header = token_endpoint["calls"][0]["headers"]["Authorization"]
    assert header.startswith("Basic ")
    decoded = base64.b64decode(header.split(" ", 1)[1]).decode()
    assert decoded == "client-id-123:client-secret-456"


def test_the_exchange_sends_the_documented_body(creds, token_endpoint):
    notion_helper.exchange_notion_code("code-1", redirect_uri="https://x.test/cb")
    body = token_endpoint["calls"][0]["body"]
    assert body == {
        "grant_type": "authorization_code",
        "code": "code-1",
        "redirect_uri": "https://x.test/cb",
    }


def test_the_refresh_token_is_kept(creds, token_endpoint):
    """The whole point of this change: it used to be dropped."""
    out = notion_helper.exchange_notion_code("code-1", redirect_uri="https://x.test/cb")
    assert out["refresh_token"] == "tok_refresh"
    assert out["expires_at"] is not None


def test_a_token_with_no_expiry_is_not_treated_as_expiring(creds, token_endpoint):
    """Connections predating expiring tokens have no expiry and must keep
    working rather than being refreshed on every single request."""
    token_endpoint["queue"].append(FakeResponse({"access_token": "tok", "workspace_id": "ws"}))
    out = notion_helper.exchange_notion_code("code-1", redirect_uri="https://x.test/cb")
    assert out["expires_at"] is None
    assert notion_helper.token_needs_refresh(out["expires_at"]) is False


def test_an_error_body_is_raised_rather_than_returned_as_a_token(creds, token_endpoint):
    token_endpoint["queue"].append(
        FakeResponse({"error": "invalid_grant"}, status=400))
    with pytest.raises(Exception, match="invalid_grant"):
        notion_helper.exchange_notion_code("bad", redirect_uri="https://x.test/cb")


# ── Refresh ───────────────────────────────────────────────────────────


def test_refreshing_sends_the_refresh_grant(creds, token_endpoint):
    notion_helper.refresh_notion_token("old_refresh")
    body = token_endpoint["calls"][0]["body"]
    assert body == {"grant_type": "refresh_token", "refresh_token": "old_refresh"}


def test_refreshing_without_a_token_is_refused_before_the_network(creds, token_endpoint):
    with pytest.raises(RuntimeError):
        notion_helper.refresh_notion_token("")
    assert token_endpoint["calls"] == []


@pytest.mark.parametrize("delta_seconds,expected", [
    (-60, True),      # already expired
    (60, True),       # inside the skew
    (3600, False),    # comfortably valid
])
def test_the_refresh_window_opens_before_expiry(delta_seconds, expected):
    when = utcnow() + timedelta(seconds=delta_seconds)
    assert notion_helper.token_needs_refresh(when) is expected


# ── The callback, end to end ──────────────────────────────────────────


def _install(client, uid, state="st-1"):
    with client.session_transaction() as s:
        s["notion_oauth_state"] = state
    return client.get(f"/oauth/notion/callback?code=abc&state={state}")


def test_a_completed_install_stores_the_whole_token_pair(client, signed_in, token_endpoint):
    r = _install(client, signed_in)
    assert r.status_code == 302

    with App.app.app_context():
        row = NotionIntegration.query.filter_by(user_id=signed_in).one()
        assert row.token == "tok_access"
        assert row.refresh_token == "tok_refresh"
        assert row.token_expires_at is not None
        assert row.auth_type == "oauth"
        assert row.workspace_name == "Sam's Workspace"


def test_duplicating_the_template_selects_it_as_the_task_database(client, signed_in, token_endpoint):
    """The user already answered "use this one" by duplicating it; asking
    them to pick it from a list afterwards repeats the question."""
    token_endpoint["queue"].append(FakeResponse({
        "access_token": "tok_access", "refresh_token": "tok_refresh",
        "expires_in": 3600, "workspace_id": "ws_1",
        "duplicated_template_id": "page_dup_1",
    }))
    _install(client, signed_in)

    with App.app.app_context():
        row = NotionIntegration.query.filter_by(user_id=signed_in).one()
        assert row.duplicated_template_id == "page_dup_1"
        assert row.database_id == "page_dup_1"


def test_cancelling_the_install_is_not_an_error_page(client, signed_in, token_endpoint):
    with client.session_transaction() as s:
        s["notion_oauth_state"] = "st-1"
    r = client.get("/oauth/notion/callback?error=access_denied&state=st-1")
    assert r.status_code == 302
    assert "notion_error=access_denied" in r.headers["Location"]
    with App.app.app_context():
        assert NotionIntegration.query.count() == 0


def test_a_forged_state_is_still_refused(client, signed_in, token_endpoint):
    with client.session_transaction() as s:
        s["notion_oauth_state"] = "the-real-one"
    r = client.get("/oauth/notion/callback?code=abc&state=forged")
    assert r.status_code == 400
    with App.app.app_context():
        assert NotionIntegration.query.count() == 0


# ── Using the connection ──────────────────────────────────────────────


def test_an_expiring_token_is_renewed_before_it_is_handed_out(client, signed_in, token_endpoint):
    with App.app.app_context():
        db.session.add(NotionIntegration(
            user_id=signed_in, token="stale_access", refresh_token="old_refresh",
            token_expires_at=utcnow() - timedelta(minutes=1), auth_type="oauth",
            database_id="db_1",
        ))
        db.session.commit()

    token_endpoint["queue"].append(FakeResponse({
        "access_token": "new_access", "refresh_token": "new_refresh", "expires_in": 3600,
    }))

    with App.app.test_request_context():
        from flask_login import login_user
        login_user(db.session.get(User, signed_in))
        token, db_id = App.get_notion_token_and_db()

    assert token == "new_access"
    assert db_id == "db_1"
    with App.app.app_context():
        row = NotionIntegration.query.filter_by(user_id=signed_in).one()
        # Both rotate. Keeping the old refresh token would break the *next*
        # renewal, which is the failure this is here to stop.
        assert row.refresh_token == "new_refresh"


def test_a_healthy_token_is_not_refreshed_on_every_request(client, signed_in, token_endpoint):
    with App.app.app_context():
        db.session.add(NotionIntegration(
            user_id=signed_in, token="good_access", refresh_token="r",
            token_expires_at=utcnow() + timedelta(hours=2), auth_type="oauth",
        ))
        db.session.commit()

    with App.app.test_request_context():
        from flask_login import login_user
        login_user(db.session.get(User, signed_in))
        token, _ = App.get_notion_token_and_db()

    assert token == "good_access"
    assert token_endpoint["calls"] == [], "refreshed a token that was still valid"


def test_a_failed_refresh_still_hands_back_the_stored_token(client, signed_in, monkeypatch):
    """The stored token may have minutes left. Returning it gives the call a
    chance; if it really is dead, Notion says so in its own words."""
    with App.app.app_context():
        db.session.add(NotionIntegration(
            user_id=signed_in, token="stale_access", refresh_token="old_refresh",
            token_expires_at=utcnow(), auth_type="oauth",
        ))
        db.session.commit()

    def boom(*a, **kw):
        raise RuntimeError("notion is down")

    monkeypatch.setattr(App, "refresh_notion_token", boom)

    with App.app.test_request_context():
        from flask_login import login_user
        login_user(db.session.get(User, signed_in))
        token, _ = App.get_notion_token_and_db()

    assert token == "stale_access"


def test_a_manual_token_connection_is_left_alone(client, signed_in, token_endpoint):
    """Pasted integration tokens have no refresh token and never expire."""
    with App.app.app_context():
        db.session.add(NotionIntegration(
            user_id=signed_in, token="secret_manual", auth_type="manual",
        ))
        db.session.commit()

    with App.app.test_request_context():
        from flask_login import login_user
        login_user(db.session.get(User, signed_in))
        token, _ = App.get_notion_token_and_db()

    assert token == "secret_manual"
    assert token_endpoint["calls"] == []
