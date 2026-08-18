"""The recovery endpoint — what happens when the student misses a session.

Real students do not follow schedules exactly. What the planner does about
that is most of whether it survives contact with a real week, so these tests
are about the endpoint's judgement: leaving an on-track plan alone, crediting
what was done, and never leaving a broken calendar behind.
"""

import json
from datetime import date, timedelta

import pytest

import App
from App import SavedSchedule, User, db


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            SavedSchedule.query.delete()
            User.query.filter(User.email.like("recover+%")).delete(
                synchronize_session=False)
            db.session.commit()
        yield c
        with App.app.app_context():
            SavedSchedule.query.delete()
            User.query.filter(User.email.like("recover+%")).delete(
                synchronize_session=False)
            db.session.commit()
    App.limiter.enabled = True


def make_user(email="recover+a@example.com"):
    with App.app.app_context():
        u = User(
            email=email,
            password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
            name="Recovery Tester",
        )
        db.session.add(u)
        db.session.commit()
        return u.id


def login(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


YESTERDAY = date.today() - timedelta(days=1)
NEXT_WEEK = date.today() + timedelta(days=7)


def saved_plan(user_id, progress=None, day=YESTERDAY):
    """A one-day plan holding two blocks: history research and maths."""
    data = {
        "schedule": [
            {
                "date": day.isoformat(),
                "day_name": day.strftime("%A"),
                "blocks": [
                    {
                        "id": "b1", "block_id": "b1", "task_id": "hist",
                        "assignment": "History research", "parent_title": "History research",
                        "duration_minutes": 60, "is_break": False,
                    },
                    {
                        "id": "b2", "block_id": "b2", "task_id": "math",
                        "assignment": "Math problem set", "parent_title": "Math problem set",
                        "duration_minutes": 30, "is_break": False,
                    },
                ],
            }
        ]
    }
    with App.app.app_context():
        row = SavedSchedule(
            user_id=user_id,
            name="Test plan",
            schedule_data=json.dumps(data),
            progress_json=json.dumps(progress or {}),
            is_active=True,
        )
        db.session.add(row)
        db.session.commit()
        return row.id


ASSIGNMENTS = [
    {
        "title": "History research", "course": "History",
        "due_date": NEXT_WEEK.isoformat(), "estimated_time": 60,
        "priority": "High", "points_possible": 50, "id": "hist",
    },
    {
        "title": "Math problem set", "course": "Math",
        "due_date": NEXT_WEEK.isoformat(), "estimated_time": 30,
        "priority": "Medium", "points_possible": 20, "id": "math",
    },
]


def recover(client, **body):
    payload = {"assignments": ASSIGNMENTS, "hours_per_day": 2}
    payload.update(body)
    return client.post("/schedule/recover", json=payload)


# ── Restraint ─────────────────────────────────────────────────────────


def test_no_saved_plan_is_not_an_error(client):
    login(client, make_user())
    assert recover(client).get_json()["status"] == "none"


def test_an_on_track_plan_is_left_alone(client):
    """Re-solving a plan that is on track moves blocks the student has already
    made peace with, for no gain."""
    uid = make_user()
    login(client, uid)
    saved_plan(uid, progress={"b1": {"done": True}, "b2": {"done": True}})
    body = recover(client).get_json()
    assert body["status"] == "ok"
    assert body["changed"] is False
    assert body["completed_minutes"] == 90


def test_todays_unchecked_work_is_not_treated_as_a_miss(client):
    uid = make_user()
    login(client, uid)
    saved_plan(uid, progress={}, day=date.today())
    assert recover(client).get_json()["changed"] is False


# ── Recovery ──────────────────────────────────────────────────────────


def test_a_missed_session_triggers_a_replan(client):
    uid = make_user()
    login(client, uid)
    saved_plan(uid, progress={"b2": {"done": True}})
    body = recover(client).get_json()
    assert body["changed"] is True
    assert body["missed_minutes"] == 60
    assert [m["title"] for m in body["missed"]] == ["History research"]
    assert body["data"]["schedule"]


def test_the_missed_work_is_scheduled_in_the_future_not_left_behind(client):
    uid = make_user()
    login(client, uid)
    saved_plan(uid, progress={"b2": {"done": True}})
    body = recover(client).get_json()
    days = [d["date"] for d in body["data"]["schedule"]]
    assert days
    assert all(d >= date.today().isoformat() for d in days)


def test_finished_work_is_not_scheduled_again(client):
    """Both blocks of the maths set were checked off, so it is done — putting
    it back in the plan tells the student their work did not count."""
    uid = make_user()
    login(client, uid)
    saved_plan(uid, progress={"b2": {"done": True}})
    body = recover(client).get_json()
    titles = {
        b.get("parent_title")
        for d in body["data"]["schedule"] for b in d["blocks"] if not b.get("is_break")
    }
    assert "Math problem set" not in titles


def test_the_student_is_told_what_moved(client):
    uid = make_user()
    login(client, uid)
    saved_plan(uid, progress={})
    changes = recover(client).get_json()["changes"]
    assert changes
    assert all({"title", "kind", "reason"} <= set(c) for c in changes)


def test_the_recovered_plan_is_saved_without_being_asked(client):
    """A recovery the student has to remember to save is a recovery that
    silently does not happen."""
    uid = make_user()
    login(client, uid)
    row_id = saved_plan(uid, progress={})
    recover(client)
    with App.app.app_context():
        stored = json.loads(db.session.get(SavedSchedule, row_id).schedule_data)
    days = [d["date"] for d in stored["schedule"]]
    assert days and all(d >= date.today().isoformat() for d in days)


def test_the_saved_plan_keeps_its_identity(client):
    uid = make_user()
    login(client, uid)
    row_id = saved_plan(uid, progress={})
    recover(client)
    with App.app.app_context():
        row = db.session.get(SavedSchedule, row_id)
        assert row.name == "Test plan"
        assert row.is_active is True
        assert SavedSchedule.query.filter_by(user_id=uid).count() == 1


# ── Guards ────────────────────────────────────────────────────────────


def test_recovery_without_assignments_asks_for_them(client):
    uid = make_user()
    login(client, uid)
    saved_plan(uid, progress={})
    resp = recover(client, assignments=[])
    assert resp.status_code == 400


def test_an_unreadable_saved_plan_fails_loudly_not_silently(client):
    uid = make_user()
    login(client, uid)
    with App.app.app_context():
        db.session.add(SavedSchedule(
            user_id=uid, name="Broken", schedule_data="{not json",
            progress_json=None, is_active=True,
        ))
        db.session.commit()
    assert recover(client).status_code == 500


def test_one_students_plan_is_never_recovered_for_another(client):
    owner = make_user("recover+owner@example.com")
    other = make_user("recover+other@example.com")
    saved_plan(owner, progress={})
    login(client, other)
    assert recover(client).get_json()["status"] == "none"
