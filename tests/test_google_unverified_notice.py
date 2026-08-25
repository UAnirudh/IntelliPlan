"""The pre-consent notice for Google's unverified-app warning.

Calendar is a sensitive scope, so Google shows a full "Google hasn't
verified this app" screen — naming the developer's personal address and
advising against continuing — until the OAuth consent screen passes review.
Sending a student into that with no context is where the flow loses people.

The notice explains it; it does not suppress it, and it cannot. It is gated
on GOOGLE_OAUTH_UNVERIFIED so it disappears the moment verification lands.
"""

from __future__ import annotations

import pytest

import App
from App import User, db


@pytest.fixture
def client(monkeypatch):
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    with App.app.test_client() as c:
        with App.app.app_context():
            User.query.filter(User.email.like("gnotice+%")).delete(
                synchronize_session=False)
            db.session.commit()
        yield c
        with App.app.app_context():
            User.query.filter(User.email.like("gnotice+%")).delete(
                synchronize_session=False)
            db.session.commit()
    App.limiter.enabled = True


@pytest.fixture
def signed_in(client):
    with App.app.app_context():
        u = User(email="gnotice+a@example.com",
                 password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
                 name="G Tester")
        db.session.add(u)
        db.session.commit()
        uid = u.id
    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
    return client


def test_the_notice_shows_while_the_app_is_unverified(signed_in, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_UNVERIFIED", "1")
    r = signed_in.get("/oauth/google")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "hasn't verified this app" in html


def test_the_notice_says_how_to_get_past_the_warning(signed_in, monkeypatch):
    """Naming the exact buttons matters: "Advanced" is collapsed by default
    and most people never find it."""
    monkeypatch.setenv("GOOGLE_OAUTH_UNVERIFIED", "1")
    html = signed_in.get("/oauth/google").get_data(as_text=True)
    assert "Advanced" in html
    assert "Go to IntelliPlan (unsafe)" in html


def test_the_notice_states_what_the_access_is_used_for(signed_in, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_UNVERIFIED", "1")
    html = signed_in.get("/oauth/google").get_data(as_text=True)
    assert "/privacy" in html
    assert "myaccount.google.com/permissions" in html


def test_acknowledging_it_continues_to_google(signed_in, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_UNVERIFIED", "1")
    r = signed_in.get("/oauth/google?ack=1")
    assert r.status_code == 302
    assert r.headers["Location"].startswith("https://accounts.google.com/o/oauth2/v2/auth")


def test_the_return_target_survives_the_notice(signed_in, monkeypatch):
    """?return=settings has to reach the real handler, or the user is
    bounced to the dashboard after connecting from Settings."""
    monkeypatch.setenv("GOOGLE_OAUTH_UNVERIFIED", "1")
    html = signed_in.get("/oauth/google?return=settings").get_data(as_text=True)
    assert "return=settings" in html
    assert "ack=1" in html

    signed_in.get("/oauth/google?return=settings&ack=1")
    with signed_in.session_transaction() as s:
        assert s.get("oauth_return_to_settings") is True


def test_turning_the_flag_off_removes_the_notice_entirely(signed_in, monkeypatch):
    """The whole point of the gate: one env var to delete once Google
    grants verification, with no code change."""
    monkeypatch.delenv("GOOGLE_OAUTH_UNVERIFIED", raising=False)
    r = signed_in.get("/oauth/google")
    assert r.status_code == 302
    assert r.headers["Location"].startswith("https://accounts.google.com/o/oauth2/v2/auth")


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_falsey_flag_values_do_not_enable_the_notice(signed_in, monkeypatch, value):
    monkeypatch.setenv("GOOGLE_OAUTH_UNVERIFIED", value)
    assert signed_in.get("/oauth/google").status_code == 302


def test_signing_in_with_google_never_asks_for_calendar(monkeypatch):
    """Login uses only the non-sensitive identity scopes, so it does not
    trigger the unverified warning at all. Worth pinning: widening this to
    the full scope set would put the warning in front of every new signup."""
    from google_calendar_helper import CALENDAR_SCOPES, get_auth_url

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    url, _verifier = get_auth_url("state", purpose="login")
    for scope in CALENDAR_SCOPES:
        assert scope.replace(":", "%3A").replace("/", "%2F") not in url
        assert scope not in url
