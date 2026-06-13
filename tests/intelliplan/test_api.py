"""Tests for the Command Center blueprint with fake dependencies."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from flask import Flask

from intelliplan.api.command_center import (
    CommandCenterDeps,
    availability_to_minutes,
    create_command_center_blueprint,
    time_range_to_minutes,
)
from intelliplan.domain.assignment import (
    Assignment,
    AssignmentKind,
    AssignmentStatus,
)
from intelliplan.domain.plan import (
    BriefingText,
    DayLoad,
    HealthScore,
    PlannedTask,
    PriorityScore,
    PriorityTier,
    ReasonChip,
    TodayPayload,
    WorkloadForecast,
)


TODAY = date(2026, 6, 12)
NOW = datetime(2026, 6, 12, 18, 0, 0)


def _payload() -> TodayPayload:
    a = Assignment(
        id="manual-abc", title="Chem packet", course="AP Chemistry",
        due_date=TODAY, kind=AssignmentKind.HOMEWORK,
        status=AssignmentStatus.NOT_STARTED, points_possible=10.0,
        est_minutes=45, source="manual",
    )
    score = PriorityScore(
        score=92, tier=PriorityTier.CRITICAL,
        rationale=(ReasonChip(key="urgency", weight=38, reason="Due today"),),
    )
    return TodayPayload(
        generated_at=NOW,
        student_name="Anirudh",
        student_grade_level="11th grade",
        personalization_enabled=True,
        briefing=BriefingText(headline="H", body="B", tone="calm-focused",
                              generated_by="test", cached=True),
        plan=(PlannedTask(assignment=a, priority=score, why_now="Due today",
                          deep_link="/priority"),),
        forecast=WorkloadForecast(
            days=(DayLoad(day=TODAY, committed_minutes=45, prework_minutes=0,
                          available_minutes=120, stress=0.375),),
            heaviest_day=TODAY, summary="Today is your heaviest day — 45 min of work.",
        ),
        health=HealthScore(score=78, delta_vs_yesterday=-4, tier="steady",
                           components=(), summary="ok"),
    )


class FakeService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def build(self, uid: int, *, force_brief: bool = False) -> TodayPayload:
        self.calls.append({"uid": uid, "force": force_brief})
        return _payload()


@pytest.fixture()
def harness():
    service = FakeService()
    signals: list[tuple] = []
    state = {
        "flag": True,
        "uid": 1,
        "stale": [1, 2],
        "cron_token": "sekrit",
    }
    deps = CommandCenterDeps(
        get_service=lambda: service,
        feature_enabled=lambda key: state["flag"],
        current_user_id=lambda: state["uid"],
        emit_signal=lambda uid, kind, **kw: signals.append((uid, kind)),
        stale_briefing_user_ids=lambda: state["stale"],
        cron_token=lambda: state["cron_token"],
    )
    app = Flask(__name__)
    app.register_blueprint(create_command_center_blueprint(deps))
    return app.test_client(), service, signals, state


class TestApiToday:
    def test_returns_payload(self, harness) -> None:
        client, service, _, _ = harness
        resp = client.get("/api/today")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["student"]["name"] == "Anirudh"
        assert data["plan"][0]["priority"]["score"] == 92
        assert data["meta"]["schema_version"] == 1
        assert service.calls == [{"uid": 1, "force": False}]

    def test_401_when_anonymous(self, harness) -> None:
        client, _, _, state = harness
        state["uid"] = None
        resp = client.get("/api/today")
        assert resp.status_code == 401
        assert resp.get_json()["error"] == "auth_required"

    def test_404_when_flag_off(self, harness) -> None:
        client, _, _, state = harness
        state["flag"] = False
        assert client.get("/api/today").status_code == 404


class TestRefresh:
    def test_forces_brief(self, harness) -> None:
        client, service, signals, _ = harness
        resp = client.post("/api/today/refresh")
        assert resp.status_code == 200
        assert service.calls == [{"uid": 1, "force": True}]
        assert (1, "briefing_refreshed") in signals


class TestExplain:
    def test_priority_explain(self, harness) -> None:
        client, _, _, _ = harness
        resp = client.get("/api/today/explain?component=priority&task_id=manual-abc")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["score"] == 92
        assert data["rationale"][0]["key"] == "urgency"

    def test_priority_unknown_task_404(self, harness) -> None:
        client, _, _, _ = harness
        resp = client.get("/api/today/explain?component=priority&task_id=nope")
        assert resp.status_code == 404

    def test_health_explain(self, harness) -> None:
        client, _, _, _ = harness
        resp = client.get("/api/today/explain?component=health")
        assert resp.get_json()["score"] == 78

    def test_forecast_explain(self, harness) -> None:
        client, _, _, _ = harness
        data = client.get("/api/today/explain?component=forecast").get_json()
        assert data["component"] == "forecast"
        assert data["rationale"][0]["committed_min"] == 45

    def test_invalid_component_400(self, harness) -> None:
        client, _, _, _ = harness
        assert client.get("/api/today/explain?component=magic").status_code == 400


class TestCron:
    def test_rejects_bad_token(self, harness) -> None:
        client, _, _, _ = harness
        assert client.post("/cron/refresh-briefings").status_code == 403
        assert client.post("/cron/refresh-briefings",
                           headers={"X-Cron-Token": "wrong"}).status_code == 403

    def test_rejects_when_no_token_configured(self, harness) -> None:
        client, _, _, state = harness
        state["cron_token"] = ""
        assert client.post("/cron/refresh-briefings",
                           headers={"X-Cron-Token": ""}).status_code == 403

    def test_refreshes_stale_users(self, harness) -> None:
        client, service, _, _ = harness
        resp = client.post("/cron/refresh-briefings",
                           headers={"X-Cron-Token": "sekrit"})
        assert resp.status_code == 200
        assert resp.get_json() == {"refreshed": 2, "failed": 0}
        assert all(c["force"] for c in service.calls)


class TestPageRedirects:
    def test_anonymous_redirects_to_login(self, harness) -> None:
        client, _, _, state = harness
        state["uid"] = None
        resp = client.get("/command-center")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_flag_off_redirects_to_classic(self, harness) -> None:
        client, _, _, state = harness
        state["flag"] = False
        resp = client.get("/command-center")
        assert resp.status_code == 302
        assert "classic=1" in resp.headers["Location"]


class TestAvailabilityParsing:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("16:00-19:00", 180),
            ("9:30-11:00", 90),
            ("22:00-01:00", 180),   # crosses midnight
            ("16-19", 180),         # hour-only
            ("", None),
            ("after school", None),
        ],
    )
    def test_time_range(self, raw: str, expected: int | None) -> None:
        assert time_range_to_minutes(raw) == expected

    def test_availability_dict(self) -> None:
        out = availability_to_minutes({"Monday": "16:00-18:00", "tuesday": "garbage"})
        assert out == {"monday": 120}

    def test_all_garbage_returns_none(self) -> None:
        assert availability_to_minutes({"monday": "idk"}) is None
        assert availability_to_minutes(None) is None
        assert availability_to_minutes({}) is None
