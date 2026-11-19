"""Connecting Canvas: the one-click path, and the token path that always works.

Canvas OAuth is per school. A Developer Key only works against the Canvas
instance that issued it, so "Continue with Canvas" can only ever light up for
schools whose Canvas admin has registered IntelliPlan. That is a Canvas
constraint, not something the app can route around -- which is exactly why
the token fallback has to stay solid for everyone else.

Two bugs sat in that path:

1. **Per-school Developer Keys could never light up.** ``canvas_oauth.py``
   supports ``CANVAS_CLIENT_ID_<HOST>`` overrides so one deploy can serve
   several schools, but the login page asked ``oauth_is_configured()`` with
   no argument -- which only ever sees the *global* key. A deploy holding
   only per-school keys therefore rendered no OAuth block at all, including
   the probe script whose entire job is to ask, per URL, whether that school
   is registered. The feature was dead unless a global key happened to exist
   too, which is the opposite of its purpose.

2. **A typo'd school URL returned a 500.** The token check called
   ``requests.get`` with no exception handling, so an unreachable host, a
   Canvas that is down, or a network blip rendered a crash page rather than
   "check the address". The older ``canvas_routes.py`` blueprint already got
   this right; the live route did not.
"""

from __future__ import annotations

import pytest
import requests

import App
import canvas_oauth


SCHOOL = "https://lakesideschool.instructure.com"
OTHER_SCHOOL = "https://someotherschool.instructure.com"


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        yield c
    App.limiter.enabled = True


@pytest.fixture
def no_keys(monkeypatch):
    """A deploy with no Canvas Developer Key of any kind."""
    for name in list(App.os.environ):
        if name.startswith("CANVAS_CLIENT_"):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def only_school_key(no_keys, monkeypatch):
    """A deploy registered with one school and no global key."""
    monkeypatch.setenv("CANVAS_CLIENT_ID_LAKESIDESCHOOL_INSTRUCTURE_COM", "10000000000001")
    monkeypatch.setenv("CANVAS_CLIENT_SECRET_LAKESIDESCHOOL_INSTRUCTURE_COM", "secret")


# ── Naming the env vars a school needs ───────────────────────────────


def test_the_env_var_suffix_is_derived_from_the_school_host():
    assert canvas_oauth._host_key_suffix(SCHOOL) == "LAKESIDESCHOOL_INSTRUCTURE_COM"


def test_a_trailing_slash_does_not_change_the_suffix():
    """Students paste the URL straight from the address bar."""
    assert canvas_oauth._host_key_suffix(SCHOOL + "/") == "LAKESIDESCHOOL_INSTRUCTURE_COM"


def test_a_bare_host_without_a_scheme_resolves_the_same_way():
    assert canvas_oauth._host_key_suffix("lakesideschool.instructure.com") == \
        "LAKESIDESCHOOL_INSTRUCTURE_COM"


# ── A per-school key must actually reach the page ────────────────────


def test_a_school_key_alone_counts_as_configured_for_that_school(only_school_key):
    assert canvas_oauth.oauth_is_configured(SCHOOL) is True


def test_a_school_key_does_not_configure_a_different_school(only_school_key):
    assert canvas_oauth.oauth_is_configured(OTHER_SCHOOL) is False


def test_a_school_key_alone_counts_as_some_key_being_present(only_school_key):
    """The check the login page needs. oauth_is_configured() with no argument
    answers False here, which is what hid the whole block."""
    assert canvas_oauth.oauth_any_configured() is True
    assert canvas_oauth.oauth_is_configured() is False


def test_an_id_without_its_secret_is_not_configured(no_keys, monkeypatch):
    monkeypatch.setenv("CANVAS_CLIENT_ID_LAKESIDESCHOOL_INSTRUCTURE_COM", "10000000000001")
    assert canvas_oauth.oauth_any_configured() is False


def test_no_keys_at_all_is_not_configured(no_keys):
    assert canvas_oauth.oauth_any_configured() is False


def test_the_login_page_offers_one_click_when_a_school_key_exists(client, only_school_key):
    body = client.get("/login/canvas").get_data(as_text=True)
    assert "Continue with Canvas" in body
    assert "/oauth/canvas/check" in body, "the per-URL probe must render too"


def test_the_probe_answers_per_school(client, only_school_key):
    assert client.get(f"/oauth/canvas/check?canvas_base={SCHOOL}").get_json()["available"] is True
    assert client.get(f"/oauth/canvas/check?canvas_base={OTHER_SCHOOL}").get_json()["available"] is False


def test_the_button_stays_hidden_when_no_school_is_registered(client, no_keys):
    """The inverse guard: the looser page-level test must not advertise
    one-click sign-in on a deploy that has no keys at all."""
    body = client.get("/login/canvas").get_data(as_text=True)
    assert "Continue with Canvas" not in body


@pytest.fixture
def owner_testing(monkeypatch):
    monkeypatch.setattr(App, "is_owner", lambda _: True)
    monkeypatch.setattr(App.school_pilot, "enabled", lambda: False)
    monkeypatch.setattr(App, "_lms_policy_allowed", lambda source: source == "canvas")
    monkeypatch.setattr(App, "_resolves_to_public_host", lambda _: True)


def test_the_token_form_appears_above_the_calendar_link_for_the_owner(client, no_keys, owner_testing):
    body = client.get("/login/canvas").get_data(as_text=True)
    assert 'name="canvas_token"' in body
    assert 'name="canvas_url"' in body
    assert body.index('id="canvasTokenForm"') < body.index('id="canvasTokenDemo"') < body.index('name="feed_url"')


def test_guest_can_practice_but_cannot_submit_a_token(client, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("A guest should not be able to contact Canvas with a token")

    monkeypatch.setattr(App.net_guard, "pinned_https_request", unexpected)
    page = client.get("/login/canvas").get_data(as_text=True)
    assert 'id="canvasTokenDemo"' in page
    assert 'name="canvas_token"' not in page
    response = client.post("/login/canvas", data={"canvas_token": "fake", "canvas_url": SCHOOL})
    assert response.status_code == 403


# ── The token path must not crash ────────────────────────────────────


def _unreachable(*args, **kwargs):
    raise requests.ConnectionError("nope")


def test_an_unreachable_canvas_is_an_explanation_not_a_crash(client, monkeypatch, owner_testing):
    monkeypatch.setattr(App.net_guard, "pinned_https_request", _unreachable)
    r = client.post("/login/canvas", data={
        "canvas_token": "1234~tok", "canvas_url": SCHOOL})
    assert r.status_code == 200
    assert "Could not reach" in r.get_data(as_text=True)


def test_an_unreachable_canvas_is_not_blamed_on_the_token(client, monkeypatch, owner_testing):
    """"Invalid token" sends the student off to regenerate a token that was
    never the problem."""
    monkeypatch.setattr(App.net_guard, "pinned_https_request", _unreachable)
    body = client.post("/login/canvas", data={
        "canvas_token": "1234~tok", "canvas_url": SCHOOL}).get_data(as_text=True)
    assert "Invalid token or Canvas URL" not in body


def test_a_timeout_is_handled_the_same_way(client, monkeypatch, owner_testing):
    def timeout(*a, **k):
        raise requests.Timeout("slow")
    monkeypatch.setattr(App.net_guard, "pinned_https_request", timeout)
    r = client.post("/login/canvas", data={
        "canvas_token": "1234~tok", "canvas_url": SCHOOL})
    assert r.status_code == 200
    assert "Could not reach" in r.get_data(as_text=True)


def test_a_genuinely_bad_token_still_says_so(client, monkeypatch, owner_testing):
    """The inverse guard: a 401 from a reachable Canvas is a token problem,
    and must not be reported as a connectivity one."""
    class Resp:
        status_code = 401
    monkeypatch.setattr(App.net_guard, "pinned_https_request", lambda *a, **k: Resp())
    body = client.post("/login/canvas", data={
        "canvas_token": "bad", "canvas_url": SCHOOL}).get_data(as_text=True)
    assert "Invalid token or Canvas URL" in body
    assert "Could not reach" not in body


def test_canvas_token_check_does_not_follow_redirects(client, monkeypatch, owner_testing):
    captured = {}

    class Resp:
        status_code = 401

    def fake_get(url, method, **kwargs):
        captured["method"] = method
        captured.update(kwargs)
        return Resp()

    monkeypatch.setattr(App.net_guard, "pinned_https_request", fake_get)
    client.post("/login/canvas", data={
        "canvas_token": "synthetic-token", "canvas_url": SCHOOL})
    assert captured["method"] == "GET"
    assert captured["timeout"] == 20


def test_a_missing_field_is_caught_before_any_request(client, monkeypatch, owner_testing):
    def explode(*a, **k):
        raise AssertionError("should not have called Canvas")
    monkeypatch.setattr(App.requests, "get", explode)
    body = client.post("/login/canvas", data={
        "canvas_token": "", "canvas_url": SCHOOL}).get_data(as_text=True)
    assert "Please fill in both fields" in body


# ── Telling a school what to do ──────────────────────────────────────


def test_the_unavailable_notice_tells_the_admin_what_to_register(client):
    body = client.get("/login/canvas?reason=oauth_unavailable").get_data(as_text=True)
    assert "Developer Key" in body, "the admin needs to know what to create"
    assert "/oauth/canvas/callback" in body, "and the redirect URI it needs"


def test_the_unavailable_notice_points_to_the_calendar_link(client):
    """It is not a dead end: the fallback works today and is worth saying so."""
    body = client.get("/login/canvas?reason=oauth_unavailable").get_data(as_text=True)
    assert "calendar link" in body.lower()


def test_assignment_link_uses_the_connected_canvas_school():
    assert App.canvas_assignment_url(SCHOOL, 4716, 19) == f"{SCHOOL}/courses/4716/assignments/19"
    assert App.canvas_assignment_url(SCHOOL, None, 19) is None
