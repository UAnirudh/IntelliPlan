"""Active without a schedule, and with one that is finished.

Two reports, one screen. A student who had generated a schedule opened
Active and was told to go and generate a schedule; and Active refused to do
anything at all without a plan, which is a strange rule for a study timer.

The first was two bugs wearing a coat:

  * generating a plan did not save it, and only saved plans are visible to
    Active — so "I generated a schedule" and "the app can see a schedule"
    were different states, silently
  * ``/api/active/next`` reported only ``next: null``, so three different
    situations (no plan, finished plan, plan of nothing but breaks) shared
    one message telling the student to build a plan
"""

import json

import pytest

import App
from App import SavedSchedule, User, db, bcrypt


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            db.create_all()
            SavedSchedule.query.delete()
            User.query.filter(User.email.like("active+%")).delete(
                synchronize_session=False)
            db.session.commit()
        yield c
        with App.app.app_context():
            SavedSchedule.query.delete()
            User.query.filter(User.email.like("active+%")).delete(
                synchronize_session=False)
            db.session.commit()
    App.limiter.enabled = True


@pytest.fixture
def student(client):
    with App.app.app_context():
        user = User(email="active+a@example.com",
                    password_hash=bcrypt.generate_password_hash("hunter2ok").decode())
        db.session.add(user)
        db.session.commit()
        uid = user.id
    with client.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
    return uid


def save_plan(client, days):
    return client.post("/schedule/save",
                       json={"schedule_data": {"schedule": days}, "name": "Test"})


def block(**overrides):
    base = {"assignment": "Read Ch. 4", "course": "Biology",
            "duration_minutes": 45, "is_break": False}
    base.update(overrides)
    return base


# ── Why there is no next block ───────────────────────────────

def test_with_no_plan_at_all(client, student):
    body = client.get("/api/active/next").get_json()
    assert body["next"] is None
    assert body["has_plan"] is False
    assert body["all_done"] is False


def test_with_a_plan_that_has_work_left(client, student):
    save_plan(client, [{"date": "2026-08-26", "blocks": [block()]}])
    body = client.get("/api/active/next").get_json()
    assert body["next"]["title"] == "Read Ch. 4"
    assert body["has_plan"] is True
    assert body["all_done"] is False


def test_a_finished_plan_is_reported_as_finished_not_as_missing(client, student):
    """The reported bug. Telling a student who finished their plan to go and
    make one reads as the app having lost their schedule."""
    save_plan(client, [{"date": "2026-08-26", "blocks": [block(done=True)]}])
    body = client.get("/api/active/next").get_json()
    assert body["next"] is None
    assert body["has_plan"] is True
    assert body["all_done"] is True


def test_a_plan_of_nothing_but_breaks_is_not_called_finished(client, student):
    """Nothing was completed, so "everything is done" would be a lie."""
    save_plan(client, [{"date": "2026-08-26",
                        "blocks": [block(assignment="Break", is_break=True)]}])
    body = client.get("/api/active/next").get_json()
    assert body["has_plan"] is True
    assert body["all_done"] is False


def test_breaks_are_never_offered_as_the_next_session(client, student):
    save_plan(client, [{"date": "2026-08-26", "blocks": [
        block(assignment="Break", is_break=True),
        block(assignment="Essay draft"),
    ]}])
    assert client.get("/api/active/next").get_json()["next"]["title"] == "Essay draft"


# ── Studying without a plan ──────────────────────────────────

def test_a_session_can_be_started_with_no_schedule_whatsoever(client, student):
    """Wanting to study for twenty minutes should not require having
    generated a schedule first."""
    assert client.get("/api/active/next").get_json()["has_plan"] is False

    response = client.post("/api/active/start",
                           json={"title": "Biology reading", "planned_minutes": 25})
    assert response.status_code == 201
    session = response.get_json()["session"]
    assert session["title"] == "Biology reading"
    assert session["planned_minutes"] == 25


def test_the_ad_hoc_session_becomes_the_current_one(client, student):
    client.post("/api/active/start",
                json={"title": "Chemistry problems", "planned_minutes": 45})
    current = client.get("/api/active/current").get_json()["session"]
    assert current["title"] == "Chemistry problems"


def test_a_session_still_needs_a_title(client, student):
    assert client.post("/api/active/start",
                       json={"title": "  ", "planned_minutes": 25}).status_code == 400


@pytest.mark.parametrize("minutes", [0, -5, 301, "abc"])
def test_an_unusable_duration_is_refused(client, student, minutes):
    assert client.post("/api/active/start",
                       json={"title": "Study", "planned_minutes": minutes}
                       ).status_code == 400


@pytest.mark.parametrize("minutes", [15, 25, 45, 60])
def test_every_duration_the_form_offers_is_accepted(client, student, minutes):
    """The chips and the server must agree, or a button does nothing."""
    response = client.post("/api/active/start",
                           json={"title": f"Session {minutes}",
                                 "planned_minutes": minutes})
    assert response.status_code == 201
    client.post(f"/api/active/{response.get_json()['session']['id']}/finish",
                json={"active_seconds": 60})


def test_a_guest_can_study_too(client):
    """No account, no plan, still a study timer."""
    assert client.post("/api/active/start",
                       json={"title": "Revision", "planned_minutes": 25}
                       ).status_code == 201


# ── Generating a plan makes it visible to Active ─────────────

def test_a_saved_plan_is_what_active_reads(client, student):
    """Generation now persists, because "generated" and "saved" being
    different states is what produced the original report."""
    assert client.get("/api/active/next").get_json()["has_plan"] is False
    save_plan(client, [{"date": "2026-08-26", "blocks": [block()]}])
    assert client.get("/api/active/next").get_json()["has_plan"] is True


def test_saving_a_new_plan_retires_the_previous_one(client, student):
    save_plan(client, [{"date": "2026-08-26", "blocks": [block(assignment="Old")]}])
    save_plan(client, [{"date": "2026-08-27", "blocks": [block(assignment="New")]}])
    assert client.get("/api/active/next").get_json()["next"]["title"] == "New"
    with App.app.app_context():
        assert SavedSchedule.query.filter_by(is_active=True).count() == 1


def test_a_save_without_data_is_refused_rather_than_silently_dropped(client, student):
    """It answers 200 with an error body, which is how an unsaved plan could
    look saved to the caller."""
    body = client.post("/schedule/save", json={"name": "Nothing"}).get_json()
    assert body["status"] == "error"


# ── The page itself ──────────────────────────────────────────

def test_the_ad_hoc_form_is_present_on_the_page(client, student):
    html = client.get("/active").data.decode("utf-8", "ignore")
    assert 'id="ipaAdhoc"' in html
    assert 'id="ipaAdhocTitle"' in html
    assert "IPActive.adhocStart()" in html


def test_the_duration_choices_are_buttons_not_a_number_field(client, student):
    """Picking beats typing, and it removes any chance of entering a
    duration the server will reject."""
    html = client.get("/active").data.decode("utf-8", "ignore")
    for minutes in (15, 25, 45, 60):
        assert f'data-minutes="{minutes}"' in html
    assert 'type="number"' not in html


def test_the_start_button_begins_disabled(client, student):
    """Gate the action on the required field rather than explaining what was
    missing after they press it."""
    html = client.get("/active").data.decode("utf-8", "ignore")
    assert 'id="ipaAdhocStart"' in html
    start = html[html.find('id="ipaAdhocStart"'):]
    assert "disabled" in start[:start.find(">")]
