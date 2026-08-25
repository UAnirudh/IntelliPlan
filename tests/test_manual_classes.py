"""Classes a student types in themselves.

Every other course list in the app is derived from a linked account. A
student whose school is unsupported — or who is waiting on an LMS
connection that is still failing — had no way to name their classes, so
the course dropdown, grades page, and scheduler all came up empty and the
app looked broken on first run. These tests cover the path that fixes
that: the classes save, they survive a round trip, they show up in
``/courses``, and one student's classes never leak into another's.
"""

import pytest

import App
from App import ManualCourse, User, db


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            ManualCourse.query.delete()
            User.query.filter(User.email.like("mclass+%")).delete(
                synchronize_session=False)
            db.session.commit()
        yield c
        with App.app.app_context():
            ManualCourse.query.delete()
            User.query.filter(User.email.like("mclass+%")).delete(
                synchronize_session=False)
            db.session.commit()
    App.limiter.enabled = True


def make_user(email="mclass+a@example.com"):
    with App.app.app_context():
        u = User(email=email,
                 password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
                 name="Class Tester")
        db.session.add(u)
        db.session.commit()
        return u.id


def login(client, user_id):
    with client.session_transaction() as s:
        s["_user_id"] = str(user_id)
        s["_fresh"] = True


def test_a_guest_can_add_a_class_without_linking_an_account(client):
    r = client.post("/api/classes/manual", json={"name": "Algebra II"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok"
    assert [c["name"] for c in body["classes"]] == ["Algebra II"]


def test_a_whole_timetable_saves_in_one_request(client):
    r = client.post("/api/classes/manual",
                    json={"names": ["Algebra II", "AP Biology", "World History"]})
    names = [c["name"] for c in r.get_json()["classes"]]
    assert names == ["Algebra II", "AP Biology", "World History"]


def test_blank_lines_and_repeats_within_one_paste_are_dropped(client):
    r = client.post("/api/classes/manual",
                    json={"names": ["Algebra II", "  ", "algebra ii", "AP Biology"]})
    body = r.get_json()
    assert [c["name"] for c in body["classes"]] == ["Algebra II", "AP Biology"]


def test_adding_a_class_twice_reports_it_as_skipped_not_as_an_error(client):
    client.post("/api/classes/manual", json={"names": ["Algebra II"]})
    r = client.post("/api/classes/manual", json={"names": ["ALGEBRA II"]})
    body = r.get_json()
    assert r.status_code == 200
    assert body["created"] == []
    assert body["skipped"] == ["ALGEBRA II"]
    assert len(body["classes"]) == 1


def test_an_empty_name_is_refused_with_a_usable_message(client):
    r = client.post("/api/classes/manual", json={"names": ["", "   "]})
    assert r.status_code == 400
    assert "class name" in r.get_json()["message"].lower()


def test_classes_survive_the_round_trip(client):
    client.post("/api/classes/manual", json={"names": ["AP Biology"]})
    r = client.get("/api/classes/manual")
    assert [c["name"] for c in r.get_json()["classes"]] == ["AP Biology"]


def test_manual_classes_appear_in_the_course_list(client):
    client.post("/api/classes/manual", json={"names": ["World History"]})
    r = client.get("/courses")
    assert r.status_code == 200
    assert "World History" in [c.get("name") for c in r.get_json()]


def test_a_class_can_be_renamed(client):
    created = client.post("/api/classes/manual", json={"names": ["Algebra II"]})
    cid = created.get_json()["classes"][0]["id"]
    r = client.patch(f"/api/classes/manual/{cid}", json={"name": "Algebra 2 Honors"})
    assert [c["name"] for c in r.get_json()["classes"]] == ["Algebra 2 Honors"]


def test_renaming_to_nothing_is_refused(client):
    created = client.post("/api/classes/manual", json={"names": ["Algebra II"]})
    cid = created.get_json()["classes"][0]["id"]
    r = client.patch(f"/api/classes/manual/{cid}", json={"name": "   "})
    assert r.status_code == 400


def test_a_class_can_be_removed(client):
    created = client.post("/api/classes/manual", json={"names": ["Algebra II"]})
    cid = created.get_json()["classes"][0]["id"]
    r = client.delete(f"/api/classes/manual/{cid}")
    assert r.get_json()["classes"] == []


def test_removing_someone_elses_class_is_a_404_not_a_deletion(client):
    owner = make_user("mclass+owner@example.com")
    other = make_user("mclass+other@example.com")
    login(client, owner)
    created = client.post("/api/classes/manual", json={"names": ["Algebra II"]})
    cid = created.get_json()["classes"][0]["id"]

    login(client, other)
    assert client.delete(f"/api/classes/manual/{cid}").status_code == 404

    with App.app.app_context():
        assert db.session.get(ManualCourse, cid) is not None


def test_one_students_classes_are_invisible_to_another(client):
    owner = make_user("mclass+owner@example.com")
    other = make_user("mclass+other@example.com")
    login(client, owner)
    client.post("/api/classes/manual", json={"names": ["Algebra II"]})

    login(client, other)
    assert client.get("/api/classes/manual").get_json()["classes"] == []


def test_a_signed_in_students_classes_are_owned_by_them(client):
    uid = make_user()
    login(client, uid)
    client.post("/api/classes/manual", json={"names": ["Algebra II"]})
    with App.app.app_context():
        row = ManualCourse.query.filter_by(name="Algebra II").one()
        assert row.user_id == uid
        assert row.guest_session_id is None
