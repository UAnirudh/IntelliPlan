"""Timetable + Today timeline through the real app.

Covers the seams: class time reaching the scheduler as busy time (so no
study block can land in class), the timeline payload shape, and the move
endpoint's auth, validation, preview-vs-commit and conflict handling.
No network: calendar lookups and school imports are stubbed.
"""

import io
import json
from datetime import date, datetime, timedelta

import pytest
from flask_login import login_user

import App
from App import ClassMeeting, FeatureFlag, SavedSchedule, TimetableSettings, User, db
from intelliplan.api import timetable as timetable_api
import scheduler_engine

# A Monday far enough ahead that "now" never trims the day.
MONDAY = date.today() + timedelta(days=(7 - date.today().weekday()) % 7 or 7)


@pytest.fixture
def client(monkeypatch):
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    # No calendar is connected in tests; make that explicit so nothing
    # reaches for Google or Microsoft.
    monkeypatch.setattr(App, "_calendar_busy_by_date", lambda *a, **k: {})
    with App.app.test_client() as c:
        with App.app.app_context():
            _clean()
        yield c
        with App.app.app_context():
            _clean()
    App.limiter.enabled = True


def _clean():
    ClassMeeting.query.delete()
    TimetableSettings.query.delete()
    SavedSchedule.query.filter(SavedSchedule.user_id.isnot(None)).delete(synchronize_session=False)
    FeatureFlag.query.filter_by(key="timetable").delete()
    User.query.filter(User.email.like("tt+%")).delete(synchronize_session=False)
    db.session.commit()


def make_user(email="tt+a@example.com"):
    with App.app.app_context():
        u = User(email=email, password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(), name="TT")
        db.session.add(u)
        db.session.commit()
        return u.id


def login(client, uid):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True


def add_class(client, **fields):
    body = {"course": "Chemistry", "start": "09:00", "end": "10:00", **fields}
    r = client.post("/api/timetable/classes", json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def block(bid, title, start, end, day=MONDAY):
    s = datetime.combine(day, datetime.min.time()).replace(hour=start // 60, minute=start % 60)
    e = datetime.combine(day, datetime.min.time()).replace(hour=end // 60, minute=end % 60)
    return {"block_id": bid, "assignment": title, "course": "Math", "duration_minutes": end - start,
            "start_iso": s.isoformat(), "end_iso": e.isoformat(), "time_slot": "", "is_break": False}


def save_plan(uid, blocks, day=MONDAY, progress=None):
    with App.app.app_context():
        db.session.add(SavedSchedule(
            user_id=uid, name="Plan", is_active=True,
            schedule_data=json.dumps({"schedule": [{"date": day.isoformat(), "blocks": blocks}]}),
            progress_json=json.dumps(progress or {}),
        ))
        db.session.commit()


def saved_blocks(uid):
    with App.app.app_context():
        row = SavedSchedule.query.filter_by(user_id=uid, is_active=True).first()
        return json.loads(row.schedule_data)["schedule"][0]["blocks"]


# ── The scheduler never overlaps classes ─────────────────────────────


def _overlaps(a, b):
    return a[0] < b[1] and b[0] < a[1]


def _minute(iso):
    t = datetime.fromisoformat(iso)
    return t.hour * 60 + t.minute


def test_class_time_is_busy_time_for_the_planner(client):
    uid = make_user()
    login(client, uid)
    add_class(client, course="Chem", start="13:00", end="14:30")
    with App.app.test_request_context():
        login_user(db.session.get(User, uid))
        busy = App._planner_busy_by_date(horizon_days=14)
    school_days = [d for d in busy if d.weekday() < 5]
    assert school_days, busy
    assert all(busy[d] == [(13 * 60, 14 * 60 + 30)] for d in school_days)
    assert not any(d.weekday() >= 5 for d in busy)


def test_placed_blocks_never_overlap_a_class():
    classes = {MONDAY: [(12 * 60, 13 * 60), (14 * 60, 15 * 60 + 30)]}
    windows = scheduler_engine.windows_for_date(
        MONDAY, {"Mon": {"start": "11:00", "end": "19:00"}}, "afternoon", None,
        now=datetime.combine(MONDAY - timedelta(days=1), datetime.min.time()), busy_by_date=classes,
    )
    blocks = [{"assignment": f"Task {i}", "duration_minutes": 45, "difficulty": "Medium"} for i in range(6)]
    placed, _overflow = scheduler_engine.place_day_blocks(blocks, windows)
    assert placed
    for b in placed:
        span = (_minute(b["start_iso"]), _minute(b["end_iso"]))
        for cls in classes[MONDAY]:
            assert not _overlaps(span, cls), (b["assignment"], span, cls)


def test_reflow_keeps_dragged_blocks_out_of_class(client):
    """The drag-and-drop re-time path consults the timetable too."""
    uid = make_user()
    login(client, uid)
    add_class(client, course="Band", start="16:00", end="17:30", weekdays=[MONDAY.strftime("%a")])
    data = {"schedule": [{"date": MONDAY.isoformat(), "blocks": [
        {"assignment": "Essay", "duration_minutes": 60, "is_break": False},
        {"assignment": "Problem set", "duration_minutes": 60, "is_break": False},
    ]}]}
    with App.app.test_request_context():
        login_user(db.session.get(User, uid))
        out = App.reflow_schedule(
            data, availability={"Mon": {"start": "15:00", "end": "21:00"}}, commitments="",
            dna=scheduler_engine.StudyDNA(), preferred_time="afternoon",
        )
    placed = [b for b in out["schedule"][0]["blocks"] if not b.get("is_break")]
    assert len(placed) == 2
    for b in placed:
        assert not _overlaps((_minute(b["start_iso"]), _minute(b["end_iso"])), (16 * 60, 17 * 60 + 30))


def test_kill_switch_takes_class_time_out_of_the_planner(client):
    uid = make_user()
    login(client, uid)
    add_class(client)
    with App.app.app_context():
        db.session.add(FeatureFlag(key="timetable", enabled=False))
        db.session.commit()
    with App.app.test_request_context():
        login_user(db.session.get(User, uid))
        assert App._class_busy_by_date(uid, MONDAY, 7) == {}


# ── Timetable API ────────────────────────────────────────────────────


def test_timetable_requires_sign_in(client):
    assert client.get("/api/timetable").status_code == 401
    assert client.post("/api/timetable/classes", json={"course": "X"}).status_code == 401


def test_add_class_validates_times(client):
    login(client, make_user())
    bad = client.post("/api/timetable/classes", json={"course": "Chem", "start": "10:00", "end": "09:00"})
    assert bad.status_code == 400
    half = client.post("/api/timetable/classes", json={"course": "Chem", "start": "10:00"})
    assert half.status_code == 400
    assert client.post("/api/timetable/classes", json={"course": ""}).status_code == 400


def test_classes_are_scoped_to_their_owner(client):
    a = make_user("tt+a@example.com")
    b = make_user("tt+b@example.com")
    login(client, a)
    class_id = add_class(client)["classes"][0]["id"]
    login(client, b)
    assert client.get("/api/timetable").get_json()["classes"] == []
    assert client.delete(f"/api/timetable/classes/{class_id}").status_code == 404


def test_rotation_settings_and_today_is(client):
    login(client, make_user())
    add_class(client, course="Chem", rotation_days=["A"])
    r = client.put("/api/timetable/settings", json={"kind": "ab", "today_is": "B", "date": MONDAY.isoformat()})
    body = r.get_json()
    assert r.status_code == 200, body
    assert body["settings"]["kind"] == "ab" and body["settings"]["anchor_date"] == MONDAY.isoformat()
    assert body["today"]["rotation_label"] == "B Day"
    assert body["classes"][0]["rotation_days"] == [1]
    saturday = MONDAY - timedelta(days=2)
    r = client.put("/api/timetable/settings", json={"today_is": 1, "date": saturday.isoformat()})
    assert r.status_code == 400


def test_skip_days_validation(client):
    login(client, make_user())
    ok = client.put("/api/timetable/settings", json={"skip_days": [
        {"start": MONDAY.isoformat(), "label": "Holiday"}]})
    assert ok.status_code == 200
    assert ok.get_json()["settings"]["skip_days"][0]["end"] == MONDAY.isoformat()
    assert client.put("/api/timetable/settings", json={"skip_days": [{"start": "nope"}]}).status_code == 400


def test_import_maps_and_upserts_without_network(client, monkeypatch):
    uid = make_user()
    login(client, uid)
    monkeypatch.setattr(timetable_api, "_linked_account",
                        lambda kind: {"login_type": kind} if kind == "studentvue" else None)
    payload = {
        "classes": [
            {"course": "Algebra", "period": "1", "room": "101", "teacher": "Kim", "start": "08:00",
             "end": "08:50", "weekdays": [], "rotation_days": [1], "source": "studentvue", "external_id": "S1"},
            {"course": "Art", "period": "2", "room": "", "teacher": "", "start": "", "end": "",
             "weekdays": [], "rotation_days": [2], "source": "studentvue", "external_id": "S2"},
        ],
        "rotation_hint": {"kind": "ab", "length": 2, "today_is": 1, "date": MONDAY.isoformat()},
        "bell": {"1": ["08:00", "08:50"]},
    }
    monkeypatch.setattr(timetable_api, "fetch_timetable", lambda kind, acct: json.loads(json.dumps(payload)))
    r = client.post("/api/timetable/import", json={"source": "auto"})
    body = r.get_json()
    assert r.status_code == 200, body
    assert body["imported"] == 2 and body["untimed"] == 1 and body["source"] == "studentvue"
    assert body["settings"]["kind"] == "ab" and body["settings"]["bell"] == {"1": ["08:00", "08:50"]}
    # Re-import with a class dropped: it leaves the timetable too.
    payload["classes"] = payload["classes"][:1]
    body = client.post("/api/timetable/import", json={"source": "studentvue"}).get_json()
    assert [c["course"] for c in body["classes"]] == ["Algebra"]


def test_import_without_a_connection_asks_for_one(client, monkeypatch):
    login(client, make_user())
    monkeypatch.setattr(timetable_api, "_linked_account", lambda kind: None)
    r = client.post("/api/timetable/import", json={})
    assert r.status_code == 409 and r.get_json()["needs_connection"] is True


def test_photo_import_falls_back_when_ai_is_unavailable(client, monkeypatch):
    login(client, make_user())
    monkeypatch.setattr(App, "ai_available", lambda: False)
    r = client.post("/api/timetable/photo", data={"image": (io.BytesIO(b"\x89PNG..."), "s.png", "image/png")},
                    content_type="multipart/form-data")
    assert r.status_code == 503 and r.get_json()["fallback"] == "manual"


def test_photo_import_returns_drafts_and_saves_nothing(client, monkeypatch):
    login(client, make_user())
    monkeypatch.setattr(App, "ai_available", lambda: True)
    monkeypatch.setattr(App, "ai_vision", lambda **kw: json.dumps({"classes": [
        {"course": "Physics", "start": "10:00", "end": "10:50", "days": ["Mon"]}]}))
    r = client.post("/api/timetable/photo", data={"image": (io.BytesIO(b"img"), "s.png", "image/png")},
                    content_type="multipart/form-data")
    body = r.get_json()
    assert r.status_code == 200 and body["draft"] is True
    assert body["classes"][0]["course"] == "Physics"
    assert client.get("/api/timetable").get_json()["classes"] == []


# ── Timeline ─────────────────────────────────────────────────────────


def test_timeline_requires_sign_in(client):
    assert client.get("/api/today/timeline").status_code == 401
    assert client.post("/api/today/timeline/move", json={}).status_code == 401


def test_timeline_payload_shape(client, monkeypatch):
    uid = make_user()
    login(client, uid)
    add_class(client, course="Chemistry", room="Lab 2", period="3", start="09:00", end="10:00")
    save_plan(uid, [block("b1", "Essay draft", 16 * 60, 17 * 60), block("b2", "Quiz review", 17 * 60 + 10, 17 * 60 + 40)],
              progress={"b2": {"done": True}})
    monkeypatch.setattr(App, "_calendar_busy_by_date",
                        lambda horizon, start=None: {MONDAY: [(19 * 60, 20 * 60)]})
    r = client.get(f"/api/today/timeline?date={MONDAY.isoformat()}&now=08:00")
    body = r.get_json()
    assert r.status_code == 200
    assert body["status"] == "ok" and body["date"] == MONDAY.isoformat()
    assert body["school_day"] is True and body["has_plan"] is True and body["has_timetable"] is True
    assert body["range"] == {"start": 7 * 60, "end": 22 * 60}
    assert [i["kind"] for i in body["items"]] == ["class", "study", "study", "busy"]
    item = body["items"][0]
    for key in ("id", "kind", "title", "subtitle", "start", "end", "start_minute", "end_minute", "label", "movable"):
        assert key in item
    assert item["movable"] is False and item["room"] == "Lab 2"
    study = body["items"][1]
    assert study["id"] == "b1" and study["movable"] is True and study["start"] == "16:00"
    assert body["items"][2]["done"] is True
    assert body["next_class"]["title"] == "Chemistry"
    assert body["counts"] == {"class": 1, "study": 2, "busy": 1}


def test_timeline_rejects_a_bad_date(client):
    login(client, make_user())
    assert client.get("/api/today/timeline?date=31-12-2026").status_code == 400


def test_weekend_timeline_has_no_classes(client):
    uid = make_user()
    login(client, uid)
    add_class(client)
    body = client.get(f"/api/today/timeline?date={(MONDAY - timedelta(days=1)).isoformat()}").get_json()
    assert body["school_day"] is False and body["items"] == []


# ── Moving a block ───────────────────────────────────────────────────


@pytest.mark.parametrize("payload,status", [
    ({}, 400),
    ({"block_id": "b1"}, 400),
    ({"block_id": "b1", "date": "tomorrow", "start": "16:00"}, 400),
    ({"block_id": "b1", "date": MONDAY.isoformat(), "start": "4ish"}, 400),
    ({"block_id": "nope", "date": MONDAY.isoformat(), "start": "16:00"}, 404),
    ({"block_id": "b1", "date": MONDAY.isoformat(), "start": "03:00"}, 200),  # priced, refused
])
def test_move_validation(client, payload, status):
    uid = make_user()
    login(client, uid)
    save_plan(uid, [block("b1", "Essay", 16 * 60, 17 * 60)])
    r = client.post("/api/today/timeline/move", json={**payload, "preview": True})
    assert r.status_code == status, r.get_json()
    if status == 200:
        assert r.get_json()["allowed"] is False


def test_move_without_a_plan_is_404(client):
    login(client, make_user())
    r = client.post("/api/today/timeline/move", json={"block_id": "b1", "date": MONDAY.isoformat(), "start": "16:00"})
    assert r.status_code == 404


def test_preview_then_commit(client):
    uid = make_user()
    login(client, uid)
    save_plan(uid, [block("b1", "Essay", 16 * 60, 17 * 60), block("b2", "Reading", 18 * 60, 18 * 60 + 30)])
    move = {"block_id": "b2", "date": MONDAY.isoformat(), "start": "15:00"}

    preview = client.post("/api/today/timeline/move", json={**move, "preview": True}).get_json()
    assert preview["preview"] is True and preview["saved"] is False and preview["allowed"] is True
    assert preview["block"]["start"] == "15:00" and preview["block"]["end"] == "15:30"
    assert "Nothing else changes" in preview["summary"]
    assert [b["block_id"] for b in saved_blocks(uid)] == ["b1", "b2"]  # untouched

    done = client.post("/api/today/timeline/move", json={**move, "preview": False}).get_json()
    assert done["saved"] is True
    blocks = saved_blocks(uid)
    assert [b["block_id"] for b in blocks] == ["b2", "b1"]  # re-sorted by clock
    assert blocks[0]["start_iso"].endswith("15:00:00") and blocks[0]["moved_by_student"] is True
    assert blocks[1]["start_iso"].endswith("16:00:00")      # nothing else moved
    assert done["timeline"]["items"][0]["id"] == "b2"


def test_a_move_into_class_is_refused_even_without_a_preview(client):
    uid = make_user()
    login(client, uid)
    add_class(client, course="Chemistry", start="09:00", end="10:00")
    save_plan(uid, [block("b1", "Essay", 16 * 60, 17 * 60)])
    move = {"block_id": "b1", "date": MONDAY.isoformat(), "start": "09:30"}
    preview = client.post("/api/today/timeline/move", json={**move, "preview": True}).get_json()
    assert preview["allowed"] is False and preview["conflicts"][0]["kind"] == "class"
    assert "Chemistry" in preview["summary"]
    r = client.post("/api/today/timeline/move", json=move)
    assert r.status_code == 409
    assert saved_blocks(uid)[0]["start_iso"].endswith("16:00:00")


def test_a_move_onto_another_block_is_refused(client):
    uid = make_user()
    login(client, uid)
    save_plan(uid, [block("b1", "Essay", 16 * 60, 17 * 60), block("b2", "Reading", 18 * 60, 18 * 60 + 30)])
    r = client.post("/api/today/timeline/move",
                    json={"block_id": "b2", "date": MONDAY.isoformat(), "start": "16:30"})
    assert r.status_code == 409 and r.get_json()["conflicts"][0]["kind"] == "study"


def test_calendar_busy_time_is_a_warning_not_a_refusal(client, monkeypatch):
    uid = make_user()
    login(client, uid)
    save_plan(uid, [block("b1", "Essay", 16 * 60, 17 * 60)])
    monkeypatch.setattr(App, "_calendar_busy_by_date",
                        lambda horizon, start=None: {MONDAY: [(19 * 60, 20 * 60)]})
    body = client.post("/api/today/timeline/move", json={
        "block_id": "b1", "date": MONDAY.isoformat(), "start": "19:30", "preview": True}).get_json()
    assert body["allowed"] is True and body["warnings"] and "calendar" in body["summary"]


def test_moving_someone_elses_block_finds_nothing(client):
    a = make_user("tt+a@example.com")
    b = make_user("tt+b@example.com")
    save_plan(a, [block("b1", "Essay", 16 * 60, 17 * 60)])
    login(client, b)
    r = client.post("/api/today/timeline/move", json={"block_id": "b1", "date": MONDAY.isoformat(), "start": "15:00"})
    assert r.status_code == 404
    assert saved_blocks(a)[0]["start_iso"].endswith("16:00:00")


def test_flag_off_404s(client):
    login(client, make_user())
    with App.app.app_context():
        db.session.add(FeatureFlag(key="timetable", enabled=False))
        db.session.commit()
    assert client.get("/api/today/timeline").status_code == 404
    assert client.get("/api/timetable").status_code == 404
