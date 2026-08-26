"""Blackboard Learn connection setup.

A client hit an error setting Blackboard up and had nothing to act on: the
URL they pasted came straight out of the address bar (a deep Ultra link),
the credentials could be under either of two env naming conventions, the
registered redirect URI was never read, and every failure landed on
``/connect?lms_error=1``, a page that said nothing at all. These tests pin
down each of those.

Then a second client got further and still failed: they signed in on their
school's Blackboard page and the next screen was
``{"code":"illegalArgument","message":"invalid client_id"}`` — served by
Blackboard, on the school's host, past the point where any of our code runs.
The tests from "the reported failure" down cover catching that before the
redirect, plus the two configuration mistakes that cause it.
"""

import json

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


#: The real address check, captured before any test stubs it out.
_REAL_HOST_CHECK = App._resolves_to_public_host


@pytest.fixture(autouse=True)
def no_preflight_network(monkeypatch):
    """``/api/lms/connect/blackboard`` probes the school's Learn host before
    redirecting. Answer it the way a Learn instance that accepts our key does,
    so no test reaches the network or DNS. Tests about the probe override the
    response; tests that need the address check ask for ``real_host_check``."""
    monkeypatch.setattr(App.requests, "get", lambda *a, **kw: _Resp(302, ""))
    monkeypatch.setattr(App, "_resolves_to_public_host", lambda url: True)


@pytest.fixture
def real_host_check(monkeypatch):
    """Put the genuine address check back for the SSRF tests."""
    monkeypatch.setattr(App, "_resolves_to_public_host", _REAL_HOST_CHECK)


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


def test_current_app_key_wins_over_stale_client_id(monkeypatch):
    """A stale alias must not cause Blackboard's invalid-client_id error."""
    monkeypatch.setenv("BLACKBOARD_CLIENT_ID", "old-client-id")
    monkeypatch.setenv("BLACKBOARD_CLIENT_SECRET", "old-secret")
    monkeypatch.setenv("BLACKBOARD_APP_KEY", "current-app-key")
    monkeypatch.setenv("BLACKBOARD_APP_SECRET", "current-app-secret")
    assert App._blackboard_credentials() == ("current-app-key", "current-app-secret")


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


# ── the reported failure: "invalid client_id" after sign-in ───
#
# The client reached their school's Blackboard login, entered their
# credentials, and the next screen was a raw API body:
#     {"code":"illegalArgument","message":"invalid client_id"}
# That page is served by Blackboard, on the school's host, after login, so
# nothing in our callback ever ran and there was no way back. Blackboard
# answers that way when the institution has not registered our Application ID,
# or when the Application ID was configured where the application *key*
# belongs. Both are detectable before the student is redirected.


class _Resp:
    """Minimal stand-in for a requests response."""

    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)


BB_INVALID_CLIENT = ('{"status":400,"code":"illegalArgument",'
                     '"message":"invalid client_id"}')


def test_a_school_that_has_not_approved_us_is_caught_before_the_redirect(client, monkeypatch):
    signed_in(client)
    monkeypatch.setenv("BLACKBOARD_CLIENT_ID", "cid")
    monkeypatch.setenv("BLACKBOARD_CLIENT_SECRET", "sec")
    monkeypatch.setenv("BLACKBOARD_APPLICATION_ID", "app-id-uuid")
    monkeypatch.setattr(App.requests, "get",
                        lambda *a, **kw: _Resp(400, BB_INVALID_CLIENT))

    body = client.post("/api/lms/connect/blackboard",
                       json={"institution_url": "https://learn.school.edu"}).get_json()

    assert body["status"] == "not_registered"
    assert "url" not in body
    assert body["application_id"] == "app-id-uuid"
    assert any("REST API Integrations" in step for step in body["admin_steps"])
    assert body["fallback"] == "manual"


def test_the_admin_steps_name_the_application_id_to_register(client, monkeypatch):
    signed_in(client)
    monkeypatch.setenv("BLACKBOARD_CLIENT_ID", "cid")
    monkeypatch.setenv("BLACKBOARD_CLIENT_SECRET", "sec")
    monkeypatch.setenv("BLACKBOARD_APPLICATION_ID", "0BADCAFE-1234")
    monkeypatch.setattr(App.requests, "get",
                        lambda *a, **kw: _Resp(400, BB_INVALID_CLIENT))

    body = client.post("/api/lms/connect/blackboard",
                       json={"institution_url": "https://learn.school.edu"}).get_json()
    assert any("0BADCAFE-1234" in step for step in body["admin_steps"])


def test_without_an_application_id_the_student_is_still_told_what_to_ask_for(client, monkeypatch):
    signed_in(client)
    monkeypatch.setenv("BLACKBOARD_CLIENT_ID", "cid")
    monkeypatch.setenv("BLACKBOARD_CLIENT_SECRET", "sec")
    monkeypatch.delenv("BLACKBOARD_APPLICATION_ID", raising=False)
    monkeypatch.setattr(App.requests, "get",
                        lambda *a, **kw: _Resp(400, BB_INVALID_CLIENT))

    body = client.post("/api/lms/connect/blackboard",
                       json={"institution_url": "https://learn.school.edu"}).get_json()
    assert body["application_id"] is None
    assert any("Application ID" in step for step in body["admin_steps"])


def test_swapping_the_portal_values_is_called_out_in_the_log(client, monkeypatch, capsys):
    """The Application ID sent as client_id is rejected by *every* instance,
    which otherwise reads as "no school has approved us yet"."""
    signed_in(client)
    monkeypatch.setenv("BLACKBOARD_APP_KEY", "same-uuid")
    monkeypatch.setenv("BLACKBOARD_APP_SECRET", "sec")
    monkeypatch.setenv("BLACKBOARD_APPLICATION_ID", "same-uuid")
    monkeypatch.setattr(App.requests, "get",
                        lambda *a, **kw: _Resp(400, BB_INVALID_CLIENT))

    client.post("/api/lms/connect/blackboard",
                json={"institution_url": "https://learn.school.edu"})
    assert "MISCONFIGURED" in capsys.readouterr().out


def test_a_school_that_accepts_the_key_still_gets_the_redirect(client, monkeypatch):
    """A good key sends the student to the sign-in page for that institution."""
    signed_in(client)
    monkeypatch.setenv("BLACKBOARD_CLIENT_ID", "cid")
    monkeypatch.setenv("BLACKBOARD_CLIENT_SECRET", "sec")
    monkeypatch.setattr(App.requests, "get", lambda *a, **kw: _Resp(302, ""))

    body = client.post("/api/lms/connect/blackboard",
                       json={"institution_url": "https://learn.school.edu"}).get_json()
    assert body["status"] == "ok"
    assert body["url"].startswith(
        "https://learn.school.edu/learn/api/public/v1/oauth2/authorizationcode?")


def test_a_wrong_school_url_is_named_as_such(client, monkeypatch):
    signed_in(client)
    monkeypatch.setenv("BLACKBOARD_CLIENT_ID", "cid")
    monkeypatch.setenv("BLACKBOARD_CLIENT_SECRET", "sec")

    def unreachable(*a, **kw):
        raise App.requests.exceptions.ConnectionError("no such host")

    monkeypatch.setattr(App.requests, "get", unreachable)
    r = client.post("/api/lms/connect/blackboard",
                    json={"institution_url": "https://learn.typo.edu"})
    assert r.status_code == 400
    assert "Could not reach" in r.get_json()["message"]


def test_an_unrecognised_probe_response_does_not_block_the_student(client, monkeypatch):
    """Fail open: a Learn instance that answers the probe in some shape we do
    not know about must not cost the student a connection that would work."""
    signed_in(client)
    monkeypatch.setenv("BLACKBOARD_CLIENT_ID", "cid")
    monkeypatch.setenv("BLACKBOARD_CLIENT_SECRET", "sec")
    monkeypatch.setattr(App.requests, "get",
                        lambda *a, **kw: _Resp(503, "maintenance window"))

    body = client.post("/api/lms/connect/blackboard",
                       json={"institution_url": "https://learn.school.edu"}).get_json()
    assert body["status"] == "ok"


def test_the_probe_does_not_burn_the_real_csrf_state(client, monkeypatch):
    """The preflight uses a throwaway state; the state stored in the session
    must be the one embedded in the URL the student actually follows."""
    signed_in(client)
    monkeypatch.setenv("BLACKBOARD_CLIENT_ID", "cid")
    monkeypatch.setenv("BLACKBOARD_CLIENT_SECRET", "sec")
    monkeypatch.setattr(App.requests, "get", lambda *a, **kw: _Resp(302, ""))

    body = client.post("/api/lms/connect/blackboard",
                       json={"institution_url": "https://learn.school.edu"}).get_json()
    with client.session_transaction() as s:
        stored = s["lms_oauth_state"]
    assert f"state={stored}" in body["url"]


# ── the probe must not become an SSRF primitive ───────────────
#
# The preflight is a *server-side* GET to a host the user typed. Without a
# check on the target, pasting an internal address would have IntelliPlan
# fetch it and report what came back.

@pytest.mark.parametrize("internal", [
    "http://127.0.0.1:8080",
    "http://169.254.169.254",
    "http://10.0.0.5",
    "http://192.168.1.1",
])
def test_an_internal_address_is_never_fetched(client, monkeypatch, real_host_check, internal):
    signed_in(client)
    monkeypatch.setenv("BLACKBOARD_CLIENT_ID", "cid")
    monkeypatch.setenv("BLACKBOARD_CLIENT_SECRET", "sec")

    fetched = []
    monkeypatch.setattr(App.requests, "get",
                        lambda *a, **kw: fetched.append(a) or _Resp(302, ""))

    r = client.post("/api/lms/connect/blackboard", json={"institution_url": internal})
    assert r.status_code == 400
    assert fetched == []


def _resolving_to(address):
    """Stub ``socket.getaddrinfo`` so the host check needs no real DNS."""
    return lambda host, port: [(0, 0, 0, "", (address, 0))]


@pytest.mark.parametrize("address", ["8.8.8.8", "2606:4700:4700::1111"])
def test_a_publicly_routable_host_is_allowed(monkeypatch, real_host_check, address):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to(address))
    assert App._resolves_to_public_host("https://learn.school.edu") is True


def test_a_hostname_that_resolves_inward_is_still_refused(monkeypatch, real_host_check):
    """DNS rebinding: a public-looking name pointing at an internal address."""
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", _resolving_to("10.1.2.3"))
    assert App._resolves_to_public_host("https://learn.school.edu") is False


def test_an_unresolvable_host_is_refused(monkeypatch, real_host_check):
    import socket

    def fail(host, port):
        raise OSError("name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    assert App._resolves_to_public_host("https://learn.school.edu") is False


# ── scope ─────────────────────────────────────────────────────

def test_offline_scope_is_requested_so_a_refresh_token_comes_back(client, monkeypatch):
    """``read`` alone yields no refresh token, so the connection expired
    within the hour and the refresh path could never run."""
    signed_in(client)
    monkeypatch.setenv("BLACKBOARD_CLIENT_ID", "cid")
    monkeypatch.setenv("BLACKBOARD_CLIENT_SECRET", "sec")
    monkeypatch.delenv("BLACKBOARD_SCOPE", raising=False)
    monkeypatch.setattr(App.requests, "get", lambda *a, **kw: _Resp(302, ""))

    body = client.post("/api/lms/connect/blackboard",
                       json={"institution_url": "https://learn.school.edu"}).get_json()
    assert "scope=read%20offline" in body["url"]


def test_the_package_provider_requests_the_same_scope(monkeypatch):
    from intelliplan.integrations.lms.blackboard import BlackboardProvider
    monkeypatch.setenv("BLACKBOARD_BASE_URL", "https://learn.school.edu")
    monkeypatch.setenv("BLACKBOARD_APP_KEY", "key")
    monkeypatch.setenv("BLACKBOARD_APP_SECRET", "secret")
    monkeypatch.delenv("BLACKBOARD_SCOPE", raising=False)
    url = BlackboardProvider().get_authorize_url(
        user_id=1, redirect_uri="https://intelliplan.tech/cb", state="s")
    assert "scope=read+offline" in url


def test_the_scope_can_be_overridden(client, monkeypatch):
    signed_in(client)
    monkeypatch.setenv("BLACKBOARD_CLIENT_ID", "cid")
    monkeypatch.setenv("BLACKBOARD_CLIENT_SECRET", "sec")
    monkeypatch.setenv("BLACKBOARD_SCOPE", "read write offline")
    monkeypatch.setattr(App.requests, "get", lambda *a, **kw: _Resp(302, ""))

    body = client.post("/api/lms/connect/blackboard",
                       json={"institution_url": "https://learn.school.edu"}).get_json()
    assert "scope=read%20write%20offline" in body["url"]


# ── token exchange ────────────────────────────────────────────

def test_credentials_move_to_the_body_when_basic_auth_is_refused(monkeypatch):
    """Both are legal OAuth 2.0. A Learn instance that rejects the Basic
    header used to look like a broken integration."""
    calls = []

    def fake_post(url, headers=None, data=None, timeout=None):
        calls.append({"headers": headers or {}, "data": data or {}})
        if "Authorization" in (headers or {}):
            return _Resp(401, '{"error":"invalid_client"}')
        return _Resp(200, '{"access_token":"tok","expires_in":3600}')

    monkeypatch.setenv("BLACKBOARD_APP_KEY", "key")
    monkeypatch.setenv("BLACKBOARD_APP_SECRET", "secret")
    monkeypatch.setattr(App.requests, "post", fake_post)

    data = App._blackboard_token_request("https://learn.school.edu", {"code": "c"})
    assert data["access_token"] == "tok"
    assert len(calls) == 2
    assert calls[1]["data"]["client_id"] == "key"
    assert calls[1]["data"]["client_secret"] == "secret"


def test_a_genuinely_bad_secret_still_fails_loudly(monkeypatch):
    monkeypatch.setattr(App.requests, "post",
                        lambda *a, **kw: _Resp(401, '{"error":"invalid_client"}'))
    with pytest.raises(RuntimeError) as excinfo:
        App._blackboard_token_request("https://learn.school.edu", {"code": "c"})
    assert "invalid_client" in str(excinfo.value)


def test_the_user_id_from_the_token_response_is_used(client, monkeypatch):
    """Blackboard returns the user UUID with the token. Some instances
    restrict /users/me, which left the row without an id."""
    uid = signed_in(client)
    state = _start_flow(client, monkeypatch)
    monkeypatch.setattr(App, "_blackboard_token_request",
                        lambda url, payload: {"access_token": "tok",
                                              "refresh_token": "ref",
                                              "expires_in": 3600,
                                              "user_id": "uuid-from-token"})
    monkeypatch.setattr(App, "_blackboard_get_userinfo", lambda url, tok: {})

    client.get(f"/api/lms/callback/blackboard?state={state}&code=abc")
    with App.app.app_context():
        row = BlackboardIntegration.query.filter_by(user_id=uid).one()
        assert row.bb_user_id == "uuid-from-token"


# ── the callback names an unapproved integration ──────────────

def test_an_invalid_client_id_at_the_token_step_is_named_not_registered(client, monkeypatch):
    signed_in(client)
    state = _start_flow(client, monkeypatch)

    def reject(url, payload):
        raise RuntimeError('Blackboard token endpoint HTTP 400: '
                           '{"code":"illegalArgument","message":"invalid client_id"}')

    monkeypatch.setattr(App, "_blackboard_token_request", reject)
    r = client.get(f"/api/lms/callback/blackboard?state={state}&code=abc")
    assert "reason=not_registered" in r.headers["Location"]


def test_blackboard_bouncing_back_with_invalid_client_id_is_named_too(client, monkeypatch):
    signed_in(client)
    state = _start_flow(client, monkeypatch)
    r = client.get(f"/api/lms/callback/blackboard"
                   f"?state={state}&error=illegalArgument"
                   f"&error_description=invalid%20client_id")
    assert "reason=not_registered" in r.headers["Location"]
