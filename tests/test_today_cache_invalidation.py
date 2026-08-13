"""Adding work must change the plan immediately.

``/api/today`` caches its payload for 90 seconds, which is right for
repeated dashboard loads and wrong the moment a student adds an
assignment: the plan they are then shown was computed before the thing
they just typed existed, and nothing on screen admits it. It reads as the
app having ignored them.

These cover the eviction hook rather than the engines — that the write
paths which change what a plan is made of drop the cached copy, and that
ordinary reads still get the cache they exist for.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import App
from App import ManualTask, User, db
from intelliplan.api import command_center as cc


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        yield c
    App.limiter.enabled = True


@pytest.fixture
def user_id():
    email = "cache+today@example.com"
    with App.app.app_context():
        User.query.filter_by(email=email).delete(synchronize_session=False)
        db.session.commit()
        user = User(email=email,
                    password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
                    name="Cache Tester")
        db.session.add(user)
        db.session.commit()
        uid = user.id
    yield uid
    with App.app.app_context():
        ManualTask.query.filter_by(user_id=uid).delete(synchronize_session=False)
        User.query.filter_by(id=uid).delete(synchronize_session=False)
        db.session.commit()
    cc._TODAY_CACHE.pop(uid, None)


def login(client, uid: int) -> None:
    with client.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True


def seed_cache(uid: int, marker: str = "stale") -> None:
    cc._cache_put(uid, {"plan": [], "marker": marker})


def tomorrow() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


# ── The eviction helper ─────────────────────────────────────────────


def test_invalidate_drops_the_entry(user_id):
    seed_cache(user_id)
    assert user_id in cc._TODAY_CACHE
    cc.invalidate_today(user_id)
    assert user_id not in cc._TODAY_CACHE


def test_invalidating_a_user_with_no_cache_is_not_an_error():
    cc.invalidate_today(9_999_999)  # must not raise


@pytest.mark.parametrize("bad", [None, "not-an-int", object()])
def test_invalidate_tolerates_junk(bad):
    """An eviction failure must never take down the write that triggered it."""
    cc.invalidate_today(bad)


def test_invalidating_one_student_leaves_another_alone(user_id):
    seed_cache(user_id, "mine")
    seed_cache(user_id + 100_000, "theirs")
    try:
        cc.invalidate_today(user_id)
        assert user_id not in cc._TODAY_CACHE
        assert cc._TODAY_CACHE[user_id + 100_000][1]["marker"] == "theirs"
    finally:
        cc._TODAY_CACHE.pop(user_id + 100_000, None)


# ── The hook, through real endpoints ────────────────────────────────


def test_creating_a_task_evicts_the_cached_plan(client, user_id):
    login(client, user_id)
    seed_cache(user_id)
    resp = client.post("/tasks/manual/create", json={
        "title": "Physics lab", "course": "AP Physics",
        "due_date": tomorrow(), "estimated_time": 45, "priority": "High"})
    assert resp.status_code == 200
    assert user_id not in cc._TODAY_CACHE


def test_dismissing_a_task_evicts_the_cached_plan(client, user_id):
    login(client, user_id)
    seed_cache(user_id)
    assert client.post("/dismiss", json={"title": "Physics lab"}).status_code == 200
    assert user_id not in cc._TODAY_CACHE


def test_marking_a_test_evicts_the_cached_plan(client, user_id):
    login(client, user_id)
    seed_cache(user_id)
    assert client.post("/test/mark", json={"title": "Unit 4 exam"}).status_code == 200
    assert user_id not in cc._TODAY_CACHE


def test_a_plain_read_does_not_evict(client, user_id):
    """The cache has to survive reads or it is not a cache."""
    login(client, user_id)
    seed_cache(user_id)
    client.get("/tasks/manual/list")
    assert user_id in cc._TODAY_CACHE


def test_a_rejected_write_does_not_evict(client, user_id):
    """A 400 changed nothing, so the cached plan is still accurate."""
    login(client, user_id)
    seed_cache(user_id)
    assert client.post("/dismiss", json={}).status_code == 400
    assert user_id in cc._TODAY_CACHE


def test_a_write_on_an_unrelated_path_does_not_evict(client, user_id):
    login(client, user_id)
    seed_cache(user_id)
    client.post("/api/a11y/prefs", json={"high_contrast": True})
    assert user_id in cc._TODAY_CACHE
