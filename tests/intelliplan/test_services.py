"""Tests for BriefingService caching + TodayService composition."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from intelliplan.intelligence.health import CourseGrade
from intelliplan.repositories.assignments import AssignmentRepository
from intelliplan.repositories.signals import SignalRepository
from intelliplan.services.briefing import BriefingService, facts_hash
from intelliplan.services.today import StudentInfo, TodayService


NOW = datetime(2026, 6, 12, 18, 0, 0)
FACTS = {"top_tasks": [{"title": "X"}], "health_score": 80}


def _ai_counter():
    state = {"calls": 0}

    def fake_ai(messages, **kwargs):
        state["calls"] += 1
        return {"headline": f"H{state['calls']}", "body": "B", "tone": "calm-focused"}

    return fake_ai, state


@pytest.fixture()
def seeded(app, db, models):
    with app.app_context():
        db.session.add(models["User"](id=1, email="a@b.com"))
        db.session.add_all([
            models["ManualTask"](
                id=10, user_id=1, title="Chem review packet",
                due_date="2026-06-13", course="AP Chemistry",
                estimated_time=45, done=False,
            ),
            models["ManualTask"](
                id=11, user_id=1, title="Unit 4 Test",
                due_date="2026-06-15", course="AP Chemistry",
                estimated_time=60, done=False,
            ),
        ])
        db.session.commit()
    yield


class TestBriefingService:
    def test_changed_provider_policy_invalidates_both_cache_paths(self, app, db, models, seeded):
        fake_ai, state = _ai_counter()
        permission = {'providers': frozenset({'groq', 'gemini'})}
        with app.app_context():
            svc = BriefingService(models['BriefingCache'], db.session,
                                  ai_chat_json=fake_ai, now=lambda: NOW,
                                  authorize_ai=lambda uid: permission['providers'])
            svc.get_or_refresh(1, FACTS)
            permission['providers'] = frozenset({'groq'})
            assert svc.peek(1) is None
            assert svc.get_or_refresh(1, FACTS).cached is False
            assert state['calls'] == 2
            assert svc.peek(1).headline == 'H2'

    def test_policy_failure_returns_template_before_cache_or_provider(self):
        from unittest.mock import Mock

        session = Mock()
        ai = Mock()
        svc = BriefingService(object, session, ai_chat_json=ai,
                              authorize_ai=Mock(side_effect=RuntimeError('Policy unavailable')))
        assert svc.get_or_refresh(1, FACTS).generated_by == 'fallback-template'
        assert svc.peek(1) is None
        session.query.assert_not_called()
        ai.assert_not_called()

    def test_revocation_blocks_fresh_and_stale_cached_text(self, app, db, models, seeded):
        fake_ai, state = _ai_counter()
        permission = {"providers": frozenset({'groq'})}
        with app.app_context():
            svc = BriefingService(models["BriefingCache"], db.session,
                                  ai_chat_json=fake_ai, now=lambda: NOW,
                                  authorize_ai=lambda uid: permission['providers'])
            svc.get_or_refresh(1, FACTS)
            permission['providers'] = frozenset()
            text = svc.get_or_refresh(1, FACTS)
            assert text.generated_by == 'fallback-template'
            assert svc.peek(1) is None
            assert state['calls'] == 1

    def test_provider_change_during_generation_discards_output(self, app, db, models, seeded):
        permission = {'providers': frozenset({'groq', 'gemini'})}

        def ai(messages, **kwargs):
            permission['providers'] = frozenset({'groq'})
            return {'headline': 'must not publish', 'body': 'must not store'}

        with app.app_context():
            svc = BriefingService(models['BriefingCache'], db.session, ai_chat_json=ai,
                                  authorize_ai=lambda uid: permission['providers'])
            result = svc.get_or_refresh(1, FACTS)
            assert result.generated_by == 'fallback-template'
            assert db.session.query(models['BriefingCache']).filter_by(user_id=1).first() is None

    def test_first_call_hits_ai_and_caches(self, app, db, models, seeded) -> None:
        fake_ai, state = _ai_counter()
        with app.app_context():
            svc = BriefingService(models["BriefingCache"], db.session,
                                  ai_chat_json=fake_ai, now=lambda: NOW)
            first = svc.get_or_refresh(1, FACTS, student_name="A")
            second = svc.get_or_refresh(1, FACTS, student_name="A")
        assert state["calls"] == 1
        assert first.cached is False
        assert second.cached is True
        assert second.headline == "H1"

    def test_changed_facts_invalidate(self, app, db, models, seeded) -> None:
        fake_ai, state = _ai_counter()
        with app.app_context():
            svc = BriefingService(models["BriefingCache"], db.session,
                                  ai_chat_json=fake_ai, now=lambda: NOW)
            svc.get_or_refresh(1, FACTS)
            svc.get_or_refresh(1, dict(FACTS, health_score=60))
        assert state["calls"] == 2

    def test_expiry_invalidates(self, app, db, models, seeded) -> None:
        fake_ai, state = _ai_counter()
        clock = {"now": NOW}
        with app.app_context():
            svc = BriefingService(models["BriefingCache"], db.session,
                                  ai_chat_json=fake_ai, now=lambda: clock["now"])
            svc.get_or_refresh(1, FACTS)
            clock["now"] = NOW + timedelta(hours=7)
            svc.get_or_refresh(1, FACTS)
        assert state["calls"] == 2

    def test_force_refresh(self, app, db, models, seeded) -> None:
        fake_ai, state = _ai_counter()
        with app.app_context():
            svc = BriefingService(models["BriefingCache"], db.session,
                                  ai_chat_json=fake_ai, now=lambda: NOW)
            svc.get_or_refresh(1, FACTS)
            svc.get_or_refresh(1, FACTS, force=True)
        assert state["calls"] == 2

    def test_facts_hash_stable(self) -> None:
        assert facts_hash({"b": 1, "a": 2}) == facts_hash({"a": 2, "b": 1})
        assert facts_hash({"a": 1}) != facts_hash({"a": 2})

    def test_peek_returns_stale(self, app, db, models, seeded) -> None:
        fake_ai, _ = _ai_counter()
        with app.app_context():
            svc = BriefingService(models["BriefingCache"], db.session,
                                  ai_chat_json=fake_ai, now=lambda: NOW)
            assert svc.peek(1) is None
            svc.get_or_refresh(1, FACTS)
            peeked = svc.peek(1)
        assert peeked is not None and peeked.cached is True


class TestTodayService:
    def _service(self, app, db, models, fake_ai, health_access=None) -> TodayService:
        briefing = BriefingService(models["BriefingCache"], db.session,
                                   ai_chat_json=fake_ai, now=lambda: NOW)
        repo = AssignmentRepository(models["ManualTask"], db.session)
        return TodayService(
            assignments_repo=repo,
            briefing_service=briefing,
            get_student=lambda uid: StudentInfo(
                user_id=uid, name="Anirudh U", grade_level="11th grade",
                availability={"saturday": 240, "sunday": 240},
                personalization_enabled=True,
            ),
            get_grades=lambda uid: (CourseGrade("AP Chemistry", 88.0, (88.0, 90.0, 91.0)),),
            get_completion_rate_7d=lambda uid: 0.85,
            health_snapshot_model=models["HealthSnapshot"],
            health_access=health_access,
            session=db.session,
            now=lambda: NOW,
        )

    def test_denied_history_never_reads_or_writes_snapshot(self):
        from unittest.mock import Mock

        session = Mock()
        svc = TodayService(assignments_repo=None, briefing_service=None, get_student=lambda uid: None,
                           health_snapshot_model=object, session=session, health_access=lambda uid: False)
        assert svc._yesterday_health(1, NOW.date()) is None
        svc._persist_health(1, NOW.date(), None, None)
        session.query.assert_not_called()
        session.commit.assert_not_called()

    def test_history_revoked_during_read_is_not_returned(self, app, db, models, seeded):
        from unittest.mock import Mock

        with app.app_context():
            db.session.add(models['HealthSnapshot'](user_id=1, snapshot_date=NOW.date() - timedelta(days=1), score=31))
            db.session.commit()
            checks = Mock(side_effect=[True, False])
            svc = self._service(app, db, models, lambda *args, **kwargs: {}, health_access=checks)
            assert svc._yesterday_health(1, NOW.date()) is None
            assert checks.call_count == 2

    @pytest.mark.parametrize('existing', [False, True])
    def test_history_revocation_before_commit_rolls_back_snapshot(self, app, db, models, seeded, existing):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from intelliplan.domain.plan import HealthScore

        with app.app_context():
            Model = models['HealthSnapshot']
            if existing:
                db.session.add(Model(user_id=1, snapshot_date=NOW.date(), score=12))
                db.session.commit()
            checks = Mock(side_effect=[True, True, False])
            svc = self._service(app, db, models, lambda *args, **kwargs: {}, health_access=checks)
            svc._persist_health(1, NOW.date(), HealthScore(score=70, delta_vs_yesterday=0,
                                tier='steady', components=(), summary=''),
                                SimpleNamespace(completion_rate_7d=.5, schedule_balance_index=.5))
            row = db.session.query(Model).filter_by(user_id=1, snapshot_date=NOW.date()).first()
            assert (row.score if row else None) == (12 if existing else None)
            assert checks.call_count == 3

    def test_build_full_payload(self, app, db, models, seeded) -> None:
        fake_ai, _ = _ai_counter()
        with app.app_context():
            payload = self._service(app, db, models, fake_ai).build(1)

        assert payload.student_name == "Anirudh U"
        assert len(payload.plan) == 2
        # Review packet due sooner + prep-for-test bonus → ranked first
        assert payload.plan[0].assignment.title == "Chem review packet"
        assert payload.plan[0].priority.score > payload.plan[1].priority.score
        assert payload.briefing.headline
        assert len(payload.forecast.days) == 7
        assert 0 <= payload.health.score <= 100

    def test_dependency_bonus_applied_via_context(self, app, db, models, seeded) -> None:
        fake_ai, _ = _ai_counter()
        with app.app_context():
            payload = self._service(app, db, models, fake_ai).build(1)
        review = payload.plan[0]
        keys = {c.key for c in review.priority.rationale}
        assert "dependency" in keys  # "review packet" before "Unit 4 Test"

    def test_health_snapshot_persisted(self, app, db, models, seeded) -> None:
        fake_ai, _ = _ai_counter()
        with app.app_context():
            payload = self._service(app, db, models, fake_ai).build(1)
            row = models["HealthSnapshot"].query.filter_by(
                user_id=1, snapshot_date=NOW.date()).first()
            assert row is not None
            assert row.score == payload.health.score
            assert row.components_json  # structured breakdown saved

    def test_delta_uses_prior_snapshot(self, app, db, models, seeded) -> None:
        fake_ai, _ = _ai_counter()
        with app.app_context():
            # Seed yesterday's snapshot at a higher score.
            db.session.add(models["HealthSnapshot"](
                user_id=1, snapshot_date=NOW.date() - timedelta(days=1),
                score=95, components_json="[]",
            ))
            db.session.commit()
            payload = self._service(app, db, models, fake_ai).build(1)
        assert payload.health.delta_vs_yesterday == payload.health.score - 95

    def test_second_build_skips_ai(self, app, db, models, seeded) -> None:
        fake_ai, state = _ai_counter()
        with app.app_context():
            svc = self._service(app, db, models, fake_ai)
            svc.build(1)
            svc.build(1)
        assert state["calls"] == 1  # unchanged facts → cache hit

    def test_serializes_to_api_shape(self, app, db, models, seeded) -> None:
        from intelliplan.api.serialize import today_to_dict

        fake_ai, _ = _ai_counter()
        with app.app_context():
            payload = self._service(app, db, models, fake_ai).build(1)
        data = today_to_dict(payload)
        assert set(data) == {"generated_at", "student", "briefing", "plan",
                             "forecast", "health", "meta"}
        assert data["meta"]["schema_version"] == 1
        assert data["student"]["greeting"] == "Good evening"
        assert data["plan"][0]["priority"]["score"] >= data["plan"][1]["priority"]["score"]
        assert all(c["reason"] for c in data["plan"][0]["priority"]["rationale"])


class TestSignalRepository:
    def test_emit_inserts_row(self, app, db, models, seeded) -> None:
        with app.app_context():
            repo = SignalRepository(models["StudentSignal"], db.session)
            ok = repo.emit(1, "briefing_seen", subject_type="briefing",
                           subject_ref="h1", value={"source": "test"})
            assert ok is True
            rows = models["StudentSignal"].query.filter_by(user_id=1).all()
            assert len(rows) == 1
            assert rows[0].kind == "briefing_seen"

    def test_emit_failure_returns_false(self, app, db, models) -> None:
        class Broken:
            def __init__(self, **kw):
                raise RuntimeError("boom")

        with app.app_context():
            repo = SignalRepository(Broken, db.session)
            assert repo.emit(1, "x") is False
