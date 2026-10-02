"""Quick-add over HTTP: every capture surface, one parser, one placement.

Pinned here:

* the web route saves a task, places it in the saved plan and answers with
  where it landed — the whole point versus "added";
* auth matches the rest of the app: session cookie (with the same-origin
  CSRF guard every write gets), Bearer app token on /api/v1 (no Origin, so
  the guard has nothing to check — that is the mobile/share-sheet path),
  X-Extension-Token on the extension route, and nothing for anyone else;
* the extension's old ``{title}`` body keeps working, now parsed.

The student's clock is pinned (Monday 9am) so placements are exact.
"""

import json
from datetime import datetime

import pytest

import App
import plan_insert_glue
import quick_add_glue
from App import ExtensionToken, ManualCourse, ManualTask, SavedSchedule, User, db

NOW = datetime(2026, 10, 5, 9, 0)  # Monday
PASSWORD = "quick-add-pw-1"
EXT_TOKEN = "qa-ext-token-123"


@pytest.fixture
def client(monkeypatch):
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    monkeypatch.setattr(quick_add_glue, "student_now", lambda uid, hint=None: NOW)
    monkeypatch.setattr(plan_insert_glue, "student_now", lambda uid, hint=None: NOW)
    with App.app.test_client() as c:
        with App.app.app_context():
            db.create_all()
            _wipe()
        yield c
        with App.app.app_context():
            _wipe()
    App.limiter.enabled = True


def _wipe():
    ids = [u.id for u in User.query.filter(User.email.like("qa+%")).all()]
    if ids:
        for model in (ManualTask, SavedSchedule, ManualCourse, ExtensionToken):
            model.query.filter(model.user_id.in_(ids)).delete(synchronize_session=False)
    User.query.filter(User.email.like("qa+%")).delete(synchronize_session=False)
    db.session.commit()


@pytest.fixture
def student():
    with App.app.app_context():
        u = User(email="qa+a@example.com", name="Quick",
                 password_hash=App.bcrypt.generate_password_hash(PASSWORD).decode())
        db.session.add(u)
        db.session.commit()
        db.session.add(ManualCourse(user_id=u.id, name="AP Biology"))
        db.session.add(ExtensionToken(user_id=u.id, token=EXT_TOKEN))
        db.session.commit()
        return u.id


def login(client, uid):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True


def save_plan(uid, blocks=()):
    with App.app.app_context():
        db.session.add(SavedSchedule(user_id=uid, is_active=True, name="Plan", schedule_data=json.dumps({
            "schedule": [{"date": "2026-10-05", "day_name": "Monday", "blocks": list(blocks)}]})))
        db.session.commit()


def plan_blocks(uid):
    with App.app.app_context():
        row = SavedSchedule.query.filter_by(user_id=uid, is_active=True).first()
        return [b for d in json.loads(row.schedule_data)["schedule"] for b in d["blocks"]]


# ── Web: session cookie ───────────────────────────────────────────────


def test_quick_add_saves_parses_and_says_where_it_landed(client, student):
    login(client, student)
    save_plan(student, [{"id": "b1", "assignment": "Math", "duration_minutes": 60,
                         "start_iso": "2026-10-05T17:00:00", "end_iso": "2026-10-05T18:00:00"}])
    r = client.post("/api/quick-add", json={"text": "bio lab due fri 2h"})
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["task"]["title"] == "Bio lab"
    assert body["task"]["due_date"] == "2026-10-09"
    assert body["task"]["course"] == "AP Biology"
    assert body["task"]["estimated_time"] == 120
    # Evening fallback window (5–10pm), after Math at 5–6: two hour sittings.
    assert body["placement"]["label"] == "Scheduled Mon 6:05–7:05 PM and Mon 7:10–8:10 PM"
    assert body["message"].endswith(body["placement"]["label"])
    assert "Bio lab" in body["message"]
    added = [b for b in plan_blocks(student) if b.get("task_id") == str(body["task"]["id"])]
    assert len(added) == 2 and all(b["parent_title"] == "Bio lab" for b in added)
    with App.app.app_context():
        task = db.session.get(ManualTask, body["task"]["id"])
        assert task.user_id == student and task.import_source == "quickadd"


def test_without_a_plan_the_task_is_still_saved(client, student):
    login(client, student)
    body = client.post("/api/quick-add", json={"text": "chem quiz tmrw"}).get_json()
    assert body["placement"]["status"] == "no_plan"
    assert "build your plan" in body["message"]
    assert body["task"]["due_date"] == "2026-10-06"


def test_a_typed_duration_wins_and_an_untyped_one_is_estimated(client, student):
    login(client, student)
    typed = client.post("/api/quick-add", json={"text": "essay 45m", "schedule": False}).get_json()
    guessed = client.post("/api/quick-add", json={"text": "essay", "schedule": False}).get_json()
    assert typed["minutes"] == 45 and typed["minutes_source"] == "typed"
    assert guessed["minutes"] > 0 and guessed["minutes_source"] == "estimated"
    assert guessed["placement"]["status"] == "skipped"


def test_preview_saves_nothing(client, student):
    login(client, student)
    r = client.post("/api/quick-add/preview", json={"text": "bio lab fri 2h"})
    assert r.status_code == 200
    assert r.get_json()["parsed"]["course"] == "AP Biology"
    with App.app.app_context():
        assert ManualTask.query.filter_by(user_id=student).count() == 0


def test_client_supplied_course_names_are_used(client, student):
    login(client, student)
    body = client.post("/api/quick-add/preview",
                       json={"text": "apush reading", "courses": ["AP US History"]}).get_json()
    assert body["parsed"]["course"] == "AP US History"


@pytest.mark.parametrize("payload", [{}, {"text": ""}, {"text": "   "}])
def test_empty_text_is_a_400(client, student, payload):
    login(client, student)
    assert client.post("/api/quick-add", json=payload).status_code == 400


def test_cross_site_quick_add_is_refused(client, student):
    """Same Origin/Referer guard as every other cookie-authenticated write."""
    login(client, student)
    r = client.post("/api/quick-add", json={"text": "evil task"},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    with App.app.app_context():
        assert ManualTask.query.filter_by(title="Evil task").first() is None


def test_same_origin_quick_add_is_allowed(client, student):
    login(client, student)
    r = client.post("/api/quick-add", json={"text": "fine task"},
                    headers={"Origin": "http://localhost"})
    assert r.status_code == 201


def test_a_guest_can_quick_add_to_their_own_session(client):
    r = client.post("/api/quick-add", json={"text": "guest essay fri", "schedule": False})
    assert r.status_code == 201
    with App.app.app_context():
        task = db.session.get(ManualTask, r.get_json()["task"]["id"])
        assert task.user_id is None and task.guest_session_id
        db.session.delete(task)
        db.session.commit()


# ── /api/v1: bearer token (Android share sheet, desktop tray) ─────────


def _token(client):
    return client.post("/api/v1/auth/token",
                       json={"email": "qa+a@example.com", "password": PASSWORD}).get_json()["token"]


def test_v1_quick_add_with_a_bearer_token(client, student):
    token = _token(client)
    save_plan(student)
    r = client.post("/api/v1/tasks/quick-add", json={"text": "bio quiz wed 30m"},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["task"]["due_date"] == "2026-10-07"
    assert body["placement"]["label"] == "Scheduled Mon 5:00–5:30 PM"
    with App.app.app_context():
        assert db.session.get(ManualTask, body["task"]["id"]).user_id == student


def test_v1_quick_add_needs_a_credential(client, student):
    assert client.post("/api/v1/tasks/quick-add", json={"text": "x"}).status_code == 401
    r = client.post("/api/v1/tasks/quick-add", json={"text": "x"},
                    headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401


def test_v1_quick_add_needs_text(client, student):
    r = client.post("/api/v1/tasks/quick-add", json={},
                    headers={"Authorization": f"Bearer {_token(client)}"})
    assert r.status_code == 400


def test_the_session_route_also_accepts_the_app_bearer_token(client, student):
    """No Origin header (a native client), so the CSRF guard has nothing to
    object to, and the request loader resolves the token to the student."""
    r = client.post("/api/quick-add", json={"text": "native task", "schedule": False},
                    headers={"Authorization": f"Bearer {_token(client)}"})
    assert r.status_code == 201
    with App.app.app_context():
        assert db.session.get(ManualTask, r.get_json()["task"]["id"]).user_id == student


# ── Extension token ───────────────────────────────────────────────────


def test_extension_text_is_parsed_and_placed(client, student):
    save_plan(student)
    r = client.post("/extension/task/add", json={"text": "bio lab due fri 2h", "timezone": "America/Chicago"},
                    headers={"X-Extension-Token": EXT_TOKEN, "Origin": "chrome-extension://abc"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok" and body["id"] == body["task"]["id"]
    assert body["task"]["course"] == "AP Biology"
    assert body["placement"]["label"].startswith("Scheduled Mon")


def test_extension_explicit_fields_override_the_parse(client, student):
    r = client.post("/extension/task/add",
                    json={"title": "quiz fri", "due_date": "2026-10-20", "course": "Chem"},
                    headers={"X-Extension-Token": EXT_TOKEN})
    task = r.get_json()["task"]
    assert task["due_date"] == "2026-10-20" and task["course"] == "Chem"


def test_extension_without_a_token_is_refused(client, student):
    r = client.post("/extension/task/add", json={"text": "no token"})
    assert r.status_code == 401
