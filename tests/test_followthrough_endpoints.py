"""One-tap rescheduling and forecasts, through the real app.

These drive ``/api/schedule/adjust`` and ``/api/schedule/forecast`` the way
the scheduler page does: a saved plan, a student intent, and then a look at
what was saved. The engines have their own unit tests; these are about the
seams — ownership, preview vs apply, progress that must not light up the
wrong blocks, and the kill switch.
"""

import json
from datetime import date, datetime, time, timedelta

import pytest

import App
from App import FeatureFlag, ModelPrior, SavedSchedule, StudentSignal, User, db
from intelliplan.intelligence.planner import PlannerTask, build_plan, capacities_from_minutes
from intelliplan.services.scheduling import plan_to_schedule_data

TODAY = date.today()


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            _clean()
        yield c
        with App.app.app_context():
            _clean()
    App.limiter.enabled = True


def _clean():
    SavedSchedule.query.delete()
    StudentSignal.query.filter(StudentSignal.kind.in_(("plan_outcomes", "schedule_adjusted"))).delete(
        synchronize_session=False
    )
    FeatureFlag.query.filter_by(key="followthrough_engine").delete()
    ModelPrior.query.delete()
    User.query.filter(User.email.like("ft+%")).delete(synchronize_session=False)
    db.session.commit()


def make_user(email="ft+a@example.com"):
    with App.app.app_context():
        u = User(
            email=email,
            password_hash=App.bcrypt.generate_password_hash("hunter2ok").decode(),
            name="Follow Through",
        )
        db.session.add(u)
        db.session.commit()
        return u.id


def login(client, user_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


def plan_data(start=TODAY, days=10):
    """A realistic planner-built plan starting ``start``, with block ids."""
    tasks = [
        PlannerTask(id="chem", title="Chem lab report", course="Chemistry", kind="lab",
                    due_date=start + timedelta(days=6), est_minutes=120, priority=75),
        PlannerTask(id="math", title="Math problem set", course="Math", kind="homework",
                    due_date=start + timedelta(days=3), est_minutes=60, priority=60),
        PlannerTask(id="hist", title="History reading", course="History", kind="homework",
                    due_date=start + timedelta(days=8), est_minutes=90, priority=45),
    ]
    plan = build_plan(tasks, capacities_from_minutes(start, [120] * days), today=start)
    # Place blocks as of the morning: with the wall clock, a run late in the
    # evening has no window left today and the fixture has no work today.
    data = plan_to_schedule_data(plan, now=datetime.combine(start, time(8, 0)))
    for di, day in enumerate(data["schedule"], start=1):
        for bi, block in enumerate(day["blocks"], start=1):
            block["block_id"] = f"d{di}-b{bi}"
    return data


def save(user_id, data, progress=None):
    with App.app.app_context():
        db.session.add(SavedSchedule(
            user_id=user_id, name="Plan", schedule_data=json.dumps(data),
            progress_json=json.dumps(progress or {}), is_active=True,
        ))
        db.session.commit()


def saved(user_id):
    with App.app.app_context():
        row = SavedSchedule.query.filter_by(user_id=user_id, is_active=True).first()
        return json.loads(row.schedule_data), json.loads(row.progress_json or "{}")


def work_blocks(data, day=None):
    return [
        b for d in data["schedule"] for b in d["blocks"]
        if not b.get("is_break") and (day is None or d["date"] == day.isoformat())
    ]


def minutes_by_task(data):
    out = {}
    for b in work_blocks(data):
        out[b["task_id"]] = out.get(b["task_id"], 0) + int(b["duration_minutes"])
    for d in data.get("deferred") or []:
        out[d["task_id"]] = out.get(d["task_id"], 0) + int(d["minutes"])
    return out


def adjust(client, **payload):
    return client.post("/api/schedule/adjust", json=payload)


# ── ownership and gating ─────────────────────────────────────────────


def test_no_saved_plan_is_a_clear_not_found(client):
    login(client, make_user())
    r = adjust(client, action="skip_day")
    assert r.status_code == 404
    assert r.get_json()["status"] == "none"


def test_the_kill_switch_hides_every_route(client):
    uid = make_user()
    login(client, uid)
    save(uid, plan_data())
    with App.app.app_context():
        db.session.add(FeatureFlag(key="followthrough_engine", enabled=False))
        db.session.commit()
    assert adjust(client, action="skip_day").status_code == 404
    assert client.get("/api/schedule/forecast").status_code == 404


def test_an_unknown_intent_is_refused_with_a_readable_reason(client):
    uid = make_user()
    login(client, uid)
    save(uid, plan_data())
    r = adjust(client, action="teleport")
    assert r.status_code == 400
    assert r.get_json()["message"]


def test_a_day_in_the_past_cannot_be_cleared(client):
    uid = make_user()
    login(client, uid)
    save(uid, plan_data())
    r = adjust(client, action="skip_day", day=(TODAY - timedelta(days=1)).isoformat())
    assert r.status_code == 400


# ── preview vs apply ─────────────────────────────────────────────────


def test_preview_shows_the_consequence_without_saving_it(client):
    uid = make_user()
    login(client, uid)
    original = plan_data()
    save(uid, original)
    r = adjust(client, action="skip_day", preview=True)
    body = r.get_json()
    assert r.status_code == 200
    assert body["preview"] is True and body["saved"] is False
    assert body["headline"]
    assert not work_blocks(body["data"], TODAY)
    # The saved plan is untouched.
    assert saved(uid)[0] == original


def test_clearing_today_moves_its_work_without_losing_any(client):
    uid = make_user()
    login(client, uid)
    original = plan_data()
    assert work_blocks(original, TODAY), "fixture must have work today"
    save(uid, original)

    body = adjust(client, action="skip_day").get_json()
    assert body["status"] == "ok" and body["saved"] is True
    data, _ = saved(uid)
    assert not work_blocks(data, TODAY)
    # Every minute is still either scheduled or honestly reported as not fitting.
    before, after = minutes_by_task(original), minutes_by_task(data)
    for task_id, minutes in before.items():
        assert after.get(task_id, 0) >= minutes - 1, task_id
    assert body["risk"]["after"] is not None
    assert body["intent"] == "skip_day"


def test_the_student_is_told_what_moved(client):
    uid = make_user()
    login(client, uid)
    save(uid, plan_data())
    body = adjust(client, action="skip_day").get_json()
    assert body["moved_sittings"] >= 1
    assert body["changes"]
    assert all(c["kind"] in ("moved", "added", "removed", "unfit") for c in body["changes"])


def test_less_time_today_keeps_today_within_the_limit(client):
    uid = make_user()
    login(client, uid)
    save(uid, plan_data())
    adjust(client, action="limit_day", minutes=30)
    data, _ = saved(uid)
    assert sum(int(b["duration_minutes"]) for b in work_blocks(data, TODAY)) <= 30


def test_pushing_a_task_keeps_it_off_today(client):
    uid = make_user()
    login(client, uid)
    original = plan_data()
    task_today = work_blocks(original, TODAY)[0]["task_id"]
    save(uid, original)
    adjust(client, action="push", task_id=task_today)
    data, _ = saved(uid)
    assert not [b for b in work_blocks(data, TODAY) if b["task_id"] == task_today]
    assert [b for b in work_blocks(data) if b["task_id"] == task_today]


def test_a_pinned_block_stays_exactly_where_it_was_put(client):
    uid = make_user()
    login(client, uid)
    original = plan_data()
    save(uid, original)
    target = TODAY + timedelta(days=2)
    block = work_blocks(original)[0]
    source = next(d["date"] for d in original["schedule"] if block in d["blocks"])
    adjust(client, action="pin", task_id=block["task_id"], day=target.isoformat(),
           from_day=source, minutes=block["duration_minutes"])
    data, _ = saved(uid)
    on_target = [b for b in work_blocks(data, target) if b["task_id"] == block["task_id"]]
    assert on_target
    assert any("You placed this here" in (b.get("notes") or "") for b in on_target)


def test_marking_work_done_takes_it_off_the_plan(client):
    uid = make_user()
    login(client, uid)
    save(uid, plan_data())
    adjust(client, action="done", task_id="math")
    data, _ = saved(uid)
    assert not [b for b in work_blocks(data) if b["task_id"] == "math"]


def test_extra_time_never_makes_the_plan_riskier(client):
    uid = make_user()
    login(client, uid)
    save(uid, plan_data())
    body = adjust(client, action="add_time", day=(TODAY + timedelta(days=1)).isoformat(),
                  minutes=120, preview=True).get_json()
    before = body["risk"]["if_you_do_nothing"]
    after = body["risk"]["after"]
    assert after["weighted_on_time"] >= before["weighted_on_time"] - 0.02


def test_a_cleared_day_stays_cleared_through_later_adjustments(client):
    uid = make_user()
    login(client, uid)
    original = plan_data()
    save(uid, original)
    tomorrow = TODAY + timedelta(days=1)
    adjust(client, action="skip_day", day=tomorrow.isoformat())
    data, _ = saved(uid)
    assert data["capacity_overrides"][tomorrow.isoformat()] == {"set": 0}
    # A later, unrelated adjustment must not put work back on that day.
    adjust(client, action="done", task_id="hist")
    adjust(client, action="catch_up")
    data, _ = saved(uid)
    assert not work_blocks(data, tomorrow)


def test_extra_time_is_real_time_on_the_clock(client):
    uid = make_user()
    login(client, uid)
    save(uid, plan_data())
    day = TODAY + timedelta(days=2)
    body = adjust(client, action="add_time", day=day.isoformat(), minutes=180).get_json()
    assert body["status"] == "ok"
    data, _ = saved(uid)
    assert data["capacity_overrides"][day.isoformat()] == {"add": 180}
    # Whatever the planner put on that day was placed inside real windows,
    # not spilled off the end of the evening.
    assert not [d for d in data.get("deferred") or [] if "free hours" in (d.get("reason") or "")]


# ── progress must follow the right blocks ────────────────────────────


def test_every_block_gets_a_fresh_id_so_old_ticks_cannot_leak(client):
    uid = make_user()
    login(client, uid)
    original = plan_data()
    old_ids = {b["block_id"] for d in original["schedule"] for b in d["blocks"]}
    save(uid, original)
    adjust(client, action="skip_day", day=(TODAY + timedelta(days=1)).isoformat())
    data, _ = saved(uid)
    new_ids = [b["block_id"] for d in data["schedule"] for b in d["blocks"]]
    assert new_ids and len(new_ids) == len(set(new_ids))
    assert not (set(new_ids) & old_ids)


def test_work_already_done_today_stays_ticked(client):
    uid = make_user()
    login(client, uid)
    original = plan_data()
    first = work_blocks(original, TODAY)[0]
    save(uid, original, progress={first["block_id"]: {"done": True, "checked": []}})

    body = adjust(client, action="skip_day", day=(TODAY + timedelta(days=1)).isoformat()).get_json()
    data, progress = saved(uid)
    carried = [b for b in work_blocks(data, TODAY) if b.get("carried_done")]
    assert len(carried) == 1
    assert progress[carried[0]["block_id"]]["done"] is True
    assert body["progress"] == progress


def test_the_new_blocks_are_ready_for_the_interactive_view(client):
    uid = make_user()
    login(client, uid)
    save(uid, plan_data())
    adjust(client, action="catch_up")
    data, _ = saved(uid)
    for b in work_blocks(data):
        assert b["block_id"] and b.get("kind") and "checklist" in b


# ── training data ────────────────────────────────────────────────────


def test_past_blocks_are_logged_as_outcomes_before_the_plan_is_replaced(client):
    uid = make_user()
    login(client, uid)
    start = TODAY - timedelta(days=3)
    old = plan_data(start=start, days=12)
    past = [b for d in old["schedule"] if d["date"] < TODAY.isoformat() for b in d["blocks"]
            if not b.get("is_break")]
    assert past, "fixture must have past work"
    save(uid, old, progress={past[0]["block_id"]: {"done": True}})
    adjust(client, action="catch_up")
    with App.app.app_context():
        signal = StudentSignal.query.filter_by(user_id=uid, kind="plan_outcomes").first()
        assert signal is not None
        rows = json.loads(signal.value_json)["rows"]
    assert len(rows) == len(past)
    assert sum(1 for r in rows if r["done"]) == 1
    # Numbers and course names only — never an assignment title.
    assert all("title" not in r and "assignment" not in r for r in rows)


# ── forecast ─────────────────────────────────────────────────────────


def test_forecast_gives_on_time_odds_per_assignment(client):
    uid = make_user()
    login(client, uid)
    save(uid, plan_data())
    body = client.get("/api/schedule/forecast").get_json()
    assert body["status"] == "ok"
    keys = {t["key"] for t in body["risk"]["tasks"]}
    assert {"chem", "math", "hist"} <= keys
    for t in body["risk"]["tasks"]:
        assert 0 <= t["percent"] <= 100
        assert t["status"] in ("on_track", "watch", "at_risk")
    assert body["model"]["personalised"] is False
    # With no history there is nothing to claim about this student.
    assert body["insights"] == []


def test_forecast_without_a_plan_says_so(client):
    login(client, make_user())
    assert client.get("/api/schedule/forecast").get_json()["status"] == "none"


# ── population prior cron ────────────────────────────────────────────


def test_refit_cron_refuses_without_the_secret(client, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    r = client.post("/cron/refit-followthrough-prior")
    assert r.status_code == 401


def test_refit_cron_writes_a_prior(client, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    r = client.post("/cron/refit-followthrough-prior", headers={"X-Cron-Secret": "s3cret"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok"
    with App.app.app_context():
        row = ModelPrior.query.filter_by(key="followthrough-v1").first()
        assert row is not None
        assert "bias" in json.loads(row.payload_json)["means"]


# ── autopilot ────────────────────────────────────────────────────────


def past_plan():
    """A plan that started three days ago, with nothing ticked off since."""
    return plan_data(start=TODAY - timedelta(days=3), days=12)


def future_minutes(data):
    return sum(int(b["duration_minutes"]) for d in data["schedule"] if d["date"] >= TODAY.isoformat()
               for b in d["blocks"] if not b.get("is_break"))


def test_autopilot_gives_missed_work_new_days_without_being_asked(client):
    uid = make_user()
    login(client, uid)
    old = past_plan()
    save(uid, old)
    body = client.post("/api/schedule/autopilot", json={}).get_json()
    assert body["acted"] is True
    assert "missed" in body["triggers"]
    assert body["headline"].startswith("Autopilot updated your plan")
    assert body["undo_available"] is True
    # The page never receives the undo copy; the server keeps it.
    assert "undo" not in body["data"]["autopilot"]
    data, _ = saved(uid)
    assert all(d["date"] >= TODAY.isoformat() for d in data["schedule"])
    assert data["autopilot"]["undo"]["data"]["schedule"] == old["schedule"]
    assert future_minutes(data) + sum(d["minutes"] for d in data.get("deferred") or []) >= future_minutes(old)


def test_autopilot_does_nothing_when_nothing_happened(client):
    uid = make_user()
    login(client, uid)
    save(uid, past_plan())
    assert client.post("/api/schedule/autopilot", json={}).get_json()["acted"] is True
    again = client.post("/api/schedule/autopilot", json={}).get_json()
    assert again["acted"] is False


def test_undo_restores_the_old_plan_and_autopilot_stands_down_today(client):
    uid = make_user()
    login(client, uid)
    old = past_plan()
    save(uid, old, progress={"d1-b1": {"done": True}})
    client.post("/api/schedule/autopilot", json={})
    body = client.post("/api/schedule/autopilot/undo").get_json()
    assert body["status"] == "ok"
    data, progress = saved(uid)
    assert data["schedule"] == old["schedule"]
    assert progress == {"d1-b1": {"done": True}}
    assert data["autopilot"]["log"][-1]["undone"] is True
    # Respected: the very next page load does not redo it.
    assert client.post("/api/schedule/autopilot", json={}).get_json()["acted"] is False
    assert client.post("/api/schedule/autopilot/undo").status_code == 409


def test_autopilot_can_be_switched_off(client):
    uid = make_user()
    login(client, uid)
    save(uid, past_plan())
    r = client.post("/api/schedule/autopilot/settings", json={"enabled": False})
    assert r.get_json()["enabled"] is False
    body = client.post("/api/schedule/autopilot", json={}).get_json()
    assert body["acted"] is False and body["enabled"] is False
    assert client.post("/api/schedule/autopilot/settings", json={"enabled": "yes"}).status_code == 400


def test_a_newly_posted_assignment_is_absorbed_into_the_week(client):
    uid = make_user()
    login(client, uid)
    save(uid, plan_data())
    new = {"id": "bio-lab", "title": "Bio lab write-up", "course": "Biology",
           "due_date": (TODAY + timedelta(days=5)).isoformat(), "points_possible": 50,
           "priority": "High"}
    body = client.post("/api/schedule/autopilot", json={"assignments": [new]}).get_json()
    assert body["acted"] is True and "new_work" in body["triggers"]
    data, _ = saved(uid)
    assert [b for b in work_blocks(data) if (b.get("parent_title") or b["assignment"]).startswith("Bio lab")]


def test_submitted_work_is_not_absorbed(client):
    uid = make_user()
    login(client, uid)
    save(uid, plan_data())
    done = {"id": "old-quiz", "title": "Old quiz", "status": "submitted",
            "due_date": (TODAY + timedelta(days=3)).isoformat()}
    assert client.post("/api/schedule/autopilot", json={"assignments": [done]}).get_json()["acted"] is False


def test_a_manual_change_after_autopilot_retires_the_undo(client):
    uid = make_user()
    login(client, uid)
    save(uid, past_plan())
    client.post("/api/schedule/autopilot", json={})
    adjust(client, action="skip_day", day=(TODAY + timedelta(days=1)).isoformat())
    data, _ = saved(uid)
    assert "undo" not in data["autopilot"]
    assert data["autopilot"]["log"]


def test_the_autopilot_cron_catches_up_students_who_never_opened_the_page(client, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    uid = make_user()
    save(uid, past_plan())
    assert client.post("/cron/autopilot").status_code == 401
    body = client.post("/cron/autopilot", headers={"X-Cron-Secret": "s3cret"}).get_json()
    assert body["acted"] >= 1
    data, _ = saved(uid)
    assert all(d["date"] >= TODAY.isoformat() for d in data["schedule"])


# ── in-process scheduler ─────────────────────────────────────────────


def test_scheduled_jobs_run_when_due_and_not_again_until_their_interval(client):
    import followthrough_glue
    from intelliplan.notifications.models import register_lease

    uid = make_user()
    save(uid, past_plan())
    with App.app.app_context():
        Lease = register_lease(db)
        Lease.query.filter(Lease.name.like("followthrough-%")).delete(synchronize_session=False)
        db.session.commit()

    first = followthrough_glue.run_due_jobs(App.app)
    assert first["followthrough-autopilot"]["acted"] >= 1
    assert "sample_size" in first["followthrough-prior"]
    data, _ = saved(uid)
    assert all(d["date"] >= TODAY.isoformat() for d in data["schedule"])

    # Inside the interval: nothing runs, whichever worker asks.
    assert followthrough_glue.run_due_jobs(App.app) == {}

    # Once the interval has passed, it runs again.
    with App.app.app_context():
        Lease = register_lease(db)
        row = db.session.get(Lease, "followthrough-autopilot")
        row.expires_at = row.expires_at - timedelta(days=1)
        db.session.commit()
    again = followthrough_glue.run_due_jobs(App.app)
    assert set(again) == {"followthrough-autopilot"}


def test_the_scheduler_never_starts_under_the_test_runner():
    import followthrough_glue

    assert followthrough_glue.start_scheduler(App.app) is False
