"""Tests for the Active study blueprint.

Uses in-memory fakes rather than the ORM: the blueprint's contract is with
the repository interface, and testing it through SQLAlchemy would be
testing SQLAlchemy.
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from flask import Flask

from intelliplan.api.active import (
    MAX_HEARTBEAT_JUMP_SECONDS,
    ActiveDeps,
    create_active_blueprint,
)
from time_utils import utcnow


class FakeRow(SimpleNamespace):
    @property
    def active_minutes(self) -> int:
        return int(round((self.active_seconds or 0) / 60.0))

    @property
    def is_terminal(self) -> bool:
        return self.state in ("completed", "abandoned", "expired")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "state": self.state,
            "active_seconds": self.active_seconds,
            "planned_minutes": self.planned_minutes,
            "completed_work": self.completed_work,
            "task_id": getattr(self, "task_id", None),
            "block_id": getattr(self, "block_id", None),
            "course": getattr(self, "course", ""),
            "due_date": None,
            "pause_count": getattr(self, "pause_count", 0),
            "paused_seconds": getattr(self, "paused_seconds", 0),
        }


class FakeRepo:
    def __init__(self):
        self.rows: dict[int, FakeRow] = {}
        self.next_id = 1
        self.buckets: list[dict] = []
        self.expired_calls = 0

    def start(self, **kw) -> FakeRow:
        row = FakeRow(
            id=self.next_id,
            state="running",
            active_seconds=0,
            paused_seconds=0,
            pause_count=0,
            completed_work=False,
            progress_percent=0,
            started_at=utcnow(),
            ended_at=None,
            focus_enabled=kw.get("focus_enabled", False),
            **{
                k: v
                for k, v in kw.items()
                if k in ("title", "planned_minutes", "course", "kind", "difficulty", "task_id", "block_id")
            },
        )
        row.user_id = kw.get("user_id")
        row.guest_session_id = kw.get("guest_id")
        self.rows[row.id] = row
        self.next_id += 1
        return row

    def get(self, session_id, *, user_id, guest_id):
        return self.rows.get(session_id)

    def active(self, *, user_id, guest_id):
        for row in self.rows.values():
            if row.state in ("running", "paused"):
                return row
        return None

    def expire_stale(self, *, user_id, guest_id, force=False):
        self.expired_calls += 1
        return 0

    def update_progress(self, row, **kw):
        if kw.get("active_seconds") is not None:
            row.active_seconds = max(row.active_seconds, kw["active_seconds"])
        if kw.get("pause_count") is not None:
            row.pause_count = max(row.pause_count, kw["pause_count"])
        if kw.get("state"):
            row.state = kw["state"]
        return row

    def finish(self, row, *, completed, active_seconds=None, **kw):
        if active_seconds is not None:
            row.active_seconds = max(row.active_seconds, active_seconds)
        row.completed_work = completed
        row.state = "completed" if completed else "abandoned"
        row.ended_at = utcnow()
        return row

    def record_focus_bucket(self, session_id, **kw):
        self.buckets.append({"session_id": session_id, **kw})
        return True

    def apply_focus_summary(self, row, **kw):
        row.focus_summary = kw
        return row

    def stats(self, *, user_id, guest_id):
        return {"sessions": len(self.rows)}

    def recent(self, *, user_id, guest_id, limit=20):
        return list(self.rows.values())[:limit]


@pytest.fixture
def ctx():
    repo = FakeRepo()
    finished: list[FakeRow] = []
    plan = {
        "schedule": [
            {
                "date": date.today().isoformat(),
                "blocks": [
                    {"id": "b1", "assignment": "Essay (part 1 of 2)", "parent_title": "Essay",
                     "task_id": "t1", "course": "APUSH", "kind": "project", "difficulty": "Hard",
                     "duration_minutes": 45, "reasons": ["Due in 3 days."], "done": True},
                    {"id": "b2", "assignment": "Break", "is_break": True, "duration_minutes": 10},
                    {"id": "b3", "assignment": "Problem set", "parent_title": "Problem set",
                     "task_id": "t2", "course": "Math", "kind": "homework", "difficulty": "Medium",
                     "duration_minutes": 30, "reasons": ["Finishes 2 days before it's due."]},
                ],
            }
        ]
    }
    app = Flask(__name__, template_folder="../../Main_Project/templates")
    app.register_blueprint(
        create_active_blueprint(
            ActiveDeps(
                get_repository=lambda: repo,
                current_user_id=lambda: 7,
                guest_session_id=lambda: None,
                feature_enabled=lambda key: True,
                get_plan=lambda: plan,
                on_session_finished=finished.append,
            )
        )
    )
    app.config["TESTING"] = True
    return SimpleNamespace(app=app, client=app.test_client(), repo=repo, finished=finished, plan=plan)


def start_body(**over):
    body = {"title": "Problem set", "planned_minutes": 30, "task_id": "t2", "course": "Math"}
    body.update(over)
    return body


# ── Start ─────────────────────────────────────────────────────────────


def test_start_creates_a_running_session(ctx):
    res = ctx.client.post("/api/active/start", json=start_body())
    assert res.status_code == 201
    assert res.get_json()["session"]["state"] == "running"


def test_start_rejects_a_missing_title(ctx):
    assert ctx.client.post("/api/active/start", json=start_body(title="  ")).status_code == 400


@pytest.mark.parametrize("minutes", [0, -5, 999, "abc", None])
def test_start_rejects_impossible_durations(ctx, minutes):
    assert ctx.client.post("/api/active/start", json=start_body(planned_minutes=minutes)).status_code == 400


def test_start_closes_any_session_left_running(ctx):
    ctx.client.post("/api/active/start", json=start_body())
    assert ctx.repo.expired_calls == 0  # FakeRepo.start doesn't call it; real one does
    ctx.client.post("/api/active/start", json=start_body())
    assert len(ctx.repo.rows) == 2


# ── Heartbeat ─────────────────────────────────────────────────────────


def _aged(ctx, sid, minutes):
    """Backdate a session so wall time permits the effort under test.

    Recorded effort is capped by how long the session has actually existed,
    so a test claiming two minutes of work must first let two minutes pass.
    """
    ctx.repo.rows[sid].started_at = utcnow() - timedelta(minutes=minutes)


def test_heartbeat_accumulates_time(ctx):
    sid = ctx.client.post("/api/active/start", json=start_body()).get_json()["session"]["id"]
    _aged(ctx, sid, 5)
    ctx.client.post(f"/api/active/{sid}/heartbeat", json={"active_seconds": 120})
    assert ctx.repo.rows[sid].active_seconds == 120


def test_heartbeat_never_moves_the_timer_backwards(ctx):
    sid = ctx.client.post("/api/active/start", json=start_body()).get_json()["session"]["id"]
    _aged(ctx, sid, 10)
    ctx.client.post(f"/api/active/{sid}/heartbeat", json={"active_seconds": 200})
    ctx.client.post(f"/api/active/{sid}/heartbeat", json={"active_seconds": 5})
    assert ctx.repo.rows[sid].active_seconds == 200


def test_a_single_heartbeat_cannot_fabricate_hours(ctx):
    sid = ctx.client.post("/api/active/start", json=start_body()).get_json()["session"]["id"]
    ctx.client.post(f"/api/active/{sid}/heartbeat", json={"active_seconds": 999_999})
    assert ctx.repo.rows[sid].active_seconds <= MAX_HEARTBEAT_JUMP_SECONDS


def test_recorded_time_cannot_exceed_wall_time(ctx):
    sid = ctx.client.post("/api/active/start", json=start_body()).get_json()["session"]["id"]
    # The session started a moment ago, so even a legal-sized jump is capped
    # by how long the session has actually existed.
    ctx.client.post(f"/api/active/{sid}/heartbeat", json={"active_seconds": 120})
    assert ctx.repo.rows[sid].active_seconds < 60


def test_heartbeat_on_a_finished_session_conflicts(ctx):
    sid = ctx.client.post("/api/active/start", json=start_body()).get_json()["session"]["id"]
    ctx.client.post(f"/api/active/{sid}/finish", json={"completed": True})
    res = ctx.client.post(f"/api/active/{sid}/heartbeat", json={"active_seconds": 60})
    assert res.status_code == 409


def test_heartbeat_for_an_unknown_session_is_404(ctx):
    assert ctx.client.post("/api/active/999/heartbeat", json={}).status_code == 404


def test_pause_state_is_recorded(ctx):
    sid = ctx.client.post("/api/active/start", json=start_body()).get_json()["session"]["id"]
    ctx.client.post(f"/api/active/{sid}/heartbeat", json={"state": "paused", "pause_count": 1})
    assert ctx.repo.rows[sid].state == "paused"


def test_invalid_state_values_are_ignored(ctx):
    sid = ctx.client.post("/api/active/start", json=start_body()).get_json()["session"]["id"]
    ctx.client.post(f"/api/active/{sid}/heartbeat", json={"state": "hacked"})
    assert ctx.repo.rows[sid].state == "running"


# ── Focus ─────────────────────────────────────────────────────────────


def test_focus_buckets_are_stored_when_enabled(ctx):
    sid = ctx.client.post(
        "/api/active/start", json=start_body(focus_enabled=True)
    ).get_json()["session"]["id"]
    ctx.client.post(
        f"/api/active/{sid}/heartbeat",
        json={"focus": {"offset_seconds": 60, "present": 20, "away": 4, "absent": 1, "confidence": 0.7}},
    )
    assert ctx.repo.buckets and ctx.repo.buckets[0]["present"] == 20


def test_focus_is_ignored_when_the_student_did_not_opt_in(ctx):
    sid = ctx.client.post("/api/active/start", json=start_body()).get_json()["session"]["id"]
    ctx.client.post(
        f"/api/active/{sid}/heartbeat",
        json={"focus": {"offset_seconds": 60, "present": 20, "away": 0, "absent": 0, "confidence": 0.9}},
    )
    assert ctx.repo.buckets == []


def test_malformed_focus_payload_does_not_break_the_session(ctx):
    sid = ctx.client.post(
        "/api/active/start", json=start_body(focus_enabled=True)
    ).get_json()["session"]["id"]
    res = ctx.client.post(f"/api/active/{sid}/heartbeat", json={"focus": {"present": "lots"}})
    assert res.status_code == 200


# ── Finish ────────────────────────────────────────────────────────────


def test_finish_marks_completion_and_notifies_the_scheduler(ctx):
    sid = ctx.client.post("/api/active/start", json=start_body()).get_json()["session"]["id"]
    res = ctx.client.post(f"/api/active/{sid}/finish", json={"completed": True, "active_seconds": 100})
    assert res.get_json()["session"]["state"] == "completed"
    assert len(ctx.finished) == 1


def test_stopping_early_records_an_abandonment_not_a_completion(ctx):
    sid = ctx.client.post("/api/active/start", json=start_body()).get_json()["session"]["id"]
    res = ctx.client.post(f"/api/active/{sid}/finish", json={"completed": False})
    assert res.get_json()["session"]["state"] == "abandoned"


def test_finish_is_idempotent(ctx):
    sid = ctx.client.post("/api/active/start", json=start_body()).get_json()["session"]["id"]
    ctx.client.post(f"/api/active/{sid}/finish", json={"completed": True})
    res = ctx.client.post(f"/api/active/{sid}/finish", json={"completed": False})
    assert res.status_code == 200
    assert res.get_json()["session"]["state"] == "completed"


def test_a_failing_scheduler_hook_does_not_fail_the_request(ctx):
    app = Flask(__name__)
    repo = FakeRepo()

    def explode(row):
        raise RuntimeError("scheduler is down")

    app.register_blueprint(
        create_active_blueprint(
            ActiveDeps(
                get_repository=lambda: repo,
                current_user_id=lambda: 7,
                guest_session_id=lambda: None,
                on_session_finished=explode,
            )
        )
    )
    client = app.test_client()
    sid = client.post("/api/active/start", json=start_body()).get_json()["session"]["id"]
    assert client.post(f"/api/active/{sid}/finish", json={"completed": True}).status_code == 200


def test_finish_reports_what_the_model_learned(ctx):
    sid = ctx.client.post(
        "/api/active/start", json=start_body(planned_minutes=60)
    ).get_json()["session"]["id"]
    ctx.repo.rows[sid].active_seconds = 30 * 60
    body = ctx.client.post(f"/api/active/{sid}/finish", json={"completed": True}).get_json()
    assert "faster" in body["learned"]["message"]


# ── Next / current ────────────────────────────────────────────────────


def test_next_skips_breaks_and_finished_blocks(ctx):
    body = ctx.client.get("/api/active/next").get_json()
    assert body["next"]["block_id"] == "b3"
    assert body["next"]["title"] == "Problem set"
    assert body["next"]["reasons"]


def test_next_is_null_with_no_plan(ctx):
    ctx.plan["schedule"] = []
    assert ctx.client.get("/api/active/next").get_json()["next"] is None


def test_current_sweeps_stale_sessions_before_answering(ctx):
    ctx.client.get("/api/active/current")
    assert ctx.repo.expired_calls == 1


def test_current_returns_a_resumable_session(ctx):
    ctx.client.post("/api/active/start", json=start_body())
    assert ctx.client.get("/api/active/current").get_json()["session"]["state"] == "running"


# ── Gating ────────────────────────────────────────────────────────────


# ── Device layout selection ───────────────────────────────────────────

IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
IPAD_UA = ("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
           "(KHTML, like Gecko) Version/17.0 Safari/604.1")
ANDROID_PHONE_UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36")
ANDROID_TABLET_UA = ("Mozilla/5.0 (Linux; Android 14; SM-X710) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def _layout(ctx, ua="", query=""):
    html = ctx.client.get("/active" + query, headers={"User-Agent": ua}).get_data(as_text=True)
    return "phone" if "ipaControlBar" in html else "desktop"


@pytest.mark.parametrize("ua", [IPHONE_UA, ANDROID_PHONE_UA])
def test_phones_get_the_phone_layout(ctx, ua):
    assert _layout(ctx, ua) == "phone"


@pytest.mark.parametrize("ua", [IPAD_UA, ANDROID_TABLET_UA, DESKTOP_UA, ""])
def test_tablets_and_desktops_keep_the_full_layout(ctx, ua):
    assert _layout(ctx, ua) == "desktop"


def test_view_override_beats_the_user_agent(ctx):
    assert _layout(ctx, DESKTOP_UA, "?view=phone") == "phone"
    assert _layout(ctx, IPHONE_UA, "?view=desktop") == "desktop"


def test_both_layouts_expose_the_ids_the_shared_controller_drives(ctx):
    required = [
        "ipaEmpty", "ipaEmptyMsg", "ipaEmptyCta", "ipaCard", "ipaCourse", "ipaDue",
        "ipaTitle", "ipaPart", "ipaReasons", "ipaRing", "ipaRingFill", "ipaClock",
        "ipaClockLabel", "ipaStart", "ipaPause", "ipaDone", "ipaQuit", "ipaStatus",
        "ipaNudge", "ipaNudgeText", "ipaNudgeDismiss", "ipaFocusToggle", "ipaFocusNote",
        "ipaFocusState", "ipaFocusStateText", "ipaFinish", "ipaProgress",
        "ipaProgressValue", "ipaDifficulty", "ipaFinishSave", "ipaFinishCancel",
        "ipaLearned", "ipaUpNext",
    ]
    for query in ("?view=desktop", "?view=phone"):
        html = ctx.client.get("/active" + query).get_data(as_text=True)
        missing = [i for i in required if f'id="{i}"' not in html]
        assert not missing, f"{query} is missing {missing}"


def test_both_layouts_load_the_shared_controller(ctx):
    for query in ("?view=desktop", "?view=phone"):
        html = ctx.client.get("/active" + query).get_data(as_text=True)
        assert "ip-active.js" in html, query
        assert "ip-focus.js" in html, query


def test_routes_404_when_the_kill_switch_is_off():
    app = Flask(__name__)
    app.register_blueprint(
        create_active_blueprint(
            ActiveDeps(
                get_repository=lambda: FakeRepo(),
                current_user_id=lambda: 7,
                guest_session_id=lambda: None,
                feature_enabled=lambda key: False,
            )
        )
    )
    assert app.test_client().post("/api/active/start", json=start_body()).status_code == 404


def test_guests_can_study():
    repo = FakeRepo()
    app = Flask(__name__)
    app.register_blueprint(
        create_active_blueprint(
            ActiveDeps(
                get_repository=lambda: repo,
                current_user_id=lambda: None,
                guest_session_id=lambda: "guest-abc",
            )
        )
    )
    res = app.test_client().post("/api/active/start", json=start_body())
    assert res.status_code == 201
    assert repo.rows[1].guest_session_id == "guest-abc"


def test_no_identity_at_all_is_rejected():
    app = Flask(__name__)
    app.register_blueprint(
        create_active_blueprint(
            ActiveDeps(
                get_repository=lambda: FakeRepo(),
                current_user_id=lambda: None,
                guest_session_id=lambda: None,
            )
        )
    )
    assert app.test_client().post("/api/active/start", json=start_body()).status_code == 401
