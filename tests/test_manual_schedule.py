"""Hand-built study days.

The generated scheduler decides placement; this one does not decide
anything. So the tests are about what it refuses: a day with two things
at the same time, a block that ends before it starts, a block long enough
that nobody could sit through it. A manual builder that accepts those is
worse than no manual builder, because the student trusts what they typed.
"""

import pytest

import App
from App import ManualPlanPreset, SavedSchedule, User, db
from intelliplan.api import manual_schedule as ms


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            ManualPlanPreset.query.delete()
            SavedSchedule.query.delete()
            User.query.filter(User.email.like("manual+%")).delete(
                synchronize_session=False)
            db.session.commit()
        yield c
        with App.app.app_context():
            ManualPlanPreset.query.delete()
            SavedSchedule.query.delete()
            User.query.filter(User.email.like("manual+%")).delete(
                synchronize_session=False)
            db.session.commit()
    App.limiter.enabled = True


def make_user(email="manual+a@example.com"):
    with App.app.app_context():
        u = User(email=email,
                 password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
                 name="Manual Tester")
        db.session.add(u)
        db.session.commit()
        return u.id


def login(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


EVENING = [
    {"assignment": "Chem problem set", "course": "AP Chem", "start": "17:00", "end": "18:00"},
    {"assignment": "Break", "start": "18:00", "end": "18:15", "is_break": True},
    {"assignment": "Hamlet essay", "course": "AP English", "start": "18:15", "end": "19:30"},
]


# ── parsing ───────────────────────────────────────────────────────


@pytest.mark.parametrize("text,expected", [
    ("17:00", 17 * 60),
    ("5:00 PM", 17 * 60),
    ("5:00PM", 17 * 60),
    ("12:00 AM", 0),
    ("12:30 PM", 12 * 60 + 30),
    ("9:05", 9 * 60 + 5),
])
def test_times_are_read_in_either_notation(text, expected):
    assert ms._parse_hhmm(text) == expected


@pytest.mark.parametrize("text", ["", "lunchtime", "25:00", "10:75", None])
def test_a_non_time_is_rejected_rather_than_guessed_at(text):
    assert ms._parse_hhmm(text) is None


# ── what the builder refuses ──────────────────────────────────────


def test_overlapping_blocks_are_named_not_silently_stacked(client):
    blocks, errors = ms.normalize_blocks([
        {"assignment": "Chem", "start": "17:00", "end": "18:30"},
        {"assignment": "English", "start": "18:00", "end": "19:00"},
    ])
    assert any(e["field"] == "overlap" for e in errors)
    msg = [e["message"] for e in errors if e["field"] == "overlap"][0]
    assert "Chem" in msg and "English" in msg and "6:00 PM" in msg


def test_a_block_that_ends_before_it_starts_is_refused(client):
    """11 PM to 1 AM. Rolling it over midnight would hand someone a study
    block at one in the morning that they never asked for."""
    _, errors = ms.normalize_blocks([
        {"assignment": "Late cram", "start": "23:00", "end": "01:00"},
    ])
    assert errors and errors[0]["field"] == "end"
    assert "midnight" in errors[0]["message"]


def test_an_unnamed_block_is_refused(client):
    _, errors = ms.normalize_blocks([{"start": "17:00", "end": "18:00"}])
    assert errors[0]["field"] == "assignment"


def test_a_break_may_go_unnamed(client):
    """A break is self-describing; making someone type "Break" is friction
    for nothing."""
    blocks, errors = ms.normalize_blocks([
        {"start": "17:00", "end": "17:15", "is_break": True}])
    assert not errors
    assert blocks[0]["assignment"] == "Break"


def test_an_absurdly_long_block_is_refused(client):
    _, errors = ms.normalize_blocks([
        {"assignment": "Everything", "start": "08:00", "end": "23:00"}])
    assert errors and "Split it" in errors[0]["message"]


def test_a_two_minute_block_is_refused(client):
    _, errors = ms.normalize_blocks([
        {"assignment": "Blink", "start": "17:00", "end": "17:02"}])
    assert errors and "minimum" in errors[0]["message"]


def test_errors_point_at_the_row_that_is_wrong(client):
    _, errors = ms.normalize_blocks([
        {"assignment": "Fine", "start": "17:00", "end": "18:00"},
        {"assignment": "Broken", "start": "nope", "end": "19:00"},
    ])
    assert errors[0]["index"] == 1
    assert errors[0]["field"] == "start"


def test_blocks_come_back_in_clock_order_whatever_order_they_arrived(client):
    blocks, errors = ms.normalize_blocks([
        {"assignment": "Late", "start": "20:00", "end": "21:00"},
        {"assignment": "Early", "start": "17:00", "end": "18:00"},
    ])
    assert not errors
    assert [b["assignment"] for b in blocks] == ["Early", "Late"]


def test_a_length_may_stand_in_for_an_end_time(client):
    blocks, errors = ms.normalize_blocks([
        {"assignment": "Chem", "start": "17:00", "duration_minutes": 45}])
    assert not errors
    assert blocks[0]["duration_minutes"] == 45
    assert blocks[0]["time_slot"] == "5:00 PM - 5:45 PM"


# ── building ──────────────────────────────────────────────────────


def test_a_built_day_is_shaped_like_the_engine_output(client):
    """Everything downstream — the interactive view, progress ticks, the
    day archive — reads the generated shape. A manual plan that differed
    would render as an empty day."""
    login(client, make_user())
    r = client.post("/api/scheduler/manual/build",
                    json={"days": [{"date": "2026-08-04", "blocks": EVENING}]})
    assert r.status_code == 200
    data = r.get_json()["data"]
    day = data["schedule"][0]
    assert day["day_name"] == "Tuesday"
    assert day["date"] == "2026-08-04"
    assert {"study_minutes", "break_minutes", "total_hours", "workload_level"} <= set(day)
    b = day["blocks"][0]
    assert {"assignment", "duration_minutes", "time_slot",
            "start_iso", "end_iso", "block_id"} <= set(b)
    assert b["start_iso"].startswith("2026-08-04T17:00")


def test_block_ids_are_stable_across_rebuilds(client):
    """The interactive view keys progress off block_id. Regenerating them
    would drop every tick the student had made."""
    login(client, make_user())
    payload = {"days": [{"date": "2026-08-04", "blocks": EVENING}]}
    first = client.post("/api/scheduler/manual/build", json=payload).get_json()
    second = client.post("/api/scheduler/manual/build", json=payload).get_json()
    ids = lambda d: [b["block_id"] for b in d["data"]["schedule"][0]["blocks"]]
    assert ids(first) == ids(second)


def test_breaks_do_not_count_as_study_time(client):
    login(client, make_user())
    day = client.post("/api/scheduler/manual/build", json={
        "days": [{"date": "2026-08-04", "blocks": EVENING}]
    }).get_json()["data"]["schedule"][0]
    assert day["study_minutes"] == 60 + 75
    assert day["break_minutes"] == 15


def test_the_same_date_twice_is_refused(client):
    login(client, make_user())
    r = client.post("/api/scheduler/manual/build", json={"days": [
        {"date": "2026-08-04", "blocks": EVENING},
        {"date": "2026-08-04", "blocks": EVENING},
    ]})
    assert r.status_code == 400
    assert any("twice" in e["message"] for e in r.get_json()["errors"])


def test_nothing_is_saved_when_any_day_is_invalid(client):
    """Half a plan is worse than none — the student would have to work out
    which days landed."""
    login(client, make_user())
    r = client.post("/api/scheduler/manual/build", json={"save": True, "days": [
        {"date": "2026-08-04", "blocks": EVENING},
        {"date": "2026-08-05", "blocks": [
            {"assignment": "A", "start": "17:00", "end": "18:30"},
            {"assignment": "B", "start": "18:00", "end": "19:00"},
        ]},
    ]})
    assert r.status_code == 400
    with App.app.app_context():
        assert SavedSchedule.query.count() == 0


def test_saving_makes_exactly_one_plan_active(client):
    login(client, make_user())
    for d in ("2026-08-04", "2026-08-05"):
        client.post("/api/scheduler/manual/build",
                    json={"save": True, "days": [{"date": d, "blocks": EVENING}]})
    with App.app.app_context():
        assert SavedSchedule.query.filter_by(is_active=True).count() == 1


def test_building_without_save_leaves_the_existing_plan_alone(client):
    login(client, make_user())
    client.post("/api/scheduler/manual/build",
                json={"save": True, "days": [{"date": "2026-08-04", "blocks": EVENING}]})
    client.post("/api/scheduler/manual/build",
                json={"days": [{"date": "2026-08-06", "blocks": EVENING}]})
    with App.app.app_context():
        assert SavedSchedule.query.count() == 1


# ── presets ───────────────────────────────────────────────────────


def test_a_preset_is_dateless_so_it_can_be_reused(client):
    login(client, make_user())
    r = client.post("/api/scheduler/manual/presets",
                    json={"name": "Weekday evening", "blocks": EVENING})
    assert r.status_code == 201
    preset = r.get_json()["preset"]
    assert preset["block_count"] == 3
    for b in preset["blocks"]:
        # Clock times, no calendar times — that is what makes it a routine.
        assert "start_iso" not in b
        assert b["start"] and b["end"]


def test_a_saved_day_can_be_stamped_onto_several_dates(client):
    login(client, make_user())
    pid = client.post("/api/scheduler/manual/presets",
                      json={"name": "Weekday evening", "blocks": EVENING}
                      ).get_json()["preset"]["id"]

    r = client.post("/api/scheduler/manual/build", json={
        "preset_id": pid,
        "days": [{"date": "2026-08-04"}, {"date": "2026-08-05"}, {"date": "2026-08-06"}],
    })
    assert r.status_code == 200
    sched = r.get_json()["data"]["schedule"]
    assert [d["date"] for d in sched] == ["2026-08-04", "2026-08-05", "2026-08-06"]
    assert all(len(d["blocks"]) == 3 for d in sched)


def test_applying_a_preset_records_that_it_was_used(client):
    login(client, make_user())
    pid = client.post("/api/scheduler/manual/presets",
                      json={"name": "Evening", "blocks": EVENING}
                      ).get_json()["preset"]["id"]
    client.post("/api/scheduler/manual/build",
                json={"preset_id": pid, "days": [{"date": "2026-08-04"}]})
    with App.app.app_context():
        assert db.session.get(ManualPlanPreset, pid).times_used == 1


def test_saving_the_same_name_edits_rather_than_duplicates(client):
    login(client, make_user())
    client.post("/api/scheduler/manual/presets",
                json={"name": "Evening", "blocks": EVENING})
    r = client.post("/api/scheduler/manual/presets", json={
        "name": "Evening",
        "blocks": [{"assignment": "Only chem", "start": "17:00", "end": "18:00"}]})
    assert r.get_json()["replaced"] is True
    with App.app.app_context():
        assert ManualPlanPreset.query.count() == 1
        assert ManualPlanPreset.query.first().to_dict()["block_count"] == 1


def test_a_preset_with_an_overlap_is_never_saved(client):
    login(client, make_user())
    r = client.post("/api/scheduler/manual/presets", json={"name": "Bad", "blocks": [
        {"assignment": "A", "start": "17:00", "end": "18:30"},
        {"assignment": "B", "start": "18:00", "end": "19:00"},
    ]})
    assert r.status_code == 400
    with App.app.app_context():
        assert ManualPlanPreset.query.count() == 0


def test_presets_are_capped(client):
    login(client, make_user())
    for i in range(ms.MAX_PRESETS):
        assert client.post("/api/scheduler/manual/presets",
                           json={"name": f"Day {i}", "blocks": EVENING}).status_code == 201
    r = client.post("/api/scheduler/manual/presets",
                    json={"name": "One more", "blocks": EVENING})
    assert r.status_code == 409


def test_one_student_cannot_see_or_delete_anothers_presets(client):
    a = make_user("manual+a@example.com")
    b = make_user("manual+b@example.com")

    login(client, a)
    pid = client.post("/api/scheduler/manual/presets",
                      json={"name": "Mine", "blocks": EVENING}
                      ).get_json()["preset"]["id"]

    login(client, b)
    assert client.get("/api/scheduler/manual/presets").get_json()["presets"] == []
    assert client.delete(f"/api/scheduler/manual/presets/{pid}").status_code == 404
    assert client.post("/api/scheduler/manual/build",
                       json={"preset_id": pid, "days": [{"date": "2026-08-04"}]}
                       ).status_code == 404


def test_a_signed_out_student_can_still_build_and_save(client):
    """Most first-time scheduler traffic is signed out. A manual plan that
    required an account would be unreachable for them."""
    r = client.post("/api/scheduler/manual/presets",
                    json={"name": "Guest evening", "blocks": EVENING})
    assert r.status_code == 201
    assert client.get("/api/scheduler/manual/presets").get_json()["presets"]
