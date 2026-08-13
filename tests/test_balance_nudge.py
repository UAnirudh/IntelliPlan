"""Wellness nudges for a student who has never touched the settings.

The endpoint crashed with a 500 on exactly the account most likely to hit
it: a brand-new one. There is no ``MediaBalancePrefs`` row until the
student saves the balance settings, and the defaulting branch read
``prefs.night_nudges_enabled`` inside the arm guarded by ``prefs is None``.
Every fresh dashboard load fired the nudger and got an error id back.

So the tests below are mostly about the no-row case, and about the default
it should fall back to: night nudges on, day nudges off.
"""

from __future__ import annotations

import pytest

import App
from App import MediaBalancePrefs, User, db


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        yield c
    App.limiter.enabled = True


def make_user(email: str) -> int:
    with App.app.app_context():
        User.query.filter_by(email=email).delete(synchronize_session=False)
        db.session.commit()
        user = User(email=email,
                    password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
                    name="Balance Tester")
        db.session.add(user)
        db.session.commit()
        return user.id


def cleanup(user_id: int) -> None:
    with App.app.app_context():
        MediaBalancePrefs.query.filter_by(user_id=user_id).delete(
            synchronize_session=False)
        User.query.filter_by(id=user_id).delete(synchronize_session=False)
        db.session.commit()


def login(client, user_id: int) -> None:
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


# ── The no-row case ─────────────────────────────────────────────────


def test_a_student_with_no_saved_prefs_gets_a_nudge_not_a_500(client):
    user_id = make_user("balance+fresh@example.com")
    try:
        login(client, user_id)
        resp = client.get("/api/balance/nudge?tz_offset=0")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "ok"
        assert body["message"]
        assert body["bucket"] in {"day", "evening", "night"}
    finally:
        cleanup(user_id)


def test_night_nudges_default_on_and_day_nudges_default_off(client):
    """The default the crash was hiding.

    Unprompted "drink water" at 2pm is noise; a 1am "sleep consolidates
    what you studied" is the one worth interrupting for. With no row, the
    night bucket shows and the day bucket does not.
    """
    user_id = make_user("balance+defaults@example.com")
    try:
        login(client, user_id)
        # tz_offset is subtracted from UTC, so sweep every offset and check
        # the two buckets behave differently rather than pinning a clock.
        day_shown, night_shown = [], []
        for offset in range(0, 24 * 60, 60):
            body = client.get(f"/api/balance/nudge?tz_offset={offset}").get_json()
            assert body["status"] == "ok"
            (night_shown if body["is_night"] else day_shown).append(body["show"])
        assert night_shown and all(night_shown), "night nudges should default on"
        assert day_shown and not any(day_shown), "day nudges should default off"
    finally:
        cleanup(user_id)


def test_a_bad_tz_offset_is_ignored_rather_than_fatal(client):
    user_id = make_user("balance+badtz@example.com")
    try:
        login(client, user_id)
        resp = client.get("/api/balance/nudge?tz_offset=not-a-number")
        assert resp.status_code == 200
        assert 0 <= resp.get_json()["local_hour"] <= 23
    finally:
        cleanup(user_id)


# ── The saved-row cases ─────────────────────────────────────────────


def test_turning_night_nudges_off_is_respected(client):
    user_id = make_user("balance+nightoff@example.com")
    try:
        with App.app.app_context():
            db.session.add(MediaBalancePrefs(user_id=user_id,
                                             night_nudges_enabled=False,
                                             reminders_enabled=False))
            db.session.commit()
        login(client, user_id)
        for offset in range(0, 24 * 60, 60):
            body = client.get(f"/api/balance/nudge?tz_offset={offset}").get_json()
            assert body["show"] is False
    finally:
        cleanup(user_id)


def test_saved_cadences_come_back_on_the_matching_bucket(client):
    user_id = make_user("balance+cadence@example.com")
    try:
        with App.app.app_context():
            db.session.add(MediaBalancePrefs(user_id=user_id,
                                             reminders_enabled=True,
                                             reminder_minutes=30,
                                             night_nudges_enabled=True,
                                             night_cadence_minutes=15))
            db.session.commit()
        login(client, user_id)
        seen = {}
        for offset in range(0, 24 * 60, 60):
            body = client.get(f"/api/balance/nudge?tz_offset={offset}").get_json()
            seen[body["is_night"]] = body["cadence_minutes"]
        assert seen[True] == 15
        assert seen[False] == 30
    finally:
        cleanup(user_id)


def test_the_endpoint_needs_a_signed_in_student(client):
    assert client.get("/api/balance/nudge").status_code in (302, 401)
