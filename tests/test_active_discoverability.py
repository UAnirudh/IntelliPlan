"""Active Study has to be reachable without typing the URL.

The page, its session API, and the on-device camera focus check-in were all
built and working, but nothing anywhere linked to ``/active``. A feature you
can only reach by guessing its path is, from the student's side, missing. These
tests assert the entry points exist, because a link is exactly the kind of
thing a later template refactor drops silently.
"""

import pytest

import App
from App import User, db


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            User.query.filter(User.email.like("navtest+%")).delete(
                synchronize_session=False)
            db.session.commit()
        yield c
        with App.app.app_context():
            User.query.filter(User.email.like("navtest+%")).delete(
                synchronize_session=False)
            db.session.commit()
    App.limiter.enabled = True


@pytest.fixture
def signed_in(client):
    with App.app.app_context():
        u = User(email="navtest+a@example.com",
                 password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
                 name="Nav Tester")
        db.session.add(u)
        db.session.commit()
        uid = u.id
    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
    return client


def test_the_active_page_still_serves(signed_in):
    r = signed_in.get("/active")
    assert r.status_code == 200


def test_the_camera_focus_check_in_is_on_the_page(signed_in):
    """The part the student was actually looking for."""
    html = signed_in.get("/active").get_data(as_text=True)
    assert 'id="ipaFocusToggle"' in html
    assert "ip-focus.js" in html


def test_the_sidebar_carries_an_active_study_tab(signed_in):
    # /scheduler rather than /dashboard: a brand-new account is bounced to
    # /onboarding from the dashboard, which renders no sidebar.
    html = signed_in.get("/scheduler").get_data(as_text=True)
    assert 'data-nav-item="active"' in html
    assert 'href="/active"' in html


def test_the_active_page_keeps_its_slim_chrome_but_still_offers_a_way_out(signed_in):
    """No app sidebar here on purpose: the page exists to hold attention on
    one task, and the blueprint is mounted standalone in
    ``tests/intelliplan/test_active_api.py``, where the sidebar's
    ``current_user`` does not exist. The top nav still carries the app links,
    so a student mid-session is never stranded."""
    html = signed_in.get("/active").get_data(as_text=True)
    assert 'class="app-side"' not in html
    assert 'href="/scheduler"' in html


def test_the_scheduler_links_to_it(signed_in):
    """Plan, then do — the scheduler is where a student is when they are
    ready to start working."""
    html = signed_in.get("/scheduler").get_data(as_text=True)
    assert 'href="/active"' in html


def test_the_study_hub_links_to_it(signed_in):
    html = signed_in.get("/study-and-learn").get_data(as_text=True)
    assert 'href="/active" class="hub-card' in html


def test_the_command_palette_can_find_it(signed_in):
    html = signed_in.get("/scheduler").get_data(as_text=True)
    assert "label:'Active Study'" in html


def test_the_study_hub_recommender_can_route_to_it(signed_in):
    r = signed_in.post("/api/study-hub/recommend",
                       json={"query": "something to keep me on task with my camera"})
    assert r.status_code == 200
    assert r.get_json().get("route") == "/active"
