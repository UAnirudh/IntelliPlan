"""The v1 endpoints the mobile app depends on.

The app has no database of its own: every screen is one of these calls, so
a change to their shape is a change to the app. These tests pin the shapes
rather than the values.

The interesting one is /api/v1/tasks. /api/v1/assignments proxies /live,
which is the connected-platform feed only — a manual task the student had
just created was missing from the very list they created it in, which
reads as the app dropping data. /tasks answers from /tasks/unified
instead, and that view returns three buckets rather than a list, which is
the bug these tests exist to keep fixed.
"""

from __future__ import annotations

import uuid

import pytest

import App as app_module


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    app_module.limiter.enabled = False
    with app_module.app.test_client() as c:
        yield c
    app_module.limiter.enabled = True


@pytest.fixture
def auth(client):
    """A real bearer token, obtained the way the app obtains one."""
    email = f"mob-{uuid.uuid4().hex[:12]}@example.test"
    password = "correcthorsebattery1"
    with app_module.app.app_context():
        user = app_module.User(
            email=email,
            name="Mobile",
            password_hash=app_module.bcrypt.generate_password_hash(password).decode(),
        )
        app_module.db.session.add(user)
        app_module.db.session.commit()
        uid = user.id
    token = client.post(
        "/api/v1/auth/token", json={"email": email, "password": password}
    ).get_json()["token"]
    return {"headers": {"Authorization": f"Bearer {token}"}, "user_id": uid}


# ── Every screen's endpoint answers ─────────────────────────────────


@pytest.mark.parametrize("path,key", [
    ("/api/v1/tasks", "tasks"),
    ("/api/v1/assignments", "assignments"),
    ("/api/v1/courses", "courses"),
    ("/api/v1/grades", "grades"),
])
def test_the_list_endpoints_return_their_named_list(client, auth, path, key):
    res = client.get(path, headers=auth["headers"])
    assert res.status_code == 200
    body = res.get_json()
    assert isinstance(body.get(key), list), f"{path} should return a list under {key!r}"


def test_the_saved_schedule_reports_none_rather_than_failing(client, auth):
    """A student who has never generated a plan is a normal state, not an
    error — the screen offers to build one."""
    res = client.get("/api/v1/schedule", headers=auth["headers"])
    assert res.status_code == 200
    assert res.get_json().get("status") in {"none", "ok"}


# ── Auth ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", [
    "/api/v1/tasks", "/api/v1/grades", "/api/v1/courses", "/api/v1/schedule",
])
def test_no_token_gets_nothing(client, path):
    assert client.get(path).status_code in {401, 403}


def test_a_junk_token_is_refused(client):
    res = client.get("/api/v1/grades", headers={"Authorization": "Bearer not-a-token"})
    assert res.status_code in {401, 403}


# ── The daily loop ──────────────────────────────────────────────────


def test_a_created_task_appears_in_the_task_list(client, auth):
    """The regression that motivated /api/v1/tasks. Creating a task and not
    seeing it is indistinguishable from the app losing it."""
    client.post("/api/v1/tasks", headers=auth["headers"],
                json={"title": "Essay draft", "course": "English"})

    titles = [t.get("title") for t in
              client.get("/api/v1/tasks", headers=auth["headers"]).get_json()["tasks"]]

    assert "Essay draft" in titles


def test_ticking_something_off_removes_it_and_persists(client, auth):
    client.post("/api/v1/tasks", headers=auth["headers"],
                json={"title": "Lab report", "course": "Physics"})

    res = client.post("/api/v1/assignments/dismiss", headers=auth["headers"],
                      json={"title": "Lab report"})
    assert res.status_code == 200

    titles = [t.get("title") for t in
              client.get("/api/v1/tasks", headers=auth["headers"]).get_json()["tasks"]]
    assert "Lab report" not in titles

    with app_module.app.app_context():
        rows = app_module.DismissedAssignment.query.filter_by(
            user_id=auth["user_id"]).count()
    assert rows == 1, "the tick has to survive the request that made it"


def test_restoring_brings_it_back(client, auth):
    """Undo matters more on a phone, where the tap target is small and the
    list scrolls under your thumb."""
    client.post("/api/v1/tasks", headers=auth["headers"],
                json={"title": "Reading", "course": "History"})
    client.post("/api/v1/assignments/dismiss", headers=auth["headers"],
                json={"title": "Reading"})

    client.post("/api/v1/assignments/restore", headers=auth["headers"],
                json={"title": "Reading"})

    titles = [t.get("title") for t in
              client.get("/api/v1/tasks", headers=auth["headers"]).get_json()["tasks"]]
    assert "Reading" in titles


def test_tasks_carry_the_bucket_they_came_from(client, auth):
    """Flattening three buckets into one list would otherwise lose the
    ordering that says what is late."""
    client.post("/api/v1/tasks", headers=auth["headers"],
                json={"title": "Bucketed", "course": "Maths"})

    tasks = client.get("/api/v1/tasks", headers=auth["headers"]).get_json()["tasks"]
    assert tasks
    assert all(t.get("bucket") in {"overdue", "today", "upcoming"} for t in tasks)


# ── Scope separation ────────────────────────────────────────────────


def test_grades_have_their_own_scope():
    """Reading what is due and reading what you scored are different levels
    of trust. A third-party key for assignments must not imply grades."""
    from api_keys import SCOPES
    assert "read:grades" in SCOPES
    assert SCOPES["read:grades"]["write"] is False
