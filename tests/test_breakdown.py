""""Break it down" + "Just 5 minutes".

Three layers, tested separately because they fail separately:

* the pure step builder (no AI, no DB): steps come from the directions when
  there are any, from the work's shape when there are not, the first step is
  always small, and minutes learn from the student's finished steps;
* placement: steps go into the gaps of the plan the student already has, in
  order, before the deadline, without moving anything;
* the endpoints: persistence, ticking, progress sync, the five-minute start
  reusing the Active timer, and that a finished step session ticks the step.

No network anywhere: AI is reported unavailable, so the deterministic path is
the one under test — which is the path every student without a key gets.
"""

import json
from datetime import date, datetime, timedelta

import pytest

import App
import plan_insert_glue
import scheduler_engine
from App import (ActiveSession, AssignmentStep, SavedSchedule, TaskFeedback,
                 User, db)
from intelliplan.intelligence import breakdown as bd
from intelliplan.services.scheduling import _open_steps, _stepped
from intelliplan.intelligence.planner import PlannerTask

DIRECTIONS = (
    "Read chapter 5 (pages 120-145). Answer questions 1-6 on page 146 in "
    "complete sentences. The chapter covers photosynthesis. Draw and label a "
    "diagram of the chloroplast. Submit your work on Canvas by Friday."
)


# ── Pure: building steps without a model ──────────────────────────────


def test_steps_come_from_the_directions_when_there_are_any():
    b = bd.deterministic_breakdown(title="Ch 5", total_minutes=120, description=DIRECTIONS)
    assert b.method == "directions"
    texts = [s.text for s in b.steps]
    assert any(t.startswith("Read chapter 5") for t in texts)
    assert any(t.startswith("Answer questions 1-6") for t in texts)
    assert any("chloroplast" in t for t in texts)
    # A sentence that only describes the topic is not a step.
    assert not any("covers photosynthesis" in t for t in texts)
    # Every directions step quotes the sentence it came from, verbatim.
    for s in b.steps:
        if s.source == "directions":
            assert s.evidence and s.evidence in DIRECTIONS


def test_order_of_the_directions_is_kept():
    b = bd.deterministic_breakdown(title="Ch 5", total_minutes=120, description=DIRECTIONS)
    texts = [s.text for s in b.steps if s.source == "directions"]
    assert texts.index(next(t for t in texts if t.startswith("Read"))) < \
        texts.index(next(t for t in texts if t.startswith("Submit")))


def test_numbered_lists_on_one_line_split_into_steps():
    items = bd.directive_items("1) Read the article. 2) Write a summary paragraph. 3) Submit it.")
    assert [i.split()[0] for i in items] == ["Read", "Write", "Submit"]


def test_no_directions_falls_back_to_the_shape_of_the_work():
    b = bd.deterministic_breakdown(title="Research paper on climate", total_minutes=300)
    assert b.method == "template"
    assert any("sources" in s.text.lower() for s in b.steps)
    assert any("draft" in s.text.lower() for s in b.steps)


def test_a_problem_set_splits_by_problem_count():
    b = bd.deterministic_breakdown(title="Problems 1-20", total_minutes=120)
    assert b.method == "generic"
    assert any(s.text.startswith("Problems 1–5") for s in b.steps)
    assert any(s.text.startswith("Problems 16–20") for s in b.steps)


def test_unknown_work_still_gets_steps():
    b = bd.deterministic_breakdown(title="Thing", total_minutes=60)
    assert len(b.steps) >= 2


@pytest.mark.parametrize("title,desc", [
    ("Ch 5", DIRECTIONS),
    ("Unit 3 test", ""),
    ("Thing", ""),
    ("Research paper", ""),
])
def test_the_first_step_is_always_small_enough_to_start_now(title, desc):
    b = bd.deterministic_breakdown(title=title, total_minutes=180, description=desc, granularity=2)
    assert b.steps[0].minutes <= 10


@pytest.mark.parametrize("granularity", [1, 2, 3])
def test_granularity_changes_the_size_of_steps(granularity):
    b = bd.deterministic_breakdown(title="Research paper", total_minutes=300, granularity=granularity)
    lo, hi = bd._STEP_COUNT[granularity]
    assert len(b.steps) <= bd.MAX_STEPS
    assert max(s.minutes for s in b.steps) <= bd._MAX_STEP_MINUTES[granularity] + 5
    if granularity == 1:
        assert len(b.steps) <= hi + 1


def test_finer_granularity_means_more_steps():
    counts = [len(bd.deterministic_breakdown(title="Research paper", total_minutes=300,
                                             granularity=g).steps) for g in (1, 2, 3)]
    assert counts[0] < counts[2]


def test_minutes_are_multiples_of_five_and_roughly_the_total():
    b = bd.deterministic_breakdown(title="Essay", total_minutes=120)
    assert all(s.minutes % 5 == 0 and s.minutes >= 5 for s in b.steps)
    assert 90 <= b.total_minutes <= 160


def test_bad_granularity_is_clamped():
    assert bd.deterministic_breakdown(title="x", total_minutes=60, granularity="spicy").granularity == 2
    assert bd.deterministic_breakdown(title="x", total_minutes=60, granularity=9).granularity == 3


# ── Pure: the model path is validated, not trusted ────────────────────


CONTEXT = {"title": "Ch 5", "description": DIRECTIONS, "materials": []}


def test_ai_steps_keep_only_quotes_that_are_really_in_the_source():
    raw = {"steps": [
        {"text": "Skim the chapter headings", "minutes": 5, "source_id": "directions",
         "evidence": "Read chapter 5 (pages 120-145)"},
        {"text": "Answer the questions", "minutes": 40, "source_id": "directions",
         "evidence": "Answer questions 1-6 on page 146"},
        {"text": "Write a 5 page essay", "minutes": 60, "source_id": "directions",
         "evidence": "Write a five page essay on mitochondria"},  # invented
    ]}
    b = bd.parse_ai_steps(raw, CONTEXT, total_minutes=110)
    assert b is not None and b.method == "ai"
    def step(prefix):
        return next(s for s in b.steps if s.text.startswith(prefix))
    assert step("Answer the questions").evidence
    assert step("Write a 5 page essay").evidence == ""


@pytest.mark.parametrize("raw", [None, "not json", {}, {"steps": []},
                                 {"steps": [{"text": "only one", "minutes": 5}]},
                                 "```json\n{\"steps\": 3}\n```"])
def test_unusable_ai_answers_mean_fallback(raw):
    assert bd.parse_ai_steps(raw, CONTEXT, total_minutes=60) is None


def test_ai_prompt_carries_the_real_directions_as_data():
    msgs = bd.ai_messages(title="Ch 5", course="Bio", context=CONTEXT, total_minutes=90)
    assert "untrusted" in msgs[0]["content"]
    payload = json.loads(msgs[1]["content"])
    assert payload["sources"][0]["text"].startswith("Read chapter 5")


# ── Pure: learning from the student's own durations ───────────────────


def test_ratio_prefers_finished_steps():
    assert bd.personal_ratio([(10, 15), (20, 30), (10, 14)], [(60, 30)] * 10)[:2] == (1.5, "steps")


def test_ratio_falls_back_to_task_feedback_then_to_one():
    assert bd.personal_ratio([(10, 15)], [(60, 90)] * 4)[:2] == (1.5, "feedback")
    assert bd.personal_ratio([(10, 15)], [(60, 90)] * 3) == (1.0, "none", 0)


def test_ratio_is_bounded_against_a_timer_left_running():
    assert bd.personal_ratio([(10, 600)] * 5)[0] == bd.RATIO_BOUNDS[1]
    assert bd.personal_ratio([(60, 1)] * 5)[0] == bd.RATIO_BOUNDS[0]


def test_calibration_scales_steps_but_not_the_starter():
    b = bd.deterministic_breakdown(title="Essay", total_minutes=120)
    slow = bd.calibrate(b, 2.0)
    assert slow.steps[0].minutes == b.steps[0].minutes
    assert slow.total_minutes > b.total_minutes * 1.6
    assert bd.calibrate(b, 1.0) is b


# ── Pure: steps → plan sittings ───────────────────────────────────────


def test_small_steps_share_a_sitting_and_order_is_kept():
    steps = [{"id": 1, "text": "a", "minutes": 5}, {"id": 2, "text": "b", "minutes": 25},
             {"id": 3, "text": "c", "minutes": 40}, {"id": 4, "text": "d", "minutes": 5}]
    groups = bd.group_into_sittings(steps)
    assert [[s["id"] for s in g] for g in groups] == [[1, 2], [3, 4]]


def test_planner_schedules_open_steps_in_order_and_skips_done_ones():
    task = PlannerTask(id="t1", title="Essay", est_minutes=200)
    steps = [{"id": 1, "text": "Plan", "minutes": 20, "done": True},
             {"id": 2, "text": "Draft", "minutes": 50},
             {"id": 3, "text": "Revise", "minutes": 30}]
    staged = _stepped(task, _open_steps(steps))
    assert [t.id for t in staged] == ["t1::steps:2", "t1::steps:3"]
    assert staged[1].depends_on == ("t1::steps:2",)
    assert all(t.parent_title == "Essay" and t.calibrated for t in staged)
    assert sum(t.est_minutes for t in staged) == 80


def test_all_steps_done_means_nothing_left_and_no_steps_means_fall_through():
    assert _open_steps([{"id": 1, "text": "x", "minutes": 5, "done": True}]) == []
    assert _open_steps(None) is None and _open_steps([]) is None


# ── Pure: placement into an existing plan ─────────────────────────────

MON = date(2026, 10, 5)


def windows(day):
    start = datetime.combine(day, datetime.min.time()) + timedelta(hours=16)
    return [scheduler_engine.Window(start, start + timedelta(hours=3))]


def test_inserted_blocks_fill_gaps_in_order_without_moving_anything():
    plan = {"schedule": [{"date": MON.isoformat(), "blocks": [{
        "id": "b7", "assignment": "Math", "duration_minutes": 60,
        "start_iso": f"{MON}T16:00:00", "end_iso": f"{MON}T17:00:00"}]}]}
    placed = scheduler_engine.insert_blocks(
        plan, [{"assignment": "Step 1", "duration_minutes": 30},
               {"assignment": "Step 2", "duration_minutes": 60}],
        windows_for=windows, start=MON, deadline=MON + timedelta(days=3),
    )
    assert [p["placed_label"] for p in placed] == ["Mon 5:05–5:35 PM", "Mon 5:40–6:40 PM"]
    assert [p["id"] for p in placed] == ["b8", "b9"]
    day = plan["schedule"][0]["blocks"]
    assert day[0]["id"] == "b7" and day[0]["start_iso"] == f"{MON}T16:00:00"
    assert len(day) == 3


def test_overflow_goes_to_the_next_day_and_never_past_the_deadline():
    plan = {"schedule": []}
    placed = scheduler_engine.insert_blocks(
        plan, [{"assignment": f"S{i}", "duration_minutes": 80} for i in range(5)],
        windows_for=windows, start=MON, deadline=MON + timedelta(days=1),
    )
    assert [p.get("unplaced", False) for p in placed] == [False, False, False, False, True]
    assert placed[2]["start_iso"].startswith((MON + timedelta(days=1)).isoformat())
    assert "b1" not in [b.get("id") for b in plan["schedule"][0]["blocks"][2:]]
    assert len(plan["schedule"]) == 2


def test_not_before_respects_the_lead_time():
    plan = {"schedule": []}
    placed = scheduler_engine.insert_blocks(
        plan, [{"assignment": "Now-ish", "duration_minutes": 30}],
        windows_for=windows, start=MON, not_before=datetime(2026, 10, 5, 17, 2),
    )
    assert placed[0]["placed_label"] == "Mon 5:05–5:35 PM"


def test_slot_descriptions():
    d = datetime(2026, 10, 7, 11, 30)
    assert scheduler_engine.describe_slot(d, d + timedelta(minutes=45)) == "Wed 11:30 AM–12:15 PM"
    assert scheduler_engine.describe_slot(d.replace(hour=16, minute=0),
                                          d.replace(hour=17, minute=0)) == "Wed 4:00–5:00 PM"


# ── Endpoints ─────────────────────────────────────────────────────────

FIXED_NOW = datetime(2026, 10, 5, 9, 0)  # Monday morning, student's clock


@pytest.fixture
def client(monkeypatch):
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    monkeypatch.setattr("ai_provider.ai_available", lambda: False)
    monkeypatch.setattr(plan_insert_glue, "student_now", lambda uid, hint=None: FIXED_NOW)
    with App.app.test_client() as c:
        with App.app.app_context():
            db.create_all()
            _wipe()
        yield c
        with App.app.app_context():
            _wipe()
    App.limiter.enabled = True


def _wipe():
    ids = [u.id for u in User.query.filter(User.email.like("brk+%")).all()]
    if ids:
        for model in (AssignmentStep, SavedSchedule, ActiveSession, TaskFeedback):
            model.query.filter(model.user_id.in_(ids)).delete(synchronize_session=False)
    User.query.filter(User.email.like("brk+%")).delete(synchronize_session=False)
    db.session.commit()


def make_user(email="brk+a@example.com"):
    with App.app.app_context():
        u = User(email=email, name="Breakdown",
                 password_hash=App.bcrypt.generate_password_hash("pw-12345678").decode())
        db.session.add(u)
        db.session.commit()
        return u.id


def login(client, uid):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True


def save_plan(uid, blocks=()):
    with App.app.app_context():
        db.session.add(SavedSchedule(user_id=uid, is_active=True, name="Plan", schedule_data=json.dumps({
            "schedule": [{"date": FIXED_NOW.date().isoformat(), "day_name": "Monday",
                          "blocks": list(blocks)}]})))
        db.session.commit()


def plan_of(uid):
    with App.app.app_context():
        row = SavedSchedule.query.filter_by(user_id=uid, is_active=True).first()
        return json.loads(row.schedule_data), json.loads(row.progress_json or "{}")


BODY = {"title": "Ch 5 Photosynthesis", "course": "AP Biology", "due_date": "2026-10-09",
        "description": DIRECTIONS, "estimated_time": 90}


def test_break_it_down_without_ai_persists_grounded_steps(client):
    login(client, make_user())
    r = client.post("/api/breakdown", json=BODY)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["method"] == "directions"
    assert body["steps"][0]["minutes"] <= 10
    assert body["next_step"]["id"] == body["steps"][0]["id"]
    again = client.get("/api/breakdown?title=ch 5  photosynthesis").get_json()
    assert [s["id"] for s in again["steps"]] == [s["id"] for s in body["steps"]]


def test_steps_are_scheduled_into_the_saved_plan_and_replace_old_blocks(client):
    uid = make_user()
    login(client, uid)
    save_plan(uid, [{"id": "b1", "assignment": "Ch 5 Photosynthesis", "parent_title": "Ch 5 Photosynthesis",
                     "duration_minutes": 90, "start_iso": "2026-10-05T17:00:00",
                     "end_iso": "2026-10-05T18:30:00", "task_id": "x"}])
    body = client.post("/api/breakdown", json=BODY).get_json()
    placement = body["placement"]
    assert placement["status"] == "ok"
    assert placement["label"].startswith("Scheduled ")
    data, _ = plan_of(uid)
    blocks = [b for d in data["schedule"] for b in d["blocks"]]
    assert all(b.get("task_id") != "x" for b in blocks), "old unfinished block should be replaced"
    # A replaced block's id is never handed to a new block: progress is keyed
    # by block id, and reuse would show the new block as already done.
    assert all(b["id"] != "b1" for b in blocks)
    step_blocks = [b for b in blocks if "::steps:" in str(b.get("task_id"))]
    assert step_blocks and all(b["parent_title"] == "Ch 5 Photosynthesis" for b in step_blocks)
    starts = [b["start_iso"] for b in step_blocks]
    assert starts == sorted(starts)
    assert all(b["start_iso"][:10] <= "2026-10-09" for b in step_blocks)
    scheduled_ids = sorted(i for b in step_blocks for i in b["step_ids"])
    assert scheduled_ids == sorted(s["id"] for s in body["steps"])


def test_no_saved_plan_is_reported_not_an_error(client):
    login(client, make_user())
    body = client.post("/api/breakdown", json=BODY).get_json()
    assert body["placement"]["status"] == "no_plan"


def test_ticking_steps_records_actual_time_and_ticks_the_plan_block(client):
    uid = make_user()
    login(client, uid)
    save_plan(uid)
    body = client.post("/api/breakdown", json=BODY).get_json()
    data, _ = plan_of(uid)
    first_block = next(b for d in data["schedule"] for b in d["blocks"] if b.get("step_ids"))
    for sid in first_block["step_ids"]:
        r = client.patch(f"/api/breakdown/steps/{sid}", json={"done": True, "actual_minutes": 12})
        assert r.status_code == 200
    _, progress = plan_of(uid)
    assert progress[first_block["id"]]["done"] is True
    with App.app.app_context():
        step = db.session.get(AssignmentStep, first_block["step_ids"][0])
        assert step.done and step.actual_minutes == 12
    # Unticking reverses only what steps ticked.
    client.patch(f"/api/breakdown/steps/{first_block['step_ids'][0]}", json={"done": False})
    _, progress = plan_of(uid)
    assert first_block["id"] not in progress
    assert body["steps"]


def test_ticking_without_a_timer_or_answer_records_no_actual(client):
    uid = make_user()
    login(client, uid)
    sid = client.post("/api/breakdown", json=BODY).get_json()["steps"][0]["id"]
    client.patch(f"/api/breakdown/steps/{sid}", json={"done": True})
    with App.app.app_context():
        assert db.session.get(AssignmentStep, sid).actual_minutes is None


def test_finished_steps_calibrate_the_next_breakdown(client):
    uid = make_user()
    login(client, uid)
    base = client.post("/api/breakdown", json={**BODY, "title": "Calib A"}).get_json()
    for s in base["steps"][:3]:
        client.patch(f"/api/breakdown/steps/{s['id']}",
                     json={"done": True, "actual_minutes": s["minutes"] * 2})
    later = client.post("/api/breakdown", json={**BODY, "title": "Calib B"}).get_json()
    assert later["calibration"]["source"] == "steps"
    assert later["calibration"]["ratio"] == 2.0
    first = {s["text"]: s["minutes"] for s in base["steps"]}
    grown = [s for s in later["steps"][1:] if s["text"] in first]
    assert grown and all(s["minutes"] > first[s["text"]] for s in grown)


def test_rebreaking_keeps_finished_steps_as_history_only(client):
    uid = make_user()
    login(client, uid)
    first = client.post("/api/breakdown", json=BODY).get_json()
    client.patch(f"/api/breakdown/steps/{first['steps'][0]['id']}", json={"done": True, "actual_minutes": 5})
    second = client.post("/api/breakdown", json={**BODY, "granularity": 3}).get_json()
    assert all(not s["done"] for s in second["steps"])
    with App.app.app_context():
        old = db.session.get(AssignmentStep, first["steps"][0]["id"])
        assert old.archived and old.actual_minutes == 5


def test_custom_steps_edit_and_delete(client):
    login(client, make_user())
    client.post("/api/breakdown", json=BODY)
    r = client.post("/api/breakdown/steps", json={"title": BODY["title"], "text": "Ask Ms. Lee about Q4", "minutes": 5})
    assert r.status_code == 201
    step = r.get_json()["steps"][-1]
    assert step["source"] == "student"
    r = client.patch(f"/api/breakdown/steps/{step['id']}", json={"text": "Email Ms. Lee", "minutes": 10})
    assert r.get_json()["steps"][-1]["text"] == "Email Ms. Lee"
    r = client.patch(f"/api/breakdown/steps/{step['id']}", json={"delete": True})
    assert all(s["id"] != step["id"] for s in r.get_json()["steps"])


def test_another_students_step_is_a_404(client):
    a = make_user("brk+a@example.com")
    b = make_user("brk+b@example.com")
    login(client, a)
    sid = client.post("/api/breakdown", json=BODY).get_json()["steps"][0]["id"]
    login(client, b)
    assert client.patch(f"/api/breakdown/steps/{sid}", json={"done": True}).status_code == 404
    assert client.post("/api/breakdown/start", json={"step_id": sid}).status_code == 404


def test_title_is_required(client):
    login(client, make_user())
    assert client.post("/api/breakdown", json={}).status_code == 400
    assert client.get("/api/breakdown").status_code == 400


def test_just_five_minutes_starts_an_active_session_on_the_first_open_step(client):
    uid = make_user()
    login(client, uid)
    steps = client.post("/api/breakdown", json=BODY).get_json()["steps"]
    client.patch(f"/api/breakdown/steps/{steps[0]['id']}", json={"done": True, "actual_minutes": 4})
    r = client.post("/api/breakdown/start", json={"title": BODY["title"]})
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["step"]["id"] == steps[1]["id"]
    assert body["session"]["planned_minutes"] == 5
    assert body["session"]["task_id"] == f"step:{steps[1]['id']}"
    assert body["redirect"].startswith("/active")
    # It is the ordinary Active session the timer page resumes.
    current = client.get("/api/active/current").get_json()["session"]
    assert current["id"] == body["session"]["id"]


def test_finishing_the_step_session_as_done_ticks_the_step_with_timer_minutes(client):
    uid = make_user()
    login(client, uid)
    steps = client.post("/api/breakdown", json=BODY).get_json()["steps"]
    session = client.post("/api/breakdown/start", json={"title": BODY["title"]}).get_json()["session"]
    with App.app.app_context():
        row = db.session.get(ActiveSession, session["id"])
        row.started_at = row.started_at - timedelta(minutes=20)
        row.active_seconds = 6 * 60  # what the heartbeats had reported
        db.session.commit()
    r = client.post(f"/api/active/{session['id']}/finish",
                    json={"completed": True, "active_seconds": 7 * 60})
    assert r.status_code == 200
    with App.app.app_context():
        step = db.session.get(AssignmentStep, steps[0]["id"])
        assert step.done is True
        assert step.actual_minutes == 7


def test_stopping_after_five_minutes_without_finishing_leaves_the_step_open(client):
    uid = make_user()
    login(client, uid)
    steps = client.post("/api/breakdown", json=BODY).get_json()["steps"]
    session = client.post("/api/breakdown/start", json={"title": BODY["title"]}).get_json()["session"]
    client.post(f"/api/active/{session['id']}/finish", json={"completed": False, "active_seconds": 300})
    with App.app.app_context():
        assert db.session.get(AssignmentStep, steps[0]["id"]).done is False


def test_five_minutes_with_nothing_broken_down_says_so(client):
    login(client, make_user())
    r = client.post("/api/breakdown/start", json={"title": "Never broken down"})
    assert r.status_code == 404
    assert "Break it down" in r.get_json()["message"]


def test_full_replan_rows_carry_the_students_steps(client):
    uid = make_user()
    login(client, uid)
    client.post("/api/breakdown", json=BODY)
    with App.app.test_request_context():
        from flask_login import login_user
        with App.app.app_context():
            login_user(db.session.get(User, uid))
            rows = App._planner_task_rows([{"title": BODY["title"], "course": "AP Biology",
                                            "due_date": "2026-10-09"}], [])
    assert rows[0]["steps"] and rows[0]["steps"][0]["text"]


def test_cross_site_post_is_refused_like_every_other_write(client):
    login(client, make_user())
    r = client.post("/api/breakdown", json=BODY, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
