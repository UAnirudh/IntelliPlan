"""End-to-end streak test against a live Flask test client.

Scenario: new user → completes task → streak=1 → next local day
completes task → streak=2 → skips a day → freeze consumed → streak=3.

Run with: pytest test_streak_e2e.py -v
"""

import json
import os
import pytest
from datetime import date, timedelta
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-e2e")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("GEMINI_API_KEY", "fake-key-for-testing")

from App import app, db, User, ManualTask, FeatureFlag, UserStreak


@pytest.fixture()
def client():
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["LOGIN_DISABLED"] = False
    app.config["SERVER_NAME"] = "localhost"

    with app.app_context():
        db.create_all()
        flag = FeatureFlag.query.filter_by(key="streak_v1").first()
        if not flag:
            flag = FeatureFlag(key="streak_v1", description="test", enabled=True)
            db.session.add(flag)
        flag.enabled = True
        flag.rollout_percentage = 100
        db.session.commit()

        yield app.test_client()

        db.session.remove()
        db.drop_all()


def _register_and_login(client):
    """Create a test user and log in via the test client."""
    with app.app_context():
        from flask_bcrypt import Bcrypt
        bcrypt = Bcrypt(app)
        pw_hash = bcrypt.generate_password_hash("testpass123").decode("utf-8")
        user = User(
            email="streaktest@example.com",
            password_hash=pw_hash,
            name="Streak Tester",
        )
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    client.post("/login", data={
        "email": "streaktest@example.com",
        "password": "testpass123",
    }, follow_redirects=True)

    return user_id


def _create_task(user_id):
    """Insert a manual task for the user."""
    with app.app_context():
        task = ManualTask(
            user_id=user_id,
            title="E2E streak test task",
            done=False,
        )
        db.session.add(task)
        db.session.commit()
        return task.id


def _complete_task(client, task_id, tz="America/Los_Angeles"):
    """Mark a task as done via the API."""
    return client.post(
        "/tasks/manual/update",
        data=json.dumps({"id": task_id, "done": True, "timezone": tz}),
        content_type="application/json",
    )


def _get_streak(client):
    """Fetch current streak status."""
    resp = client.get("/api/streak/status")
    return resp.get_json()


def test_full_streak_lifecycle(client):
    """New user → task done (streak=1) → next day task done (streak=2) →
    skip a day → task done (freeze consumed, streak=3)."""

    user_id = _register_and_login(client)

    day1 = date(2025, 7, 1)
    day2 = date(2025, 7, 2)
    day4 = date(2025, 7, 4)  # skipped day3

    # ── Day 1: First task completion ──
    task1_id = _create_task(user_id)
    with patch("streak_engine.resolve_local_date", return_value=day1):
        resp = _complete_task(client, task1_id)
        assert resp.status_code == 200

    with app.app_context():
        row = UserStreak.query.filter_by(user_id=user_id).first()
        assert row is not None
        assert row.current_streak == 1
        assert row.longest_streak == 1

    # ── Day 2: Second task completion → streak continues ──
    task2_id = _create_task(user_id)
    with patch("streak_engine.resolve_local_date", return_value=day2):
        resp = _complete_task(client, task2_id)
        assert resp.status_code == 200

    with app.app_context():
        row = UserStreak.query.filter_by(user_id=user_id).first()
        assert row.current_streak == 2
        assert row.longest_streak == 2

    # ── Day 4: Skipped day 3, freeze should be consumed ──
    task3_id = _create_task(user_id)
    with patch("streak_engine.resolve_local_date", return_value=day4):
        resp = _complete_task(client, task3_id)
        assert resp.status_code == 200

    with app.app_context():
        row = UserStreak.query.filter_by(user_id=user_id).first()
        assert row.current_streak == 3
        assert row.freezes_available == 1  # started with 2, used 1

    # ── Verify streak status API returns correct data ──
    with patch("streak_engine.resolve_local_date", return_value=day4):
        status = _get_streak(client)
        assert status["status"] == "ok"
        assert status["current_streak"] == 3
        assert status["freezes_available"] == 1


def test_streak_disabled_when_flag_off(client):
    """Streak system should no-op when flag is disabled."""
    with app.app_context():
        flag = FeatureFlag.query.filter_by(key="streak_v1").first()
        flag.enabled = False
        db.session.commit()

    user_id = _register_and_login(client)
    task_id = _create_task(user_id)

    _complete_task(client, task_id)

    with app.app_context():
        row = UserStreak.query.filter_by(user_id=user_id).first()
        assert row is None or row.current_streak == 0

    status = _get_streak(client)
    assert status.get("enabled") is False
