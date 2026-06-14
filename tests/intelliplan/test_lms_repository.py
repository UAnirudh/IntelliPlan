"""Tests for AssignmentRepository's LMS data integration.

Verifies that the optional ``lms_fetcher`` callable produces canonical
Assignment objects merged with the ManualTask stream, and that
duplicates collapse correctly.
"""

from __future__ import annotations

from datetime import date

from flask_sqlalchemy import SQLAlchemy

from intelliplan.repositories.assignments import AssignmentRepository


def _make_user_and_task(db: SQLAlchemy, models: dict, title: str, due_date: str, course: str = "Math") -> None:
    User = models["User"]
    ManualTask = models["ManualTask"]
    if not User.query.first():
        db.session.add(User(id=1, email="t@example.com"))
        db.session.commit()
    db.session.add(ManualTask(user_id=1, title=title, due_date=due_date, course=course))
    db.session.commit()


# ── Empty-state safety ──────────────────────────────────────────────


def test_repo_with_no_lms_fetcher_works(app, db, models):
    """Backward compat: omitting the fetcher must still work."""
    with app.app_context():
        _make_user_and_task(db, models, "Read chapter 4", "2026-06-20", "Bio")
        repo = AssignmentRepository(models["ManualTask"], db.session)
        result = repo.for_user(1, date(2026, 6, 13))
    assert len(result) == 1
    assert result[0].source == "manual"


def test_repo_with_empty_lms_fetcher(app, db, models):
    """A fetcher that returns an empty list must not break anything."""
    with app.app_context():
        _make_user_and_task(db, models, "Read chapter 4", "2026-06-20")
        repo = AssignmentRepository(models["ManualTask"], db.session, lms_fetcher=lambda uid: [])
        result = repo.for_user(1, date(2026, 6, 13))
    assert len(result) == 1


def test_repo_with_failing_lms_fetcher_isolates_error(app, db, models):
    """If the fetcher raises, manual tasks should still come through."""
    def boom(_uid):
        raise RuntimeError("upstream is on fire")
    with app.app_context():
        _make_user_and_task(db, models, "Read chapter 4", "2026-06-20")
        repo = AssignmentRepository(models["ManualTask"], db.session, lms_fetcher=boom)
        result = repo.for_user(1, date(2026, 6, 13))
    assert len(result) == 1
    assert result[0].title == "Read chapter 4"


# ── LMS data ingestion ──────────────────────────────────────────────


def test_repo_includes_lms_tasks(app, db, models):
    canvas_task = {
        "id": "canvas-99",
        "title": "Lab report 5",
        "course": "Chemistry",
        "due_date": "2026-06-25",
        "priority": "High",
        "estimated_time": 90,
        "source": "canvas",
        "points_possible": 100,
    }
    with app.app_context():
        _make_user_and_task(db, models, "Read chapter 4", "2026-06-20", course="Bio")
        repo = AssignmentRepository(models["ManualTask"], db.session, lms_fetcher=lambda uid: [canvas_task])
        result = repo.for_user(1, date(2026, 6, 13))
    assert len(result) == 2
    sources = {a.source for a in result}
    assert "manual" in sources
    assert "canvas" in sources


def test_repo_dedupe_prefers_manual(app, db, models):
    """When a user adds a task that also exists in LMS, manual entry wins."""
    same_title = "AP Chem Exam"
    same_course = "Chemistry"
    same_due = "2026-06-25"
    lms_dup = {
        "id": "canvas-101",
        "title": same_title,
        "course": same_course,
        "due_date": same_due,
        "source": "canvas",
        "estimated_time": 120,
    }
    with app.app_context():
        _make_user_and_task(db, models, same_title, same_due, course=same_course)
        repo = AssignmentRepository(models["ManualTask"], db.session, lms_fetcher=lambda uid: [lms_dup])
        result = repo.for_user(1, date(2026, 6, 13))
    assert len(result) == 1
    assert result[0].source == "manual"


def test_repo_dedupe_case_insensitive(app, db, models):
    """Title/course matching must be case-insensitive."""
    with app.app_context():
        _make_user_and_task(db, models, "Quiz on Cell Biology", "2026-06-30", course="Biology")
        repo = AssignmentRepository(
            models["ManualTask"], db.session,
            lms_fetcher=lambda uid: [{
                "title": "quiz on cell biology",
                "course": "BIOLOGY",
                "due_date": "2026-06-30",
                "source": "schoology",
            }],
        )
        result = repo.for_user(1, date(2026, 6, 13))
    assert len(result) == 1


def test_repo_handles_lms_missing_fields(app, db, models):
    """Defensive: LMS dicts with missing/blank fields must not crash."""
    with app.app_context():
        _make_user_and_task(db, models, "Manual", "2026-06-20")
        repo = AssignmentRepository(
            models["ManualTask"], db.session,
            lms_fetcher=lambda uid: [
                {"source": "canvas"},  # everything missing
                {"title": "OK", "source": "studentvue"},  # missing course + due
                {"title": "Has due", "due_date": "not-a-date", "source": "moodle"},
                None,  # filtered out
            ],
        )
        result = repo.for_user(1, date(2026, 6, 13))
    # The None must be skipped; the rest must produce valid Assignment objects
    assert len(result) >= 2  # manual + at least one LMS entry
    for a in result:
        assert isinstance(a.title, str)
        assert len(a.title) > 0


def test_repo_sort_order_by_due_date(app, db, models):
    with app.app_context():
        _make_user_and_task(db, models, "Task A", "2026-06-25", course="A")
        _make_user_and_task(db, models, "Task B", "2026-06-18", course="B")
        repo = AssignmentRepository(
            models["ManualTask"], db.session,
            lms_fetcher=lambda uid: [
                {"title": "LMS task", "course": "C", "due_date": "2026-06-20", "source": "canvas"},
            ],
        )
        result = repo.for_user(1, date(2026, 6, 13))
    titles = [a.title for a in result]
    # Earliest due first
    assert titles == ["Task B", "LMS task", "Task A"]
