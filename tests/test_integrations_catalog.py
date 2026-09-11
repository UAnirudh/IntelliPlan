"""The integrations catalogue, and the surfaces that render it.

The dashboard's Integrations modal listed two integrations while the app
supported ten, because the same list was hand-written in four places and
three of them drifted. A student who opened it concluded IntelliPlan
connects to Google Calendar and Notion.

These tests pin the two properties that stop that happening again: the
catalogue is the single source, and every surface renders all of it.
"""

from __future__ import annotations

import pytest

import App
import integrations_catalog as ic


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        yield c
    App.limiter.enabled = True


# ── The catalogue itself ─────────────────────────────────────────────


def test_every_integration_the_app_supports_is_listed():
    """The list that drifted. If a connect route exists, it belongs here."""
    expected = {
        "canvas", "calendar_feed", "studentvue", "schoology",
        "google_classroom", "blackboard", "moodle", "brightspace",
        "google_calendar", "notion",
    }
    assert {i.id for i in ic.CATALOG} == expected


def test_every_integration_offers_at_least_one_way_in():
    for i in ic.CATALOG:
        assert i.methods, f"{i.id} lists no way to connect it"


def test_every_method_says_how():
    """A method with no instructions is a dead end wearing a button."""
    for i in ic.CATALOG:
        for m in i.methods:
            assert m.how.strip(), f"{i.id}.{m.key} has no instructions"
            assert m.start_url or m.start_post, f"{i.id}.{m.key} goes nowhere"


def test_friction_values_are_ones_the_ui_can_label():
    for i in ic.CATALOG:
        for m in i.methods:
            assert m.friction in ic.FRICTION_ORDER, f"{i.id}.{m.key}: {m.friction}"


def test_ids_are_unique():
    ids = [i.id for i in ic.CATALOG]
    assert len(ids) == len(set(ids))


# ── Ordering: the bug that sent students down the broken path ────────


def test_canvas_offers_the_calendar_link_before_oauth():
    """OAuth is one click, so ordering on friction alone put it first --
    and it only works where a school has issued a Developer Key, which
    most have not. The student clicked the best-looking option and was
    told it was not configured. Anything gated on a school admin sorts
    below everything that works today."""
    order = [m.key for m in ic.CANVAS.sorted_methods()]
    assert order.index("calendar_feed") < order.index("oauth")
    assert order.index("token") < order.index("oauth")


def test_admin_gated_methods_sort_last_everywhere():
    for i in ic.CATALOG:
        gated = [m.needs_school_admin for m in i.sorted_methods()]
        assert gated == sorted(gated), f"{i.id} puts an admin-gated method first"


def test_within_a_tier_the_cheapest_comes_first():
    """The inverse guard: the admin rule must not have flattened the
    friction ordering it sits on top of."""
    order = [m.key for m in ic.NOTION.sorted_methods()]
    assert order == ["oauth", "token"]


def test_the_methods_a_school_must_enable_are_flagged():
    """Canvas OAuth and Blackboard genuinely cannot work until someone at
    the school acts. Saying so is the difference between 'IntelliPlan is
    broken' and 'my school hasn't done this yet'."""
    canvas_oauth = next(m for m in ic.CANVAS.methods if m.key == "oauth")
    assert canvas_oauth.needs_school_admin
    assert all(m.needs_school_admin for m in ic.BLACKBOARD.methods)


def test_the_paths_that_need_nobody_are_not_flagged():
    for i in (ic.CALENDAR_FEED, ic.GOOGLE_CALENDAR, ic.NOTION, ic.STUDENTVUE):
        assert not any(m.needs_school_admin for m in i.methods), i.id


def test_a_feed_says_it_carries_no_grades():
    """Otherwise a student connects it, opens the gradebook, finds it
    empty, and concludes the product is broken."""
    assert ic.CALENDAR_FEED.no_grades


# ── The endpoint ─────────────────────────────────────────────────────


def test_the_status_endpoint_returns_the_whole_catalogue(client):
    data = client.get("/api/integrations/status").get_json()
    assert data["status"] == "ok"
    assert len(data["integrations"]) == len(ic.CATALOG)


def test_it_works_signed_out(client):
    """A guest connects an LMS too, and the menu is the same."""
    r = client.get("/api/integrations/status")
    assert r.status_code == 200
    assert len(r.get_json()["integrations"]) == len(ic.CATALOG)


def test_nothing_is_connected_for_a_fresh_visitor(client):
    data = client.get("/api/integrations/status").get_json()
    assert not any(i["connected"] for i in data["integrations"])


def test_a_guest_session_connection_is_reported(client):
    """Guests hold the connection in the session rather than a
    LinkedAccount, and that is still a real connection."""
    with client.session_transaction() as sess:
        sess["login_type"] = "canvas"
    data = client.get("/api/integrations/status").get_json()
    canvas = next(i for i in data["integrations"] if i["id"] == "canvas")
    assert canvas["connected"] is True


def test_an_unknown_session_login_type_does_not_break_the_list(client):
    """A login_type with no catalogue entry must not 500 the page that
    lists everything else."""
    with client.session_transaction() as sess:
        sess["login_type"] = "something_we_removed"
    r = client.get("/api/integrations/status")
    assert r.status_code == 200
    assert not any(i["connected"] for i in r.get_json()["integrations"])


def test_the_payload_carries_what_the_ui_needs(client):
    data = client.get("/api/integrations/status").get_json()
    for i in data["integrations"]:
        for key in ("id", "name", "category", "brings", "connected",
                    "coming_soon", "no_grades", "methods"):
            assert key in i, f"{i.get('id')} missing {key}"
        for m in i["methods"]:
            for key in ("key", "label", "how", "friction", "needs_school_admin"):
                assert key in m


def test_brightspace_is_marked_coming_soon_rather_than_offered(client):
    """It has no credentials configured, so a Connect button would just
    fail. Saying 'coming soon' is the honest version."""
    data = client.get("/api/integrations/status").get_json()
    bs = next(i for i in data["integrations"] if i["id"] == "brightspace")
    assert bs["coming_soon"] is True


# ── A signed-in student ──────────────────────────────────────────────


@pytest.fixture
def student(request):
    from App import LinkedAccount, User, db
    with App.app.app_context():
        User.query.filter(User.email.like("intgtest+%")).delete(synchronize_session=False)
        db.session.commit()
        u = User(email="intgtest+a@example.com", name="Integration Tester",
                 password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode())
        db.session.add(u)
        db.session.commit()
        uid = u.id
        # /dashboard redirects to /onboarding until this is set, so without
        # it the dashboard tests below assert against a redirect stub and
        # pass for the wrong reason.
        identity = App._get_or_create_identity(uid)
        identity.completed = True
        db.session.commit()

    def cleanup():
        with App.app.app_context():
            LinkedAccount.query.filter_by(user_id=uid).delete()
            User.query.filter_by(id=uid).delete()
            db.session.commit()
    request.addfinalizer(cleanup)
    return uid


def login(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


def test_a_linked_account_shows_as_connected(client, student):
    from App import LinkedAccount, db
    with App.app.app_context():
        acct = LinkedAccount(user_id=student, name="School Canvas",
                             login_type="canvas", is_active=True)
        acct.set_credentials({"canvas_token": "x", "canvas_url": "https://x.test"})
        db.session.add(acct)
        db.session.commit()

    login(client, student)
    data = client.get("/api/integrations/status").get_json()
    canvas = next(i for i in data["integrations"] if i["id"] == "canvas")
    assert canvas["connected"] is True
    assert canvas["detail"] == "School Canvas"


def test_an_inactive_linked_account_is_not_reported_as_connected(client, student):
    """Connecting a second account deactivates the first. A disconnected
    account reading as connected is how a student stops looking for the
    reason their assignments stopped arriving."""
    from App import LinkedAccount, db
    with App.app.app_context():
        acct = LinkedAccount(user_id=student, name="Old", login_type="canvas",
                             is_active=False)
        acct.set_credentials({})
        db.session.add(acct)
        db.session.commit()

    login(client, student)
    data = client.get("/api/integrations/status").get_json()
    canvas = next(i for i in data["integrations"] if i["id"] == "canvas")
    assert canvas["connected"] is False


def test_a_signed_in_student_still_gets_every_integration(client, student):
    login(client, student)
    data = client.get("/api/integrations/status").get_json()
    assert len(data["integrations"]) == len(ic.CATALOG)


# ── The surfaces that render it ──────────────────────────────────────


def test_the_dashboard_modal_renders_from_the_catalogue(client, student):
    """Rather than hardcoding rows, which is how it got to two of ten."""
    login(client, student)
    body = client.get("/dashboard").get_data(as_text=True)
    assert "ipIntegrationsList" in body
    assert "/api/integrations/status" in body


def test_the_dashboard_no_longer_hardcodes_two_integrations(client, student):
    """The specific regression: a modal whose markup names its rows can
    only ever list the ones somebody remembered to add."""
    login(client, student)
    body = client.get("/dashboard").get_data(as_text=True)
    assert 'id="gcalActions"' not in body
    assert 'id="notionActions"' not in body
