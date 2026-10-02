"""Focus Shield: the endpoint the extension asks "should I block right now?".

The extension blocks the student's distractor list only while a planned
block or an Active session is running. So the endpoint has three jobs, and
these tests pin each:

1. Answer only for the token's owner -- a token for one student must never
   surface another student's plan, and a bad token is a 401.
2. Read the plan in the student's wall-clock time. Plan slots are local
   ("5:00 PM"); every stored timestamp is naive UTC; the server's own clock
   is wherever it happens to be deployed. Getting this wrong blocks YouTube
   at 10am and leaves it open during the actual study block.
3. Keep breaks limited per block and "I'm done early" honest.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

import App
import focus_shield_glue
from App import (ActiveSession, ExtensionToken, FocusShieldSettings, SavedSchedule,
                 User, UserStreak, bcrypt, db)
from intelliplan.services import focus_shield as shield
from time_utils import to_local, to_utc, utcnow

PREFIX = "fshield+"
TOKEN_A = "fshield-token-a"
TOKEN_B = "fshield-token-b"

# 2026-03-11 00:30 UTC is 2026-03-10 17:30 in Los Angeles (PDT, UTC-7: US
# DST began on 8 March), and 2026-03-11 09:30 in Tokyo.
NOW_UTC = datetime(2026, 3, 11, 0, 30)


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    monkeypatch.setattr(focus_shield_glue, "utcnow", lambda: NOW_UTC)
    with App.app.test_client() as c:
        with App.app.app_context():
            db.create_all()
            _wipe()
        yield c
        with App.app.app_context():
            _wipe()
    App.limiter.enabled = True


def _wipe():
    ids = [u.id for u in User.query.filter(User.email.like(PREFIX + "%")).all()]
    if ids:
        for model in (ExtensionToken, FocusShieldSettings, SavedSchedule, UserStreak, ActiveSession):
            model.query.filter(model.user_id.in_(ids)).delete(synchronize_session=False)
        User.query.filter(User.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()


def _user(tag: str, token: str, tz: str | None = None) -> int:
    with App.app.app_context():
        u = User(email=f"{PREFIX}{tag}@example.com",
                 password_hash=bcrypt.generate_password_hash("pw-12345678").decode())
        db.session.add(u)
        db.session.commit()
        db.session.add(ExtensionToken(user_id=u.id, token=token))
        if tz:
            db.session.add(UserStreak(user_id=u.id, timezone=tz))
        db.session.commit()
        return u.id


def _plan(user_id: int, day: str, blocks: list[dict], progress: dict | None = None) -> None:
    with App.app.app_context():
        db.session.add(SavedSchedule(
            user_id=user_id, name="Plan", is_active=True,
            schedule_data=json.dumps({"schedule": [{"date": day, "blocks": blocks}]}),
            progress_json=json.dumps(progress) if progress is not None else None,
        ))
        db.session.commit()


EVENING = [
    {"block_id": "d1-b1", "assignment": "Bio lab write-up", "course": "Biology",
     "time_slot": "4:00 PM - 5:00 PM", "duration_minutes": 60},
    {"block_id": "d1-b2", "assignment": "Chem problem set", "course": "Chemistry",
     "time_slot": "5:00 PM - 6:00 PM", "duration_minutes": 60},
    {"block_id": "d1-br", "assignment": "Break", "is_break": True, "time_slot": "6:00 PM - 6:10 PM"},
    {"block_id": "d1-b3", "assignment": "History reading", "course": "History",
     "time_slot": "6:10 PM - 6:40 PM", "duration_minutes": 30},
]


def _current(client, token=TOKEN_A, **params):
    return client.get("/extension/focus/current", query_string=params,
                      headers={"X-Extension-Token": token})


# ── pure helpers ──────────────────────────────────────────────────────


def test_block_window_reads_twelve_and_twenty_four_hour_slots():
    day = date(2026, 3, 10)
    assert shield.block_window(day, {"time_slot": "3:30 PM - 4:15 PM"}) == (
        datetime(2026, 3, 10, 15, 30), datetime(2026, 3, 10, 16, 15))
    assert shield.block_window(day, {"time_slot": "15:30-16:15"}) == (
        datetime(2026, 3, 10, 15, 30), datetime(2026, 3, 10, 16, 15))


def test_a_block_past_midnight_ends_the_next_day():
    start, end = shield.block_window(date(2026, 3, 10), {"time_slot": "11:30 PM - 12:15 AM"})
    assert end - start == timedelta(minutes=45)
    assert end.date() == date(2026, 3, 11)


def test_start_iso_is_trusted_for_its_clock_only():
    """humanize_schedule stamps every day's ISO times with *today*; the day
    the block belongs to is the day's own date."""
    window = shield.block_window(date(2026, 3, 12), {
        "start_iso": "2026-03-10T19:00:00", "end_iso": "2026-03-10T19:45:00"})
    assert window == (datetime(2026, 3, 12, 19, 0), datetime(2026, 3, 12, 19, 45))


def test_blocklist_is_normalised_deduped_and_cannot_block_intelliplan():
    cleaned = shield.clean_blocklist([
        "https://www.YouTube.com/watch?v=1", "youtube.com", "m.reddit.com",
        "not a site", "intelliplan.tech", "app.intelliplan.tech", " Discord.com/channels ",
    ])
    assert cleaned == ["youtube.com", "reddit.com", "discord.com"]


def test_default_blocklist_covers_the_named_distractors():
    for site in ("youtube.com", "tiktok.com", "instagram.com", "reddit.com", "x.com",
                 "twitter.com", "netflix.com", "discord.com", "roblox.com"):
        assert site in shield.DEFAULT_BLOCKLIST


def test_an_active_session_wins_over_the_plan():
    windows = shield.plan_windows({"schedule": [{"date": "2026-03-10", "blocks": EVENING}]})
    local_now = datetime(2026, 3, 10, 17, 30)
    active = {"key": "active-1", "title": "Essay", "course": "", "start": local_now,
              "end": local_now + timedelta(minutes=20)}
    out = shield.resolve(windows, local_now, lambda d: d, active=active)
    assert out["current"]["source"] == "active"
    assert out["current"]["title"] == "Essay"


def test_time_utils_round_trips_across_dst():
    local = to_local(NOW_UTC, "America/Los_Angeles")
    assert local == datetime(2026, 3, 10, 17, 30)
    assert to_utc(local, "America/Los_Angeles") == NOW_UTC
    # Unknown zone: falls back to the fixed offset rather than raising.
    assert to_local(NOW_UTC, "Not/AZone", -420) == datetime(2026, 3, 10, 17, 30)


# ── auth ──────────────────────────────────────────────────────────────


def test_no_token_is_a_401(client):
    assert client.get("/extension/focus/current").status_code == 401


def test_a_wrong_token_is_a_401(client):
    _user("a", TOKEN_A)
    assert _current(client, token="nope").status_code == 401


def test_the_bearer_header_works_too(client):
    _user("a", TOKEN_A)
    r = client.get("/extension/focus/current", headers={"Authorization": f"Bearer {TOKEN_A}"})
    assert r.status_code == 200


# ── timezone correctness ──────────────────────────────────────────────


def test_the_current_block_is_read_in_the_devices_timezone(client):
    uid = _user("a", TOKEN_A)
    _plan(uid, "2026-03-10", EVENING)
    body = _current(client, tz="America/Los_Angeles").get_json()
    assert body["current"]["title"] == "Chem problem set"
    # 5:00 PM PDT is 00:00 UTC the next day; the extension compares this
    # absolute instant against its own clock.
    assert body["current"]["starts_at"] == "2026-03-11T00:00:00Z"
    assert body["current"]["ends_at"] == "2026-03-11T01:00:00Z"
    assert body["current"]["minutes_left"] == 30
    assert body["next"]["title"] == "History reading"  # the break is skipped
    assert body["blocking"] is True


def test_without_a_device_zone_the_stored_streak_zone_is_used(client):
    uid = _user("a", TOKEN_A, tz="America/Los_Angeles")
    _plan(uid, "2026-03-10", EVENING)
    body = _current(client).get_json()
    assert body["timezone"] == "America/Los_Angeles"
    assert body["current"]["title"] == "Chem problem set"


def test_the_same_plan_is_not_current_in_another_timezone(client):
    """09:30 in Tokyo on the 11th: nothing in an evening plan for the 10th
    is running, and blocking must be off."""
    uid = _user("a", TOKEN_A)
    _plan(uid, "2026-03-10", EVENING)
    body = _current(client, tz="Asia/Tokyo").get_json()
    assert body["current"] is None
    assert body["blocking"] is False


def test_a_bogus_zone_falls_back_instead_of_erroring(client):
    uid = _user("a", TOKEN_A, tz="America/Los_Angeles")
    _plan(uid, "2026-03-10", EVENING)
    r = _current(client, tz="Mars/Olympus_Mons")
    assert r.status_code == 200
    assert r.get_json()["current"]["title"] == "Chem problem set"


def test_upcoming_blocks_are_sent_for_offline_use(client):
    uid = _user("a", TOKEN_A)
    _plan(uid, "2026-03-10", EVENING)
    upcoming = _current(client, tz="America/Los_Angeles").get_json()["upcoming"]
    assert [b["title"] for b in upcoming] == ["History reading"]
    assert upcoming[0]["starts_at"] == "2026-03-11T01:10:00Z"


def test_a_block_already_checked_off_does_not_block(client):
    uid = _user("a", TOKEN_A)
    _plan(uid, "2026-03-10", EVENING, progress={"d1-b2": {"done": True}})
    body = _current(client, tz="America/Los_Angeles").get_json()
    assert body["current"] is None
    assert body["blocking"] is False


# ── no leakage ────────────────────────────────────────────────────────


def test_one_students_token_never_sees_anothers_plan(client):
    _user("a", TOKEN_A)
    uid_b = _user("b", TOKEN_B)
    _plan(uid_b, "2026-03-10", EVENING)
    r = _current(client, token=TOKEN_A, tz="America/Los_Angeles")
    body = r.get_json()
    assert body["current"] is None and body["next"] is None and body["upcoming"] == []
    text = r.get_data(as_text=True)
    for title in ("Chem problem set", "Bio lab write-up", "History reading", "Chemistry"):
        assert title not in text


def test_another_students_active_session_is_not_mine(client, monkeypatch):
    now = utcnow().replace(microsecond=0)
    monkeypatch.setattr(focus_shield_glue, "utcnow", lambda: now)
    _user("a", TOKEN_A)
    uid_b = _user("b", TOKEN_B)
    with App.app.app_context():
        db.session.add(ActiveSession(user_id=uid_b, title="B's secret essay", planned_minutes=25,
                                     state="running", started_at=now, updated_at=now))
        db.session.commit()
    body = _current(client, token=TOKEN_A, tz="UTC").get_json()
    assert body["current"] is None
    # ...while B's own token does see it, so the test is not passing because
    # the session expired.
    assert _current(client, token=TOKEN_B, tz="UTC").get_json()["current"]["title"] == "B's secret essay"


def test_my_running_active_session_is_the_current_block(client, monkeypatch):
    # Real time here: the stale-session sweep runs on the repository's own
    # clock, and a heartbeat from March would (rightly) be expired.
    now = utcnow().replace(microsecond=0)
    monkeypatch.setattr(focus_shield_glue, "utcnow", lambda: now)
    uid = _user("a", TOKEN_A)
    with App.app.app_context():
        db.session.add(ActiveSession(user_id=uid, title="Essay draft", planned_minutes=25,
                                     state="running", started_at=now - timedelta(minutes=5),
                                     updated_at=now))
        db.session.commit()
    body = _current(client, tz="UTC").get_json()
    assert body["current"]["source"] == "active"
    assert body["current"]["title"] == "Essay draft"
    assert body["current"]["minutes_left"] == 20
    assert body["blocking"] is True


# ── breaks and done-early ─────────────────────────────────────────────


def test_breaks_are_limited_per_block(client):
    uid = _user("a", TOKEN_A, tz="America/Los_Angeles")
    _plan(uid, "2026-03-10", EVENING)
    with App.app.app_context():
        db.session.add(FocusShieldSettings(user_id=uid, breaks_per_block=1))
        db.session.commit()
    h = {"X-Extension-Token": TOKEN_A}
    first = client.post("/extension/focus/break", headers=h)
    assert first.status_code == 200
    assert first.get_json()["break_until"] == "2026-03-11T00:35:00Z"
    body = _current(client).get_json()
    assert body["blocking"] is False and body["breaks_left"] == 0
    # Asking again during the break returns the same break, not a new one.
    assert client.post("/extension/focus/break", headers=h).status_code == 200


def test_no_breaks_left_is_a_429(client, monkeypatch):
    uid = _user("a", TOKEN_A, tz="America/Los_Angeles")
    _plan(uid, "2026-03-10", EVENING)
    with App.app.app_context():
        db.session.add(FocusShieldSettings(user_id=uid, breaks_per_block=1))
        db.session.commit()
    h = {"X-Extension-Token": TOKEN_A}
    assert client.post("/extension/focus/break", headers=h).status_code == 200
    # Ten minutes later the break is over; the allowance is spent.
    monkeypatch.setattr(focus_shield_glue, "utcnow", lambda: NOW_UTC + timedelta(minutes=10))
    assert _current(client).get_json()["blocking"] is True
    assert client.post("/extension/focus/break", headers=h).status_code == 429


def test_a_break_with_no_block_running_is_refused(client):
    _user("a", TOKEN_A, tz="Asia/Tokyo")
    assert client.post("/extension/focus/break",
                       headers={"X-Extension-Token": TOKEN_A}).status_code == 409


def test_done_early_unblocks_and_checks_the_block_off(client):
    uid = _user("a", TOKEN_A, tz="America/Los_Angeles")
    _plan(uid, "2026-03-10", EVENING)
    r = client.post("/extension/focus/done", headers={"X-Extension-Token": TOKEN_A})
    assert r.get_json()["released"] is True
    body = _current(client).get_json()
    assert body["blocking"] is False
    with App.app.app_context():
        row = SavedSchedule.query.filter_by(user_id=uid).first()
        assert json.loads(row.progress_json)["d1-b2"]["done"] is True


def test_turning_it_off_means_never_blocking(client):
    uid = _user("a", TOKEN_A, tz="America/Los_Angeles")
    _plan(uid, "2026-03-10", EVENING)
    with App.app.app_context():
        db.session.add(FocusShieldSettings(user_id=uid, enabled=False))
        db.session.commit()
    body = _current(client).get_json()
    assert body["current"] is not None
    assert body["blocking"] is False


# ── web settings ──────────────────────────────────────────────────────


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


def test_settings_need_a_signed_in_student(client):
    assert client.get("/api/focus-shield/settings").status_code == 401


def test_settings_round_trip_and_clean_the_list(client):
    uid = _user("a", TOKEN_A)
    _login(client, uid)
    body = client.get("/api/focus-shield/settings").get_json()
    assert body["enabled"] is True and body["uses_defaults"] is True
    saved = client.post("/api/focus-shield/settings", json={
        "enabled": True,
        "blocklist": ["https://www.twitch.tv/somebody", "twitch.tv", "???"],
        "breaks_per_block": 3,
    }).get_json()
    assert saved["blocklist"] == ["twitch.tv"]
    assert saved["rejected"] == ["???"]
    assert saved["breaks_per_block"] == 3
    # The extension sees the student's list.
    assert _current(client).get_json()["blocklist"] == ["twitch.tv"]
    reset = client.post("/api/focus-shield/settings", json={"reset_blocklist": True}).get_json()
    assert reset["uses_defaults"] is True
