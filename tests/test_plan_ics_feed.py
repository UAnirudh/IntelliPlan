"""The live plan calendar feed: a secret URL calendars subscribe to.

Pins the two halves separately:

* the renderer -- valid RFC 5545 (VCALENDAR/VEVENT, CRLF, folding,
  escaping), UTC times when the student's timezone is known, UIDs that
  survive a re-fetch, and nothing in it the student did not put in the plan;
* the route -- the token is the only credential, so a wrong, malformed,
  rotated or revoked token is a plain 404, and the link can be created,
  replaced and turned off from Settings.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

import pytest

from intelliplan.services import plan_feed

NOW = datetime(2026, 10, 1, 12, 0)  # naive UTC


def plan():
    return {"schedule": [
        {"date": "2026-10-01", "blocks": [
            {"block_id": "d1-b1", "assignment": "Essay; draft, v2\nnotes", "course": "English",
             "start_iso": "2026-09-28T16:00:00", "end_iso": "2026-09-28T16:45:00",
             "due_date": "2026-10-03", "notes": "private thoughts about my teacher"},
            {"block_id": "d1-b2", "assignment": "Long break", "is_break": True,
             "start_iso": "2026-09-28T16:45:00", "end_iso": "2026-09-28T17:00:00"},
            {"block_id": "d1-b3", "assignment": "Problem set", "course": "Calc",
             "time_slot": "7:00 PM - 7:30 PM"},
            {"block_id": "u1", "assignment": "No room", "unplaced": True,
             "time_slot": "No free time — needs rescheduling"},
        ]},
    ]}


def events(ics):
    return re.findall(r"BEGIN:VEVENT\r\n(.*?)END:VEVENT", ics, re.S)


def unfold(ics):
    return ics.replace("\r\n ", "")


# ── Renderer ──────────────────────────────────────────────────────────


def test_is_a_well_formed_calendar():
    ics = plan_feed.build_plan_calendar(plan(), tz_name="America/Los_Angeles", now=NOW)
    assert ics.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n")
    assert ics.endswith("END:VCALENDAR\r\n")
    assert "\n" not in ics.replace("\r\n", "")  # CRLF only
    assert ics.count("BEGIN:VEVENT") == ics.count("END:VEVENT") == 3  # 2 blocks + 1 due date
    for ev in events(ics):
        assert re.search(r"^UID:\S+@intelliplan\.tech\r$", ev, re.M)
        assert "DTSTAMP:20261001T120000Z" in ev
    for line in ics.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75
    assert "METHOD:PUBLISH" in ics and "REFRESH-INTERVAL;VALUE=DURATION:PT1H" in ics


def test_times_are_utc_when_the_timezone_is_known():
    ics = plan_feed.build_plan_calendar(plan(), tz_name="America/Los_Angeles", now=NOW)
    # 4:00 PM PDT on Oct 1 -> 23:00Z; the ISO's stale generation date is ignored.
    assert "DTSTART:20261001T230000Z" in ics
    assert "DTEND:20261001T234500Z" in ics
    # time_slot fallback: 7 PM PDT -> 02:00Z next day.
    assert "DTSTART:20261002T020000Z" in ics


def test_floating_wall_clock_when_no_timezone_is_recorded():
    ics = plan_feed.build_plan_calendar(plan(), tz_name="", now=NOW)
    assert "DTSTART:20261001T160000\r\n" in ics
    assert "Z\r\nDTEND" not in ics


def test_text_is_escaped():
    ics = unfold(plan_feed.build_plan_calendar(plan(), now=NOW))
    assert "SUMMARY:Essay\\; draft\\, v2\\nnotes (English)" in ics
    assert plan_feed.ics_escape("a\\b") == "a\\\\b"


def test_long_lines_fold_on_character_boundaries():
    long = {"schedule": [{"date": "2026-10-01", "blocks": [
        {"assignment": "Ünïcödé " * 20, "course": "Français", "time_slot": "4:00 PM - 5:00 PM"}]}]}
    ics = plan_feed.build_plan_calendar(long, now=NOW)
    for line in ics.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75
    assert ("SUMMARY:" + "Ünïcödé " * 20).strip() in unfold(ics)


def test_uids_are_stable_across_fetches():
    a = plan_feed.build_plan_calendar(plan(), now=NOW)
    b = plan_feed.build_plan_calendar(plan(), now=NOW + timedelta(hours=3))
    uids = lambda s: re.findall(r"^UID:(.*)\r$", s, re.M)
    assert uids(a) == uids(b) and len(set(uids(a))) == len(uids(a))
    # ...and an unchanged plan has an unchanged ETag despite the new DTSTAMP.
    assert plan_feed.content_etag(a) == plan_feed.content_etag(b)
    changed = plan()
    changed["schedule"][0]["blocks"][0]["start_iso"] = "2026-09-28T15:00:00"
    assert plan_feed.content_etag(plan_feed.build_plan_calendar(changed, now=NOW)) != plan_feed.content_etag(a)


def test_due_dates_are_all_day_transparent_and_deduped():
    items = [{"title": "Essay; draft, v2\nnotes", "course": "English", "due_date": "2026-10-03"},
             {"title": "Lab", "course": "Chem", "due_date": "2026-10-05"},
             {"title": "Ancient", "course": "Chem", "due_date": "2025-01-01"}]
    ics = plan_feed.build_plan_calendar(plan(), items, now=NOW)
    due = [e for e in events(ics) if "VALUE=DATE" in e]
    assert len(due) == 2  # the essay once (plan + list), the lab; the old one dropped
    lab = next(e for e in due if "Lab" in e)
    assert "DTSTART;VALUE=DATE:20261005" in lab and "DTEND;VALUE=DATE:20261006" in lab
    assert "TRANSP:TRANSPARENT" in lab


def test_breaks_and_unplaced_blocks_are_left_out():
    ics = plan_feed.build_plan_calendar(plan(), now=NOW)
    assert "Long break" not in ics and "No room" not in ics


def test_nothing_the_student_did_not_put_in_the_plan():
    ics = plan_feed.build_plan_calendar(plan(), now=NOW, link="https://intelliplan.tech/scheduler")
    assert "private thoughts" not in ics  # block notes stay in the app
    assert "@example.com" not in ics


def test_private_mode_hides_titles_and_courses():
    ics = plan_feed.build_plan_calendar(
        plan(), [{"title": "Lab", "course": "Chem", "due_date": "2026-10-05"}],
        now=NOW, detail="private")
    for word in ("Essay", "English", "Calc", "Problem set", "Lab", "Chem"):
        assert word not in ics
    assert "SUMMARY:Study block" in ics and "SUMMARY:Assignment due" in ics


# ── Route ─────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    import App

    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        yield c
    App.limiter.enabled = True


@pytest.fixture
def user_id():
    import App
    from App import CalendarFeedToken, ManualTask, SavedSchedule, User, db

    email = "feed+owner@example.com"
    with App.app.app_context():
        User.query.filter_by(email=email).delete(synchronize_session=False)
        db.session.commit()
        user = User(email=email, password_hash="x", name="Feed Owner Name")
        db.session.add(user)
        db.session.commit()
        uid = user.id
        today = date.today()
        data = {"schedule": [{"date": today.isoformat(), "blocks": [
            {"block_id": "d1-b1", "assignment": "Bio reading", "course": "Biology",
             "time_slot": "4:00 PM - 4:45 PM"}]}]}
        db.session.add(SavedSchedule(user_id=uid, is_active=True, schedule_data=json.dumps(data)))
        db.session.add(ManualTask(user_id=uid, title="Poster", course="Art",
                                  due_date=(today + timedelta(days=3)).isoformat()))
        db.session.commit()
    yield uid
    with App.app.app_context():
        for model in (CalendarFeedToken, ManualTask, SavedSchedule):
            model.query.filter_by(user_id=uid).delete(synchronize_session=False)
        User.query.filter_by(id=uid).delete(synchronize_session=False)
        db.session.commit()


def login(client, uid):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True


def feed_path(urls):
    from urllib.parse import urlsplit

    return urlsplit(urls["https"]).path


def create(client, **body):
    resp = client.post("/api/calendar/feed", json=body)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def test_managing_the_link_needs_a_login(client):
    assert client.get("/api/calendar/feed").status_code == 401
    assert client.post("/api/calendar/feed", json={}).status_code == 401
    assert client.post("/api/calendar/feed/revoke").status_code == 401


def test_create_gives_subscribe_links_for_every_calendar(client, user_id):
    login(client, user_id)
    assert client.get("/api/calendar/feed").get_json()["active"] is False
    d = create(client)
    assert d["active"] and d["detail"] == "full"
    urls = d["urls"]
    assert urls["webcal"].startswith("webcal://") and urls["webcal"].endswith(".ics")
    assert urls["https"].startswith("https://")
    assert urls["google"].startswith("https://calendar.google.com/calendar/render?cid=webcal%3A%2F%2F")
    # Creating again keeps the same link -- a second device must not break the first.
    assert create(client)["urls"] == urls


def test_the_feed_serves_the_plan_without_a_cookie(client, user_id):
    login(client, user_id)
    urls = create(client)["urls"]
    with client.session_transaction() as sess:
        sess.clear()
    resp = client.get(feed_path(urls))
    assert resp.status_code == 200
    assert resp.mimetype == "text/calendar"
    assert "private" in resp.headers["Cache-Control"]
    body = resp.get_data(as_text=True)
    assert "SUMMARY:Bio reading (Biology)" in body
    assert "SUMMARY:Due: Poster (Art)" in body
    # No identity in the feed: not the email, not the name.
    assert "feed+owner" not in body and "Feed Owner Name" not in body

    again = client.get(feed_path(urls), headers={"If-None-Match": resp.headers["ETag"]})
    assert again.status_code == 304


def test_the_feed_follows_plan_changes(client, user_id):
    import App
    from App import SavedSchedule, db

    login(client, user_id)
    path = feed_path(create(client)["urls"])
    first = client.get(path)
    with App.app.app_context():
        row = SavedSchedule.query.filter_by(user_id=user_id).first()
        data = json.loads(row.schedule_data)
        data["schedule"][0]["blocks"][0]["assignment"] = "Bio quiz prep"
        row.schedule_data = json.dumps(data)
        db.session.commit()
    second = client.get(path, headers={"If-None-Match": first.headers["ETag"]})
    assert second.status_code == 200
    assert "Bio quiz prep" in second.get_data(as_text=True)


@pytest.mark.parametrize("token", [
    "A" * 43,                       # well formed, never issued
    "short",                        # malformed
    "!" * 40,                       # right length, wrong alphabet
])
def test_a_bad_token_is_a_plain_404(client, user_id, token):
    resp = client.get(f"/calendar/{token}.ics")
    assert resp.status_code == 404
    assert "BEGIN:VCALENDAR" not in resp.get_data(as_text=True)


def test_rotate_kills_the_old_link(client, user_id):
    login(client, user_id)
    old = feed_path(create(client)["urls"])
    rotated = client.post("/api/calendar/feed/rotate").get_json()
    new = feed_path(rotated["urls"])
    assert new != old
    assert client.get(old).status_code == 404
    assert client.get(new).status_code == 200


def test_revoke_turns_the_feed_off_and_it_can_come_back(client, user_id):
    login(client, user_id)
    path = feed_path(create(client)["urls"])
    off = client.post("/api/calendar/feed/revoke").get_json()
    assert off["active"] is False
    assert client.get(path).status_code == 404
    assert client.get("/api/calendar/feed").get_json()["active"] is False
    back = create(client)
    assert feed_path(back["urls"]) != path
    assert client.get(path).status_code == 404


def test_private_mode_from_settings(client, user_id):
    login(client, user_id)
    urls = create(client)["urls"]
    d = create(client, detail="private")
    assert d["detail"] == "private" and d["urls"] == urls
    body = client.get(feed_path(urls)).get_data(as_text=True)
    assert "Bio reading" not in body and "Biology" not in body
    assert "SUMMARY:Study block" in body


def test_only_a_hash_is_used_for_lookup(client, user_id):
    import App
    from App import CalendarFeedToken
    import calendar_feed_glue

    login(client, user_id)
    token = feed_path(create(client)["urls"]).rsplit("/", 1)[1][:-4]
    with App.app.app_context():
        row = CalendarFeedToken.query.filter_by(user_id=user_id).first()
        assert row.token_hash == calendar_feed_glue.hash_token(token)
        assert row.token_hash != token
