"""Session-cookie behaviour.

These guard the bug where signing in on a phone bounced straight back to
the login page. The password was accepted and ``login_user`` ran; the
cookie carried ``SameSite=None``, Safari's tracking prevention discarded
it, and the next request arrived with no session. It presented as "wrong
password" and was nothing of the sort.

Both requirements here are real and in tension, which is why the tests
cover both directions:

* a student signing in directly needs ``SameSite=Lax`` so the cookie
  survives Safari;
* the Lotus iframe needs ``SameSite=None`` or the cookie is never sent.
"""

from __future__ import annotations

import pytest

import App as app_module


#: An existing endpoint that writes to the session — it allocates a guest
#: id — so a cookie is actually issued. Flask only emits Set-Cookie when
#: the session is modified, so a plain GET of a static page proves nothing.
#:
#: Deliberately not a route registered by this fixture: Flask refuses to
#: accept new routes once the app has served its first request, so doing
#: that makes these tests pass alone and error whenever another test module
#: happens to run first.
PROBE_URL = "/api/active/current"


@pytest.fixture(scope="module")
def probe_app():
    app = app_module.app
    app.config["TESTING"] = True
    return app


def session_cookie(response):
    for key, value in response.headers.items():
        if key.lower() == "set-cookie" and value.startswith("session="):
            return value
    return None


def attrs(cookie: str) -> dict[str, str]:
    out = {}
    for part in cookie.split(";")[1:]:
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip().lower()] = v.strip()
        else:
            out[part.lower()] = "true"
    return out


# ── SameSite ──────────────────────────────────────────────────────────


def test_top_level_navigation_gets_samesite_lax(probe_app):
    """The phone case. Lax is what keeps the cookie alive in Safari."""
    client = probe_app.test_client()
    cookie = session_cookie(client.get(PROBE_URL, headers={"Sec-Fetch-Dest": "document"}))
    assert cookie is not None
    assert attrs(cookie).get("samesite") == "Lax"


def test_framed_request_gets_samesite_none(probe_app):
    """The embed case. Without None the cookie is not sent in an iframe."""
    client = probe_app.test_client()
    cookie = session_cookie(client.get(PROBE_URL, headers={"Sec-Fetch-Dest": "iframe"}))
    assert cookie is not None
    assert attrs(cookie).get("samesite") == "None"


def test_samesite_none_always_carries_secure(probe_app):
    """Browsers reject SameSite=None without Secure outright."""
    client = probe_app.test_client()
    cookie = session_cookie(client.get(PROBE_URL, headers={"Sec-Fetch-Dest": "iframe"}))
    assert "secure" in attrs(cookie)


def test_explicit_embed_flag_also_upgrades(probe_app):
    """Entry points that pass ?embed=1 work on browsers without Sec-Fetch-*."""
    client = probe_app.test_client()
    cookie = session_cookie(client.get(PROBE_URL + "?embed=1"))
    assert attrs(cookie).get("samesite") == "None"


def test_a_browser_without_sec_fetch_headers_defaults_to_lax(probe_app):
    """Unknown context falls back to the safe direction: direct sign-in
    keeps working, and only the embed degrades."""
    client = probe_app.test_client()
    cookie = session_cookie(client.get(PROBE_URL))
    assert attrs(cookie).get("samesite") == "Lax"


def test_cookie_is_http_only(probe_app):
    client = probe_app.test_client()
    cookie = session_cookie(client.get(PROBE_URL, headers={"Sec-Fetch-Dest": "document"}))
    assert "httponly" in attrs(cookie)


# ── The actual regression ─────────────────────────────────────────────


@pytest.fixture
def signed_up(probe_app):
    email = "cookie-regression@example.test"
    password = "correcthorsebattery1"
    with probe_app.app_context():
        user = app_module.User.query.filter_by(email=email).first()
        if user is None:
            user = app_module.User(
                email=email,
                name="Cookie Regression",
                password_hash=app_module.bcrypt.generate_password_hash(password).decode(),
            )
            app_module.db.session.add(user)
            app_module.db.session.commit()
    return email, password


def test_signing_in_does_not_bounce_back_to_the_login_page(probe_app, signed_up):
    """The reported bug, end to end.

    One client, so the cookie jar persists exactly as a browser's would.
    If the session cookie fails to stick, the redirect target is behind
    @login_required and sends the student back to /login — which is what
    they saw.
    """
    email, password = signed_up
    client = probe_app.test_client()

    login = client.post(
        "/login/account",
        data={"email": email, "password": password},
        headers={"Sec-Fetch-Dest": "document"},
        follow_redirects=False,
    )
    assert login.status_code in (301, 302), login.status_code
    destination = login.headers.get("Location", "")
    assert "login" not in destination.lower(), destination

    landed = client.get(destination, headers={"Sec-Fetch-Dest": "document"}, follow_redirects=False)
    if landed.status_code in (301, 302):
        assert "login" not in (landed.headers.get("Location") or "").lower()
    else:
        assert landed.status_code == 200


def test_the_session_survives_a_second_request(probe_app, signed_up):
    """A cookie that is set but not kept looks identical on the first hop."""
    email, password = signed_up
    client = probe_app.test_client()
    client.post(
        "/login/account",
        data={"email": email, "password": password},
        headers={"Sec-Fetch-Dest": "document"},
    )
    for _ in range(3):
        response = client.get("/dashboard", headers={"Sec-Fetch-Dest": "document"},
                              follow_redirects=False)
        location = (response.headers.get("Location") or "").lower()
        assert "login" not in location, "session was dropped between requests"


def test_login_issues_both_session_and_remember_cookies(probe_app, signed_up):
    """`remember=True` is what survives the browser being closed."""
    email, password = signed_up
    client = probe_app.test_client()
    response = client.post(
        "/login/account",
        data={"email": email, "password": password},
        headers={"Sec-Fetch-Dest": "document"},
    )
    names = {
        value.split("=", 1)[0].strip()
        for key, value in response.headers.items()
        if key.lower() == "set-cookie"
    }
    assert "session" in names
    assert "remember_token" in names
