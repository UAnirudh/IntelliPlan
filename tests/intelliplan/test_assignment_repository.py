"""Tests for AssignmentRepository against in-memory SQLite."""

from __future__ import annotations

from datetime import date

import pytest
from flask_sqlalchemy import SQLAlchemy

from intelliplan.domain.assignment import AssignmentKind, AssignmentStatus
from intelliplan.repositories.assignments import (
    AssignmentRepository,
    classify_kind,
    parse_due_date,
)


# ── pure-function tests ────────────────────────────────────────────────


class TestClassifyKind:
    @pytest.mark.parametrize(
        "title,expected",
        [
            ("AP Chemistry Final Exam", AssignmentKind.EXAM),
            ("Midterm review", AssignmentKind.EXAM),
            ("History essay rough draft", AssignmentKind.PROJECT),
            ("Group project — presentation", AssignmentKind.PROJECT),
            ("Spanish vocab quiz", AssignmentKind.TEST),
            ("Algebra test 3", AssignmentKind.TEST),
            ("Bio lab report", AssignmentKind.LAB),
            ("Math homework set 4", AssignmentKind.HOMEWORK),
            ("hw 7.2", AssignmentKind.HOMEWORK),
            ("Classwork — chapter notes", AssignmentKind.CLASSWORK),
            ("", AssignmentKind.OTHER),
            ("Reading", AssignmentKind.OTHER),
        ],
    )
    def test_classify(self, title: str, expected: AssignmentKind) -> None:
        assert classify_kind(title) is expected


class TestParseDueDate:
    def test_iso_string(self) -> None:
        assert parse_due_date("2026-06-15") == date(2026, 6, 15)

    def test_iso_with_time(self) -> None:
        assert parse_due_date("2026-06-15T13:00:00") == date(2026, 6, 15)

    def test_iso_with_z(self) -> None:
        assert parse_due_date("2026-06-15T13:00:00Z") == date(2026, 6, 15)

    def test_date_passthrough(self) -> None:
        assert parse_due_date(date(2026, 6, 15)) == date(2026, 6, 15)

    @pytest.mark.parametrize("raw", [None, "", "  ", "not a date"])
    def test_none_for_invalid(self, raw: object) -> None:
        assert parse_due_date(raw) is None


# ── repository against the ORM ────────────────────────────────────────


def _seed(db: SQLAlchemy, models: dict) -> None:
    user = models["User"](id=1, email="a@b.com")
    db.session.add(user)
    db.session.add_all(
        [
            models["ManualTask"](
                id=10, user_id=1, title="AP Chem practice packet",
                due_date="2026-06-13", course="AP Chemistry",
                estimated_time=45, done=False,
            ),
            models["ManualTask"](
                id=11, user_id=1, title="History essay outline",
                due_date="2026-06-20", course="US History",
                estimated_time=90, done=False,
            ),
            models["ManualTask"](
                id=12, user_id=1, title="Already done",
                due_date="2026-06-12", course="English",
                estimated_time=30, done=True,
            ),
            models["ManualTask"](
                id=13, user_id=1, title="No date task",
                due_date="", course="Personal",
                estimated_time=15, done=False,
            ),
            models["ManualTask"](
                id=14, user_id=2, title="Other user's task",
                due_date="2026-06-13", course="Chem",
                estimated_time=45, done=False,
            ),
        ]
    )
    db.session.commit()


class TestAssignmentRepository:
    def test_for_user_excludes_completed_and_other_users(
        self, app, db: SQLAlchemy, models: dict
    ) -> None:
        with app.app_context():
            _seed(db, models)
            repo = AssignmentRepository(models["ManualTask"], db.session)
            result = repo.for_user(user_id=1, today=date(2026, 6, 12))

        titles = [a.title for a in result]
        assert "Already done" not in titles
        assert "Other user's task" not in titles
        assert set(titles) == {
            "AP Chem practice packet",
            "History essay outline",
            "No date task",
        }

    def test_sorted_by_due_date_with_none_last(
        self, app, db: SQLAlchemy, models: dict
    ) -> None:
        with app.app_context():
            _seed(db, models)
            repo = AssignmentRepository(models["ManualTask"], db.session)
            result = repo.for_user(user_id=1, today=date(2026, 6, 12))

        assert [a.title for a in result] == [
            "AP Chem practice packet",
            "History essay outline",
            "No date task",
        ]
        assert result[-1].due_date is None

    def test_kind_classification_applied(
        self, app, db: SQLAlchemy, models: dict
    ) -> None:
        with app.app_context():
            _seed(db, models)
            repo = AssignmentRepository(models["ManualTask"], db.session)
            result = repo.for_user(user_id=1, today=date(2026, 6, 12))

        by_title = {a.title: a for a in result}
        assert by_title["AP Chem practice packet"].kind is AssignmentKind.HOMEWORK
        assert by_title["History essay outline"].kind is AssignmentKind.PROJECT

    def test_status_default_not_started(
        self, app, db: SQLAlchemy, models: dict
    ) -> None:
        with app.app_context():
            _seed(db, models)
            repo = AssignmentRepository(models["ManualTask"], db.session)
            result = repo.for_user(user_id=1, today=date(2026, 6, 12))

        assert all(a.status is AssignmentStatus.NOT_STARTED for a in result)

    def test_stable_ids(self, app, db: SQLAlchemy, models: dict) -> None:
        with app.app_context():
            _seed(db, models)
            repo = AssignmentRepository(models["ManualTask"], db.session)
            first = repo.for_user(user_id=1, today=date(2026, 6, 12))
            second = repo.for_user(user_id=1, today=date(2026, 6, 12))
        assert [a.id for a in first] == [a.id for a in second]
        assert all(a.id.startswith("manual-") for a in first)
