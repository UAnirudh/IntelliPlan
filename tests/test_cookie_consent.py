"""Cookies, and the tracker that used to load without asking.

Microsoft Clarity — session replay and heatmaps — loaded on every page with
its project id hardcoded in the template: before anyone was asked, for every
visitor including children. Meanwhile the Privacy Policy stated in writing
that IntelliPlan does not use session replay, and Microsoft was absent from
the sub-processor list.

Clarity has been removed outright rather than gated, so the promise made to
schools is true again. These tests hold that line from both ends: nothing
third-party loads and the policy says so, while the consent machinery stays
in place so anything added later has to be declared and gated first.
"""

import pytest

import App
import cookie_policy
from App import User, db


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            db.create_all()
            User.query.filter(User.email.like("cookie+%")).delete(
                synchronize_session=False)
            db.session.commit()
        yield c
        with App.app.app_context():
            User.query.filter(User.email.like("cookie+%")).delete(
                synchronize_session=False)
            db.session.commit()
    App.limiter.enabled = True


def signed_in(client, email, **kwargs):
    with App.app.app_context():
        user = User(email=email, password_hash="", **kwargs)
        db.session.add(user)
        db.session.commit()
        uid = user.id
    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
    return uid


def accept_analytics(client):
    return client.post("/api/cookies/consent", json={"granted": ["analytics"]})


def register_a_tracker(monkeypatch):
    """Pretend someone added a non-essential cookie.

    Nothing non-essential ships today, so the gate's rules are exercised
    against a declared stand-in. These are the rules any future tracker
    inherits the moment it is added to the registry.
    """
    monkeypatch.setattr(cookie_policy, "COOKIES", cookie_policy.COOKIES + [{
        "name": "_fake", "category": cookie_policy.ANALYTICS,
        "storage": "cookie", "provider": "Test",
        "purpose": "Stand-in for a future tracker.", "duration": "1 day",
    }])


# ── Nothing third-party loads ────────────────────────────────

@pytest.mark.parametrize("path", ["/", "/legal", "/pricing", "/cookies"])
def test_no_session_replay_script_is_served(client, path):
    """The original defect: Clarity loaded on every page, for everybody,
    before anyone was asked."""
    page = client.get(path).data
    assert b"clarity.ms" not in page
    assert b"googletagmanager" not in page


def test_the_content_security_policy_forbids_the_tracker_origin(client):
    """Removing the script but leaving its origin allowed would let it come
    back unnoticed."""
    response = client.get("/")
    csp = (response.headers.get("Content-Security-Policy-Report-Only")
           or response.headers.get("Content-Security-Policy") or "")
    assert "clarity.ms" not in csp


def test_nothing_non_essential_is_registered(client):
    assert cookie_policy.cookies_for(cookie_policy.ANALYTICS) == []
    assert cookie_policy.analytics_available() is False


def test_no_banner_is_shown_when_there_is_nothing_to_consent_to(client):
    """A banner asking about nothing is theatre, and trains people to
    dismiss the ones that matter."""
    assert b"ipCookieBanner" not in client.get("/").data


# ── The gate, exercised against a declared stand-in ──────────

def test_declaring_a_tracker_is_what_turns_the_category_on(client, monkeypatch):
    assert cookie_policy.analytics_available() is False
    register_a_tracker(monkeypatch)
    assert cookie_policy.analytics_available() is True


def test_a_declared_tracker_still_needs_consent_first(client, monkeypatch):
    register_a_tracker(monkeypatch)
    with App.app.test_request_context("/"):
        assert App._analytics_allowed() is False


def test_consent_opens_the_gate(client, monkeypatch):
    register_a_tracker(monkeypatch)
    value = cookie_policy.serialize_consent(["analytics"])
    with App.app.test_request_context(
            "/", headers={"Cookie": f"{cookie_policy.CONSENT_COOKIE}={value}"}):
        assert App._analytics_allowed() is True


def test_declining_keeps_it_shut(client, monkeypatch):
    register_a_tracker(monkeypatch)
    value = cookie_policy.serialize_consent([])
    with App.app.test_request_context(
            "/", headers={"Cookie": f"{cookie_policy.CONSENT_COOKIE}={value}"}):
        assert App._analytics_allowed() is False


# ── Children ─────────────────────────────────────────────────

def _allowed_for_user(monkeypatch, **user_kwargs):
    """Run the gate for a signed-in user who has accepted analytics."""
    register_a_tracker(monkeypatch)
    with App.app.app_context():
        User.query.filter(User.email.like("cookie+gate%")).delete(
            synchronize_session=False)
        db.session.commit()
        user = User(email="cookie+gate@example.com", password_hash="", **user_kwargs)
        db.session.add(user)
        db.session.commit()
        uid = user.id

    value = cookie_policy.serialize_consent(["analytics"])
    # Consent is present and valid; the only thing that can refuse is the
    # age check, which is what these cases are about.
    with App.app.test_request_context(
            "/", headers={"Cookie": f"{cookie_policy.CONSENT_COOKIE}={value}"}):
        from flask_login import login_user
        login_user(db.session.get(User, uid))
        return App._analytics_allowed()


def test_an_under_13_is_never_allowed_even_having_accepted(client, monkeypatch):
    """COPPA: a child's own click is not consent anyone can rely on. This is
    the case the whole gate exists for."""
    assert _allowed_for_user(monkeypatch, birth_year=2016) is False


def test_a_child_awaiting_a_parent_is_not_allowed(client, monkeypatch):
    assert _allowed_for_user(
        monkeypatch, parent_email="parent@example.com",
        parent_consent_granted=False) is False


def test_an_older_student_who_accepts_is_allowed(client, monkeypatch):
    assert _allowed_for_user(
        monkeypatch, birth_year=2008, parent_consent_granted=True) is True


# ── The consent value ────────────────────────────────────────

def test_essential_is_always_granted(client):
    assert cookie_policy.parse_consent("v1:")["granted"] == ["essential"]
    assert "essential" in cookie_policy.serialize_consent([])


def test_an_unknown_category_cannot_be_smuggled_in(client):
    assert "advertising" not in cookie_policy.serialize_consent(
        ["analytics", "advertising"])


def test_an_absent_choice_means_ask(client):
    assert cookie_policy.parse_consent(None) is None
    assert cookie_policy.parse_consent("") is None


@pytest.mark.parametrize("junk", ["garbage", "1:analytics", "v:analytics", "vx:a"])
def test_a_malformed_cookie_means_ask_rather_than_assume(client, junk):
    assert cookie_policy.parse_consent(junk) is None


def test_a_stale_consent_version_is_asked_again(client):
    """Bumping the version is how a material change re-asks everybody."""
    assert cookie_policy.parse_consent(
        f"v{cookie_policy.CONSENT_VERSION + 1}:analytics") is None


def test_the_choice_is_stored_in_a_readable_cookie(client):
    header = accept_analytics(client).headers.get("Set-Cookie", "")
    assert cookie_policy.CONSENT_COOKIE in header
    assert "SameSite=Lax" in header
    # Not HttpOnly on purpose: the banner reads it to stay quiet.
    assert "HttpOnly" not in header


def test_a_malformed_request_is_refused(client):
    assert client.post("/api/cookies/consent", json={}).status_code == 400
    assert client.post("/api/cookies/consent",
                       json={"granted": "analytics"}).status_code == 400


def test_the_state_endpoint_reports_the_choice(client):
    assert client.get("/api/cookies/consent").get_json()["asked"] is False
    accept_analytics(client)
    state = client.get("/api/cookies/consent").get_json()
    assert state["asked"] is True
    assert "analytics" in state["granted"]


# ── The written policy ───────────────────────────────────────

def test_the_cookie_policy_page_exists_and_is_linked_from_every_page(client):
    assert client.get("/cookies").status_code == 200
    for path in ("/", "/legal", "/pricing"):
        assert b'href="/cookies"' in client.get(path).data


def test_every_registered_cookie_is_documented_on_the_page(client):
    """The page is generated from the list the code enforces, so drift
    between the two is impossible rather than merely unlikely."""
    html = client.get("/cookies").data.decode("utf-8", "ignore")
    for cookie in cookie_policy.COOKIES:
        assert cookie["name"] in html
        assert cookie["duration"] in html


def test_the_page_says_plainly_that_nothing_optional_is_in_use(client):
    html = client.get("/cookies").data.decode("utf-8", "ignore")
    assert "Microsoft Clarity" not in html
    assert "switched off for everyone" in html


def test_the_privacy_policy_promise_is_true_again(client):
    """It denied using session replay while Clarity was loading on every
    page. The tracker is gone, so the sentence stands."""
    html = client.get("/legal").data.decode("utf-8", "ignore")
    assert "do <em>not</em> use session-replay" in html
    assert "No third-party analytics script runs in your browser" in html


def test_the_privacy_policy_no_longer_lists_microsoft(client):
    """Listing a sub-processor we do not use is the same class of error as
    omitting one we do."""
    html = client.get("/legal").data.decode("utf-8", "ignore")
    assert "privacy.microsoft.com" not in html


def test_the_privacy_policy_has_a_cookies_section(client):
    assert 'id="p-cookies"' in client.get("/legal").data.decode("utf-8", "ignore")


def test_the_cookie_page_is_in_the_sitemap(client):
    assert b"/cookies" in client.get("/sitemap.xml").data
