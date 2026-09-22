"""The two ways the browser extension talks to the server, both of which were
broken in a way that looked like the extension simply not doing anything.

1. Quick-add. The popup's "add a task" box posted to /api/tasks/quick-add, a
   route that does not exist and never did. Every attempt 404'd and the popup
   said "Could not add task", so a working-looking feature never worked once.

2. Unsupported-LMS sync. Students whose district runs PowerSchool, Aeries,
   Infinite Campus, Skyward or eSchoolPlus have no API to connect to -- those
   are district-managed and issue no developer keys -- so the extension
   scrapes the page they are already signed in to and posts the result to
   /api/import/scraper. That endpoint only ever read the session cookie, while
   the extension posts cross-origin with credentials omitted and a bearer
   token. The cookie was never sent, the token was never read, and every sync
   came back 401. The entire district-LMS path was dead on arrival.

These tests pin the auth contract on both, since that is what was wrong.
"""

import pytest

import App
from App import (ExtensionToken, ImportedGrade, ManualTask, User, bcrypt, db)

PASSWORD = "ext-sync-pw"
TOKEN = "ext-token-abc123"


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            db.create_all()
            _wipe()
        yield c
        with App.app.app_context():
            _wipe()
    App.limiter.enabled = True


def _wipe():
    ids = [u.id for u in User.query.filter(User.email.like("ext+%")).all()]
    if ids:
        ExtensionToken.query.filter(
            ExtensionToken.user_id.in_(ids)).delete(synchronize_session=False)
        ManualTask.query.filter(
            ManualTask.user_id.in_(ids)).delete(synchronize_session=False)
        ImportedGrade.query.filter(
            ImportedGrade.user_id.in_(ids)).delete(synchronize_session=False)
    User.query.filter(User.email.like("ext+%")).delete(synchronize_session=False)
    db.session.commit()


@pytest.fixture
def student():
    """A signed-up student holding an extension token, as the popup issues."""
    with App.app.app_context():
        user = User(email="ext+a@example.com",
                    password_hash=bcrypt.generate_password_hash(PASSWORD).decode())
        db.session.add(user)
        db.session.commit()
        db.session.add(ExtensionToken(user_id=user.id, token=TOKEN))
        db.session.commit()
        return user.id


# ── Quick add ─────────────────────────────────────────────────

def test_the_popup_can_add_a_task(client, student):
    r = client.post("/extension/task/add",
                    json={"title": "Read chapter 4"},
                    headers={"X-Extension-Token": TOKEN})
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"
    with App.app.app_context():
        task = ManualTask.query.filter_by(user_id=student).first()
        assert task.title == "Read chapter 4"


def test_a_task_lands_on_the_right_student(client, student):
    """The token is the only thing naming the owner on this path."""
    client.post("/extension/task/add", json={"title": "Mine"},
                headers={"X-Extension-Token": TOKEN})
    with App.app.app_context():
        task = ManualTask.query.filter_by(title="Mine").first()
        assert task.user_id == student
        assert task.guest_session_id is None


def test_quick_add_without_a_token_is_refused(client, student):
    r = client.post("/extension/task/add", json={"title": "No token"})
    assert r.status_code == 401
    with App.app.app_context():
        assert ManualTask.query.filter_by(title="No token").first() is None


def test_a_bad_token_is_refused(client, student):
    r = client.post("/extension/task/add", json={"title": "Bad token"},
                    headers={"X-Extension-Token": "not-a-real-token"})
    assert r.status_code == 401


def test_an_empty_title_is_a_message_not_a_crash(client, student):
    r = client.post("/extension/task/add", json={"title": "   "},
                    headers={"X-Extension-Token": TOKEN})
    assert r.status_code == 400


def test_an_overlong_title_is_trimmed_rather_than_failing_the_commit(client, student):
    """The column is String(512); a longer title would 500 on an ordinary typo."""
    r = client.post("/extension/task/add", json={"title": "x" * 900},
                    headers={"X-Extension-Token": TOKEN})
    assert r.status_code == 200
    with App.app.app_context():
        assert len(ManualTask.query.filter_by(user_id=student).first().title) <= 512


# ── Unsupported-LMS sync ──────────────────────────────────────

PAYLOAD = {
    "lms": "powerschool",
    "label": "PowerSchool (West HS)",
    "assignments": [
        {"title": "Lab writeup", "course": "Chemistry", "due_date": "2026-10-01"},
    ],
    "grades": [
        {"course": "Chemistry", "percentage": 91.5, "letter": "A-"},
    ],
}


def test_a_district_scrape_is_accepted_on_the_extension_token(client, student):
    """The bug: the extension sends a bearer token and no cookie, and the
    endpoint read only the cookie, so every district sync 401'd."""
    r = client.post("/api/import/scraper", json=PAYLOAD,
                    headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["assignments_imported"] == 1
    assert body["grades_imported"] == 1


def test_the_popups_header_style_works_too(client, student):
    """The popup sends X-Extension-Token; the worker sends Authorization."""
    r = client.post("/api/import/scraper", json=PAYLOAD,
                    headers={"X-Extension-Token": TOKEN})
    assert r.status_code == 200


def test_scraped_work_is_owned_by_the_token_holder(client, student):
    client.post("/api/import/scraper", json=PAYLOAD,
                headers={"Authorization": f"Bearer {TOKEN}"})
    with App.app.app_context():
        task = ManualTask.query.filter_by(user_id=student).first()
        grade = ImportedGrade.query.filter_by(user_id=student).first()
        assert task.title == "Lab writeup"
        assert task.import_source == "scraper:powerschool"
        assert task.guest_session_id is None
        assert grade.course == "Chemistry"


def test_an_unauthenticated_scrape_is_still_refused(client, student):
    r = client.post("/api/import/scraper", json=PAYLOAD)
    assert r.status_code == 401
    with App.app.app_context():
        assert ManualTask.query.filter_by(user_id=student).first() is None


def test_a_bad_token_cannot_write_into_someone_elses_account(client, student):
    r = client.post("/api/import/scraper", json=PAYLOAD,
                    headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


def test_re_syncing_replaces_rather_than_duplicates(client, student):
    """Auto-sync runs every few hours; without replace the same gradebook
    would pile up a new copy of every assignment each time."""
    for _ in range(3):
        client.post("/api/import/scraper", json=PAYLOAD,
                    headers={"Authorization": f"Bearer {TOKEN}"})
    with App.app.app_context():
        assert ManualTask.query.filter_by(
            user_id=student, import_source="scraper:powerschool").count() == 1


def test_a_session_signed_in_student_still_works(client, student):
    """The token path must not displace the existing cookie path."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(student)
        sess["_fresh"] = True
    r = client.post("/api/import/scraper", json=PAYLOAD)
    assert r.status_code == 200
    with App.app.app_context():
        assert ManualTask.query.filter_by(user_id=student).count() == 1


# ── Grades round-trip ─────────────────────────────────────────
#
# Found by running the real server rather than the test client: the sync
# reported grades_imported: 2 and the extension still showed "No grades
# available yet". /extension/grades only ever read a StudentVue LinkedAccount
# -- it returned [] for any other account type and returned early for a
# student with no linked account at all, which is every district-LMS student,
# since there is no account for them to link. The write path worked and the
# read path did not know the data existed.

def test_scraped_grades_reach_the_grades_view(client, student):
    client.post("/api/import/scraper", json=PAYLOAD,
                headers={"Authorization": f"Bearer {TOKEN}"})
    r = client.get("/extension/grades", headers={"X-Extension-Token": TOKEN})
    assert r.status_code == 200
    courses = [g.get("course") for g in r.get_json()]
    assert "Chemistry" in courses


def test_grades_carry_the_numbers_the_popup_renders(client, student):
    """The popup averages `percentage` and colours by `letter`."""
    client.post("/api/import/scraper", json=PAYLOAD,
                headers={"Authorization": f"Bearer {TOKEN}"})
    row = next(g for g in client.get(
        "/extension/grades", headers={"X-Extension-Token": TOKEN}).get_json()
        if g["course"] == "Chemistry")
    assert row["percentage"] == 91.5
    assert row["letter"] == "A-"


def test_grades_are_private_to_their_owner(client, student):
    """A grades endpoint that leaked across students would be the worst kind
    of bug to ship, so pin it."""
    client.post("/api/import/scraper", json=PAYLOAD,
                headers={"Authorization": f"Bearer {TOKEN}"})
    with App.app.app_context():
        other = User(email="ext+other@example.com",
                     password_hash=bcrypt.generate_password_hash(PASSWORD).decode())
        db.session.add(other)
        db.session.commit()
        db.session.add(ExtensionToken(user_id=other.id, token="other-token"))
        db.session.commit()
    r = client.get("/extension/grades", headers={"X-Extension-Token": "other-token"})
    assert r.status_code == 200
    assert r.get_json() == []


def test_an_unauthenticated_caller_gets_no_grades(client, student):
    assert client.get("/extension/grades").status_code == 401
