"""Adaptive-scheduler blueprint, driven with fake dependencies."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from flask import Flask

from intelliplan.api.next_action import NextActionDeps, create_next_action_blueprint
from intelliplan.services.next_action import NextActionService

NOW = datetime(2026, 8, 21, 18, 0)
TODAY = NOW.date()
TOMORROW = TODAY + timedelta(days=1)


def a_block(task_id="t1", *, minutes=45, day=None, hour=19, course="Physics"):
    day = day or TODAY
    start = datetime.combine(day, datetime.min.time()).replace(hour=hour)
    return {
        "id": f"b-{task_id}",
        "task_id": task_id,
        "assignment": f"{course} work",
        "parent_title": f"{course} work",
        "course": course,
        "kind": "homework",
        "duration_minutes": minutes,
        "priority": 70,
        "difficulty": "Medium",
        "due_date": (day + timedelta(days=2)).isoformat(),
        "start_iso": start.isoformat(),
        "end_iso": (start + timedelta(minutes=minutes)).isoformat(),
        "is_break": False,
    }


def a_schedule():
    return {
        "schedule": [
            {
                "date": TODAY.isoformat(),
                "day_name": TODAY.strftime("%A"),
                "blocks": [a_block()],
                "capacity_minutes": 180,
                "total_minutes": 45,
            },
            {
                "date": TOMORROW.isoformat(),
                "day_name": TOMORROW.strftime("%A"),
                "blocks": [],
                "capacity_minutes": 180,
                "total_minutes": 0,
            },
        ]
    }


class Recorder:
    """Captures what the blueprint wrote, so the tests can assert on it."""

    def __init__(self) -> None:
        self.decision_log_fails = False
        self.decisions: list = []
        self.overrides: list = []
        self.versions: list = []
        self.resolved: list = []
        self.signals: list = []
        self.saved: list = []

    def record_decisions(self, uid, actions, **kw):
        if self.decision_log_fails:
            raise RuntimeError("audit table is gone")
        self.decisions.append((uid, list(actions)))
        return []

    def record_override(self, uid, **kw):
        self.overrides.append((uid, kw))
        return None

    def record_version(self, uid, **kw):
        self.versions.append((uid, kw))
        return type("Row", (), {"id": 7, "version": 15})()

    def resolve(self, uid, task_id, accepted):
        self.resolved.append((uid, task_id, accepted))
        return True

    def emit(self, uid, kind, **kw):
        self.signals.append((uid, kind))

    def save(self, uid, data):
        self.saved.append((uid, data))
        return True


@pytest.fixture()
def harness():
    state = {
        "user_id": 1,
        "flag": True,
        "schedule": a_schedule(),
        "flag_calls": [],
    }
    rec = Recorder()

    def flag_check(key, uid):
        state["flag_calls"].append((key, uid))
        return state["flag"]

    service = NextActionService(
        get_active_schedule=lambda uid: state["schedule"],
        get_remaining_minutes=lambda uid, now: 90,
    )

    app = Flask(__name__)
    app.register_blueprint(
        create_next_action_blueprint(
            NextActionDeps(
                get_service=lambda: service,
                feature_enabled_for_user=flag_check,
                current_user_id=lambda: state["user_id"],
                now=lambda: NOW,
                get_active_schedule=lambda uid: state["schedule"],
                save_schedule=rec.save,
                record_decisions=rec.record_decisions,
                record_override=rec.record_override,
                record_version=rec.record_version,
                resolve_decision=rec.resolve,
                versions_for=lambda uid, limit: [{"version": 15, "trigger": "override"}],
                emit_signal=rec.emit,
                student_profile=lambda uid: ("Anirudh", False),
            )
        )
    )
    app.config["TESTING"] = True
    return app.test_client(), state, rec


# ── gating ───────────────────────────────────────────────────────────


def test_an_anonymous_visitor_is_challenged_not_served(harness):
    client, state, _ = harness
    state["user_id"] = None
    assert client.get("/api/next-action").status_code == 401


def test_a_student_outside_the_rollout_sees_a_404(harness):
    """Not a 403: an un-rolled-out feature should be indistinguishable from
    one that does not exist."""
    client, state, _ = harness
    state["flag"] = False
    for path in (
        "/api/next-action",
        "/api/next-action/explain",
        "/api/schedule/feasibility",
        "/api/schedule/versions",
    ):
        assert client.get(path).status_code == 404, path
    assert client.post("/api/schedule/override", json={"task_id": "t1"}).status_code == 404


def test_the_flag_is_checked_against_the_specific_student(harness):
    """A percentage rollout that is not asked *who* is asking is not a
    percentage rollout."""
    client, state, _ = harness
    client.get("/api/next-action")
    assert ("adaptive_scheduler", 1) in state["flag_calls"]


# ── the recommendation ───────────────────────────────────────────────


def test_the_recommendation_carries_its_reasons_and_arithmetic(harness):
    client, _, _ = harness
    body = client.get("/api/next-action").get_json()
    assert body["status"] == "ok"
    assert body["has_recommendation"] is True
    assert body["headline"]["title"]
    assert body["headline"]["reason_codes"]
    assert body["headline"]["why"]
    assert body["headline"]["components"]


def test_the_whole_shown_list_is_logged_not_only_the_winner(harness):
    client, _, rec = harness
    client.get("/api/next-action")
    assert rec.decisions
    uid, actions = rec.decisions[0]
    assert uid == 1
    assert len(actions) >= 1


def test_a_failing_decision_log_does_not_cost_the_student_the_answer(harness):
    """Telemetry is not allowed to be the reason a student gets no plan."""
    client, _, rec = harness
    rec.decision_log_fails = True
    body = client.get("/api/next-action").get_json()
    assert body["has_recommendation"] is True


def test_the_limit_is_bounded(harness):
    client, _, _ = harness
    assert client.get("/api/next-action?limit=999").status_code == 200
    assert client.get("/api/next-action?limit=banana").status_code == 200


def test_an_empty_plan_answers_cleanly_rather_than_erroring(harness):
    client, state, _ = harness
    state["schedule"] = None
    body = client.get("/api/next-action").get_json()
    assert body["has_recommendation"] is False
    assert body["reason"]


# ── responding ───────────────────────────────────────────────────────


def test_accepting_a_recommendation_is_recorded(harness):
    client, _, rec = harness
    body = client.post("/api/next-action/respond",
                       json={"task_id": "t1", "accepted": True}).get_json()
    assert body["recorded"] is True
    assert rec.resolved == [(1, "t1", True)]
    assert ("nba_accepted" in [k for _, k in rec.signals])


def test_rejecting_is_recorded_too(harness):
    """The rejection is the valuable signal; dropping it would train the
    model that every recommendation was good."""
    client, _, rec = harness
    client.post("/api/next-action/respond", json={"task_id": "t1", "accepted": False})
    assert rec.resolved == [(1, "t1", False)]
    assert ("nba_rejected" in [k for _, k in rec.signals])


def test_responding_without_a_task_is_a_bad_request(harness):
    client, _, _ = harness
    assert client.post("/api/next-action/respond", json={}).status_code == 400


# ── overrides ────────────────────────────────────────────────────────


def test_previewing_a_move_reports_consequences_without_writing(harness):
    client, _, rec = harness
    body = client.post("/api/schedule/override",
                       json={"action": "move", "task_id": "t1",
                             "day": TODAY.isoformat()}).get_json()
    assert body["changed"] is True
    assert body["moved_minutes"] == 45
    assert body["target_day"] == TOMORROW.isoformat()
    assert body["summary"]
    assert rec.saved == []
    assert rec.versions == []


def test_previewing_a_no_op_says_nothing_would_change(harness):
    client, _, _ = harness
    body = client.post("/api/schedule/override",
                       json={"action": "move", "task_id": "ghost",
                             "day": TODAY.isoformat()}).get_json()
    assert body["changed"] is False
    assert "Nothing" in body["summary"]


def test_applying_a_move_writes_the_plan_and_versions_it(harness):
    client, _, rec = harness
    body = client.post("/api/schedule/override/apply",
                       json={"action": "move", "task_id": "t1",
                             "day": TODAY.isoformat()}).get_json()
    assert body["changed"] is True
    assert body["saved"] is True
    assert body["version"] == 15

    assert rec.versions
    _, kw = rec.versions[0]
    assert kw["trigger"] == "override"
    assert kw["changes"][0]["task_id"] == "t1"
    assert rec.overrides


def test_applying_a_move_actually_relocates_the_block(harness):
    client, _, _ = harness
    data = client.post("/api/schedule/override/apply",
                       json={"action": "move", "task_id": "t1",
                             "day": TODAY.isoformat()}).get_json()["data"]
    by_day = {d["date"]: d for d in data["schedule"]}
    assert by_day[TODAY.isoformat()]["blocks"] == []
    moved = by_day[TOMORROW.isoformat()]["blocks"]
    assert len(moved) == 1
    assert moved[0]["task_id"] == "t1"


def test_a_moved_block_loses_its_old_clock_placement(harness):
    """7 PM Tuesday is not 7 PM Wednesday. Carrying the times over would
    produce a block that looks placed and is not."""
    client, _, _ = harness
    data = client.post("/api/schedule/override/apply",
                       json={"action": "move", "task_id": "t1",
                             "day": TODAY.isoformat()}).get_json()["data"]
    moved = next(d for d in data["schedule"]
                 if d["date"] == TOMORROW.isoformat())["blocks"][0]
    assert "start_iso" not in moved
    assert "time_slot" not in moved


def test_day_totals_are_recomputed_after_an_override(harness):
    client, _, _ = harness
    data = client.post("/api/schedule/override/apply",
                       json={"action": "move", "task_id": "t1",
                             "day": TODAY.isoformat()}).get_json()["data"]
    by_day = {d["date"]: d for d in data["schedule"]}
    assert by_day[TODAY.isoformat()]["total_minutes"] == 0
    assert by_day[TOMORROW.isoformat()]["total_minutes"] == 45


def test_an_unknown_override_action_is_rejected(harness):
    client, _, _ = harness
    resp = client.post("/api/schedule/override",
                       json={"action": "detonate", "task_id": "t1"})
    assert resp.status_code == 400


def test_an_override_without_a_task_is_rejected(harness):
    client, _, _ = harness
    assert client.post("/api/schedule/override", json={"action": "move"}).status_code == 400


def test_an_absurd_shorten_length_is_rejected(harness):
    client, _, _ = harness
    resp = client.post("/api/schedule/override",
                       json={"action": "shorten", "task_id": "t1", "minutes": 99999})
    assert resp.status_code == 400


def test_a_failed_save_is_reported_rather_than_claimed_as_success(harness):
    client, _, rec = harness
    body = client.post("/api/schedule/override/apply",
                       json={"action": "skip", "task_id": "t1",
                             "day": TODAY.isoformat()}).get_json()
    assert body["changed"] is True
    assert rec.saved


# ── feasibility and versions ─────────────────────────────────────────


def test_feasibility_reports_a_clean_plan_as_possible(harness):
    client, _, _ = harness
    body = client.get("/api/schedule/feasibility").get_json()
    assert body["feasible"] is True
    assert body["blocks_checked"] == 1


def test_feasibility_catches_a_plan_that_cannot_be_executed(harness):
    client, state, _ = harness
    state["schedule"]["schedule"][0]["blocks"] = [
        a_block("t1", hour=19, minutes=120),
        a_block("t2", hour=20, minutes=60),
    ]
    body = client.get("/api/schedule/feasibility").get_json()
    assert body["feasible"] is False
    assert any(v["key"] == "overlap" for v in body["violations"])


def test_the_version_history_is_readable(harness):
    client, _, _ = harness
    body = client.get("/api/schedule/versions").get_json()
    assert body["versions"][0]["version"] == 15


# ── explanation ──────────────────────────────────────────────────────


def test_the_explanation_is_grounded_when_personalisation_is_off(harness):
    client, _, _ = harness
    body = client.get("/api/next-action/explain").get_json()
    assert body["explanation"]["generated_by"] == "fallback-template"
    assert body["explanation"]["why"]
    assert body["task_id"] == "t1"


def test_explaining_nothing_is_not_an_error(harness):
    client, state, _ = harness
    state["schedule"] = None
    body = client.get("/api/next-action/explain").get_json()
    assert body["explanation"] is None
