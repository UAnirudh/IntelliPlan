"""Cookie consent, and the tracker it gates.

Microsoft Clarity — session replay and heatmaps — used to load on every page
with its project id hardcoded in the template: before anyone was asked, for
every visitor including children. Meanwhile the Privacy Policy stated in
writing that IntelliPlan does not use session replay, and Microsoft was
absent from the sub-processor list.

These tests pin the gate shut. The under-13 case is the one that matters
most: a child clicking "accept" is not consent anybody can rely on.
"""

import pytest

import App
import cookie_policy
from App import User, db


@pytest.fixture
def clarity_on(monkeypatch):
    monkeypatch.setenv("CLARITY_PROJECT_ID", "testproject")


@pytest.fixture
def clarity_off(monkeypatch):
    monkeypatch.delenv("CLARITY_PROJECT_ID", raising=False)


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


# ── The gate ─────────────────────────────────────────────────

def test_no_tracker_before_anyone_is_asked(client, clarity_on):
    """The original defect: it loaded on first paint, for everybody."""
    assert b"clarity.ms" not in client.get("/").data


def test_the_banner_appears_when_there_is_something_to_ask_about(client, clarity_on):
    assert b"ipCookieBanner" in client.get("/").data


def test_declining_keeps_the_tracker_out(client, clarity_on):
    client.post("/api/cookies/consent", json={"granted": []})
    page = client.get("/").data
    assert b"clarity.ms" not in page
    assert b"ipCookieBanner" not in page      # and we stop asking


def test_accepting_lets_it_load(client, clarity_on):
    accept_analytics(client)
    assert b"clarity.ms" in client.get("/").data


def test_withdrawing_consent_removes_it_again(client, clarity_on):
    accept_analytics(client)
    assert b"clarity.ms" in client.get("/").data
    client.post("/api/cookies/consent", json={"granted": []})
    assert b"clarity.ms" not in client.get("/").data


def test_with_no_project_configured_nothing_loads_and_nothing_is_asked(
        client, clarity_off):
    """The kill switch: blank CLARITY_PROJECT_ID disables it product-wide,
    which was impossible while the id was hardcoded in the template."""
    page = client.get("/").data
    assert b"clarity.ms" not in page
    assert b"ipCookieBanner" not in page


# ── Children ─────────────────────────────────────────────────

def test_an_under_13_is_never_tracked_even_having_accepted(client, clarity_on):
    """COPPA: a child's own click is not consent. This is the case the whole
    gate exists for."""
    signed_in(client, "cookie+kid@example.com", birth_year=2016)
    accept_analytics(client)
    assert b"clarity.ms" not in client.get("/").data


def test_a_child_awaiting_a_parent_is_not_tracked(client, clarity_on):
    signed_in(client, "cookie+pending@example.com",
              parent_email="parent@example.com", parent_consent_granted=False)
    accept_analytics(client)
    assert b"clarity.ms" not in client.get("/").data


def test_an_older_student_who_accepts_is_tracked(client, clarity_on):
    signed_in(client, "cookie+teen@example.com", birth_year=2008,
              parent_consent_granted=True)
    accept_analytics(client)
    assert b"clarity.ms" in client.get("/").data


# ── The consent value ────────────────────────────────────────

def test_essential_is_always_granted(client):
    assert cookie_policy.parse_consent("v1:")["granted"] == ["essential"]
    assert "essential" in cookie_policy.serialize_consent([])


def test_an_unknown_category_cannot_be_smuggled_in(client):
    value = cookie_policy.serialize_consent(["analytics", "advertising"])
    assert "advertising" not in value


def test_an_absent_choice_means_ask(client):
    assert cookie_policy.parse_consent(None) is None
    assert cookie_policy.parse_consent("") is None


@pytest.mark.parametrize("junk", ["garbage", "1:analytics", "v:analytics", "vx:a"])
def test_a_malformed_cookie_means_ask_rather_than_assume(client, junk):
    assert cookie_policy.parse_consent(junk) is None


def test_a_stale_consent_version_is_asked_again(client):
    """Bumping the version is how a material change re-asks everybody."""
    old = f"v{cookie_policy.CONSENT_VERSION + 1}:analytics"
    assert cookie_policy.parse_consent(old) is None


def test_the_choice_is_stored_in_a_readable_cookie(client, clarity_on):
    r = accept_analytics(client)
    header = r.headers.get("Set-Cookie", "")
    assert cookie_policy.CONSENT_COOKIE in header
    assert "SameSite=Lax" in header
    # Not HttpOnly on purpose: the banner reads it to stay quiet.
    assert "HttpOnly" not in header


def test_a_malformed_request_is_refused(client):
    assert client.post("/api/cookies/consent", json={}).status_code == 400
    assert client.post("/api/cookies/consent",
                       json={"granted": "analytics"}).status_code == 400


def test_the_state_endpoint_reports_the_choice(client, clarity_on):
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


def test_every_registered_cookie_is_documented_on_the_page(client, clarity_on):
    """The page is generated from the list the code enforces, so drift
    between the two is impossible rather than merely unlikely."""
    html = client.get("/cookies").data.decode("utf-8", "ignore")
    for cookie in cookie_policy.COOKIES:
        assert cookie["name"] in html
        assert cookie["duration"] in html


def test_the_analytics_section_is_hidden_when_analytics_is_off(client, clarity_off):
    """Offering a switch that controls nothing is worse than offering none."""
    html = client.get("/cookies").data.decode("utf-8", "ignore")
    assert "Microsoft Clarity" not in html
    assert "switched off for everyone" in html


def test_the_privacy_policy_no_longer_denies_using_session_replay(client):
    """It said, in writing, that we do not use session replay — while
    Clarity was loading on every page."""
    html = client.get("/legal").data.decode("utf-8", "ignore")
    assert "not</em> use session-replay" not in html
    assert "do <em>not</em> use keystroke loggers" in html


def test_the_privacy_policy_names_microsoft_as_a_sub_processor(client):
    html = client.get("/legal").data.decode("utf-8", "ignore")
    assert "Microsoft" in html
    assert "privacy.microsoft.com" in html


def test_the_privacy_policy_has_a_cookies_section(client):
    html = client.get("/legal").data.decode("utf-8", "ignore")
    assert 'id="p-cookies"' in html


def test_refusing_is_no_harder_than_accepting(client, clarity_on):
    """Consent is not freely given if declining costs more clicks. Both
    choices are one button, side by side, on the first screen."""
    html = client.get("/").data.decode("utf-8", "ignore")
    assert "ipCookieChoose([])" in html                 # essential only
    assert "ipCookieChoose(['analytics'])" in html      # allow analytics


def test_the_cookie_page_is_in_the_sitemap(client):
    assert b"/cookies" in client.get("/sitemap.xml").data
