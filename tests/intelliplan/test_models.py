"""Tests for the Command Center ORM models + idempotent registration."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from time_utils import utcnow


def test_register_creates_three_tables(app, db, models) -> None:
    with app.app_context():
        names = {
            models["BriefingCache"].__tablename__,
            models["HealthSnapshot"].__tablename__,
            models["StudentSignal"].__tablename__,
        }
        assert names == {"briefing_cache", "health_snapshots", "student_signals"}


def test_register_is_idempotent(app, db, models) -> None:
    from intelliplan.models import command_center as cc_models

    with app.app_context():
        again = cc_models.register(db)
    assert again[0] is models["BriefingCache"]
    assert again[1] is models["HealthSnapshot"]
    assert again[2] is models["StudentSignal"]


def test_briefing_cache_round_trip(app, db, models) -> None:
    with app.app_context():
        db.session.add(models["User"](id=1, email="a@b.com"))
        db.session.commit()
        row = models["BriefingCache"](
            user_id=1,
            payload_hash="abc",
            text_json='{"headline":"hi"}',
            model="gemini-2.5-flash",
        )
        db.session.add(row)
        db.session.commit()

        fetched = models["BriefingCache"].query.filter_by(user_id=1).first()
        assert fetched.payload_hash == "abc"
        assert fetched.model == "gemini-2.5-flash"
        assert fetched.generated_at is not None
        assert fetched.expires_at > fetched.generated_at


def test_health_snapshot_unique_per_day(app, db, models) -> None:
    with app.app_context():
        db.session.add(models["User"](id=1, email="a@b.com"))
        db.session.commit()
        snap = models["HealthSnapshot"](
            user_id=1, snapshot_date=date(2026, 6, 12), score=82,
            components_json="[]",
        )
        db.session.add(snap)
        db.session.commit()

        duplicate = models["HealthSnapshot"](
            user_id=1, snapshot_date=date(2026, 6, 12), score=70,
            components_json="[]",
        )
        db.session.add(duplicate)
        import pytest
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_student_signal_append(app, db, models) -> None:
    with app.app_context():
        db.session.add(models["User"](id=1, email="a@b.com"))
        db.session.commit()
        db.session.add_all(
            [
                models["StudentSignal"](
                    user_id=1, kind="task_completed",
                    subject_type="assignment", subject_ref="manual-abc",
                    value_json='{"est":45,"actual":60}',
                ),
                models["StudentSignal"](
                    user_id=1, kind="briefing_seen",
                    occurred_at=utcnow() - timedelta(hours=2),
                ),
            ]
        )
        db.session.commit()
        rows = models["StudentSignal"].query.filter_by(user_id=1).all()
        assert len(rows) == 2
        assert {r.kind for r in rows} == {"task_completed", "briefing_seen"}
