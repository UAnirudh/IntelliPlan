"""Blackboard Learn connection setup.

A client hit an error setting Blackboard up and had nothing to act on: the
URL they pasted came straight out of the address bar (a deep Ultra link),
the credentials could be under either of two env naming conventions, the
registered redirect URI was never read, and every failure landed on
``/connect?lms_error=1``, a page that said nothing at all. These tests pin
down each of those.
"""

import pytest

import App
from App import User, BlackboardIntegration, db


@pytest.fixture
def client(monkeypatch):
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    for var in ("BLACKBOARD_CLIENT_ID", "BLACKBOARD_CLIENT_SECRET",
                "BLACKBOARD_APP_KEY", "BLACKBOARD_APP_SECRET",
                "BLACKBOARD_REDIRECT_URI"):
        monkeypatch.delenv(var, raising=False)
    with App.app.test_client() as c:
        with App.app.app_context():
            BlackboardIntegration.query.delete()
            User.query.filter(User.email.like("bbtest+%")).delete(
                synchronize_session=False)
            db.session.commit()
        yield c
        with App.app.app_context():
            BlackboardIntegration.query.delete()
            User.query.filter(User.email.like("bbtest+%")).delete(
                synchronize_session=False)
            db.session.commit()
    App.limiter.enabled = True


def signed_in(client, email="bbtest+a@example.com"):
    with App.app.app_context():
        u = User(email=email,
                 password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
                 name="BB Tester")
        db.session.add(u)
        db.session.commit()
        uid = u.id
    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
    return uid


# ── credential discovery ──────────────────────────────────────

def test_client_id_naming_is_accepted(monkeypatch):
    monkeypatch.setenv("BLACKBOARD_CLIENT_ID", "cid")
    monkeypatch.setenv("BLACKBOARD_CLIENT_SECRET", "sec")
    assert App._blackboard_credentials() == ("cid", "sec")


def test_app_key_naming_is_accepted(monkeypatch):
    """The package provider shipped with the other pair of names. A deploy
    that set only those had Blackboard report itself as unconfigured."""
    monkeypatch.delenv("BLACKBOARD_CLIENT_ID", raising=False)
    monkeypatch.delenv("BLACKBOARD_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("BLACKBOARD_APP_KEY", "key")
    monkeypatch.setenv("BLACKBOARD_APP_SECRET", "secret")
    assert App._blackboard_credentials() == ("key", "secret")


def test_the_package_provider_reads_the_same_credentials(monkeypatch):
    from intelliplan.integrations.lms.blackboard import BlackboardProvider
    monkeypatch.setenv("BLACKBOARD_BASE_URL", "https://learn.school.edu")
    monkeypatch.delenv("BLACKBOARD_APP_KEY", raising=False)
    monkeypatch.delenv("BLACKBOARD_APP_SECRET", raising=False)
    monkeypatch.setenv("BLACKBOARD_CLIENT_ID", "cid")
    monkeypatch.setenv("BLACKBOARD_CLIENT_SECRET", "sec")
    assert BlackboardProvider.is_configured() is True


def test_the_registered_redirect_uri_wins_over_the_app_base_url(monkeypatch):
    """Blackboard rejects the exchange unless this matches the developer
    portal registration byte-for-byte."""
    monkeypatch.setenv("BLACKBOARD_REDIRECT_URI",
                       "https://intelliplan.tech/api/lms/callback/blackboard")
    assert App._blackboard_redirect_uri() == \
        "https://intelliplan.tech/api/lms/callback/blackboard"


# ── the URL a student actually pastes ─────────────────────────

@pytest.mark.parametrize("pasted", [
    "https://learn.school.edu/ultra/courses/_1234_1/outline",
    "https://learn.school.edu/webapps/portal/execute/tabs/tabAction",
    "learn.school.edu",
    "  https://learn.school.edu/  ",
])
def test_any_shape_of_school_url_reduces_to_the_origin(pasted):
    assert App.normalize_institution_url(pasted) == "https://learn.school.edu"


@pytest.mark.parametrize("junk", ["", "   ", "not a url", "https://", "localhost"])
def test_a_string_with_no_host_is_rejected(junk):
    assert App.normalize_institution_url(junk) is None


# ── the connect endpoint ──────────────────────────────────────

def test_without_credentials_the_ui_is_told_to_offer_the_manual_fallback(client):
    signed_in(client)
    body = client.post("/api/lms/connect/blackboard").get_json()
    assert body["status"] == "pending"
    assert body["fallback"] == "manual"


def test_with_credentials_and_no_url_the_ui_is_asked_for_the_school(client, monkeypatch):
    signed_in(client)
    monkeypatch.setenv("BLACKBOARD_CLIENT_ID", "cid")
    monkeypatch.setenv("BLACKBOARD_CLIENT_SECRET", "sec")
    body = client.post("/api/lms/connect/blackboard").get_json()
    assert body["status"] == "need_institution"


def test_a_deep_ultra_link_still_produces_a_working_authorize_url(client, monkeypatch):
    signed_in(client)
    monkeypatch.setenv("BLACKBOARD_CLIENT_ID", "cid")
    monkeypatch.setenv("BLACKBOARD_CLIENT_SECRET", "sec")
    body = client.post("/api/lms/connect/blackboard", json={
        "institution_url": "https://learn.school.edu/ultra/courses/_1_1/outline",
    }).get_json()
    assert body["status"] == "ok"
    assert body["url"].startswith(
        "https://learn.school.edu/learn/api/public/v1/oauth2/authorizationcode?")
    assert "client_id=cid" in body["url"]


def test_an_unusable_url_is_refused_before_the_redirect(client, monkeypatch):
    signed_in(client)
    monkeypatch.setenv("BLACKBOARD_CLIENT_ID", "cid")
    monkeypatch.setenv("BLACKBOARD_CLIENT_SECRET", "sec")
    r = client.post("/api/lms/connect/blackboard", json={"institution_url": "nope"})
    assert r.status_code == 400


# ── the callback ──────────────────────────────────────────────

def _start_flow(client, monkeypatch):
    monkeypatch.setenv("BLACKBOARD_CLIENT_ID", "cid")
    monkeypatch.setenv("BLACKBOARD_CLIENT_SECRET", "sec")
    client.post("/api/lms/connect/blackboard",
                json={"institution_url": "https://learn.school.edu"})
    with client.session_transaction() as s:
        return s["lms_oauth_state"]


def test_a_successful_callback_stores_the_institution_and_tokens(client, monkeypatch):
    uid = signed_in(client)
    state = _start_flow(client, monkeypatch)
    monkeypatch.setattr(App, "_blackboard_token_request",
                        lambda url, payload: {"access_token": "tok",
                                              "refresh_token": "ref",
                                              "expires_in": 3600})
    monkeypatch.setattr(App, "_blackboard_get_userinfo",
                        lambda url, tok: {"id": "_5_1", "userName": "student1"})
    r = client.get(f"/api/lms/callback/blackboard?state={state}&code=abc")
    assert r.headers["Location"] == "/connect?lms_connected=blackboard"
    with App.app.app_context():
        row = BlackboardIntegration.query.filter_by(user_id=uid).one()
        assert row.institution_url == "https://learn.school.edu"
        assert row.access_token == "tok"
        assert row.refresh_token == "ref"
        assert row.token_expires_at is not None


def test_the_exchange_uses_the_registered_redirect_uri(client, monkeypatch):
    signed_in(client)
    state = _start_flow(client, monkeypatch)
    monkeypatch.setenv("BLACKBOARD_REDIRECT_URI",
                       "https://intelliplan.tech/api/lms/callback/blackboard")
    seen = {}

    def capture(url, payload):
        seen.update(payload)
        return {"access_token": "tok", "expires_in": 3600}

    monkeypatch.setattr(App, "_blackboard_token_request", capture)
    monkeypatch.setattr(App, "_blackboard_get_userinfo", lambda url, tok: {})
    client.get(f"/api/lms/callback/blackboard?state={state}&code=abc")
    assert seen["redirect_uri"] == "https://intelliplan.tech/api/lms/callback/blackboard"


def test_a_stale_state_sends_the_student_back_instead_of_a_dead_end(client):
    """The exchange is still refused — this is the CSRF guard — but a 400
    error page left the student with nowhere to retry from."""
    signed_in(client)
    r = client.get("/api/lms/callback/blackboard?state=stale&code=abc")
    assert r.status_code == 302
    assert "lms_error=1" in r.headers["Location"]
    assert "reason=state_mismatch" in r.headers["Location"]
    with App.app.app_context():
        assert BlackboardIntegration.query.count() == 0


def test_a_rejected_token_exchange_names_the_reason(client, monkeypatch):
    signed_in(client)
    state = _start_flow(client, monkeypatch)

    def reject(url, payload):
        raise RuntimeError('Blackboard token endpoint HTTP 400: '
                           '{"error":"invalid_redirect_uri"}')

    monkeypatch.setattr(App, "_blackboard_token_request", reject)
    r = client.get(f"/api/lms/callback/blackboard?state={state}&code=abc")
    assert "reason=redirect_mismatch" in r.headers["Location"]


def test_a_declined_sign_in_is_reported_as_declined(client, monkeypatch):
    signed_in(client)
    state = _start_flow(client, monkeypatch)
    r = client.get(f"/api/lms/callback/blackboard?state={state}&error=access_denied")
    assert "reason=access_denied" in r.headers["Location"]


def test_a_legacy_console_encoding_cannot_turn_a_log_line_into_a_500(client, monkeypatch):
    """The original client-facing error.

    ``/api/lms/connect/blackboard`` logged a waitlist line containing a
    Unicode arrow. On a Windows host whose stdout is cp1252 that raised
    UnicodeEncodeError *inside the view*, so clicking "Blackboard Learn"
    returned a 500 with no usable detail — the unhandled-exception handler
    printed the traceback and crashed on the same character.
    """
    import io

    signed_in(client)
    cp1252_console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr("sys.stdout", cp1252_console)

    r = client.post("/api/lms/connect/blackboard")
    assert r.status_code == 200
    assert r.get_json()["status"] == "pending"


def test_a_token_endpoint_error_body_reaches_the_log(monkeypatch):
    """``raise_for_status`` threw away the only part of the response that
    says what went wrong, which is why the original failure was opaque."""
    class FakeResponse:
        status_code = 400
        text = '{"error":"unauthorized_client"}'

    monkeypatch.setattr(App.requests, "post", lambda *a, **kw: FakeResponse())
    with pytest.raises(RuntimeError) as excinfo:
        App._blackboard_token_request("https://learn.school.edu", {})
    assert "unauthorized_client" in str(excinfo.value)
