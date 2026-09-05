"""A submitted-but-ungraded Canvas assignment is not "overdue".

Canvas's assignments endpoint says nothing about whether the student has
turned work in unless the request asks for ``include[]=submission``. Without
that, every past-due Canvas assignment reads as overdue forever — even one
turned in on time that's just sitting in a teacher's queue waiting for a
grade. StudentVue already got this right (``get_assignments`` drops an
assignment the moment its DisplayScore says "Not Graded"); this brings
Canvas in line via ``_canvas_submission_done_or_pending_grade``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import App
from App import LinkedAccount, User, bcrypt, db, utcnow, _canvas_submission_done_or_pending_grade

PASSWORD = "overdue-grading-pw"


# ── The pure predicate ──────────────────────────────────────────────


def test_no_submission_data_is_not_treated_as_done():
    """Nothing to go on yet — behave like before this fix (still overdue-eligible)."""
    assert _canvas_submission_done_or_pending_grade({"id": 1, "name": "Essay"}) is False


def test_missing_or_malformed_submission_is_not_treated_as_done():
    assert _canvas_submission_done_or_pending_grade({"submission": None}) is False
    assert _canvas_submission_done_or_pending_grade({"submission": "not a dict"}) is False
    assert _canvas_submission_done_or_pending_grade("not a dict at all") is False


def test_unsubmitted_work_is_still_overdue_eligible():
    a = {"submission": {"workflow_state": "unsubmitted", "submitted_at": None}}
    assert _canvas_submission_done_or_pending_grade(a) is False


def test_submitted_but_ungraded_is_done_for_the_student():
    """The whole point: turned in, awaiting a grade, not something to still do."""
    a = {"submission": {"workflow_state": "submitted", "submitted_at": "2026-01-01T00:00:00Z"}}
    assert _canvas_submission_done_or_pending_grade(a) is True


def test_graded_is_done_even_without_a_submitted_at_timestamp():
    a = {"submission": {"workflow_state": "graded", "submitted_at": None}}
    assert _canvas_submission_done_or_pending_grade(a) is True


# ── End to end through collect_lms_assignments_for_user ─────────────


@pytest.fixture
def client():
    App.app.config["TESTING"] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            db.create_all()
            _wipe()
        yield c
        with App.app.app_context():
            _wipe()
    App.limiter.enabled = True


def _wipe():
    ids = [u.id for u in User.query.filter(User.email.like("cog+%")).all()]
    if ids:
        LinkedAccount.query.filter(LinkedAccount.user_id.in_(ids)).delete(synchronize_session=False)
    User.query.filter(User.email.like("cog+%")).delete(synchronize_session=False)
    db.session.commit()


def _canvas_student():
    with App.app.app_context():
        user = User(email="cog+a@example.com",
                    password_hash=bcrypt.generate_password_hash(PASSWORD).decode())
        db.session.add(user)
        db.session.commit()
        acct = LinkedAccount(user_id=user.id, login_type="canvas", is_active=True)
        acct.set_credentials({
            "canvas_token": "tok-123",
            "canvas_url": "https://school.instructure.com",
        })
        db.session.add(acct)
        db.session.commit()
        return user.id


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _due(days_ago):
    return (utcnow() - timedelta(days=days_ago)).strftime("%Y-%m-%dT00:00:00Z")


def test_a_graded_past_due_assignment_is_excluded(client, monkeypatch):
    """The regression: this used to always land in the overdue bucket."""

    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/courses"):
            return _Resp([{"id": 1, "name": "History"}])
        assert "include[]=submission" in url, "must ask Canvas for submission state"
        return _Resp([
            {
                "id": 10, "name": "Graded essay", "course_id": 1,
                "due_at": _due(5), "points_possible": 100,
                "submission": {"workflow_state": "graded", "submitted_at": _due(6)},
            },
            {
                "id": 11, "name": "Submitted, awaiting grade", "course_id": 1,
                "due_at": _due(5), "points_possible": 100,
                "submission": {"workflow_state": "submitted", "submitted_at": _due(5)},
            },
            {
                "id": 12, "name": "Actually blown off", "course_id": 1,
                "due_at": _due(5), "points_possible": 100,
                "submission": {"workflow_state": "unsubmitted", "submitted_at": None},
            },
        ])

    monkeypatch.setattr(App.requests, "get", fake_get)
    user_id = _canvas_student()

    with App.app.app_context():
        tasks = App.collect_lms_assignments_for_user(user_id, use_cache=False)

    titles = {t["title"] for t in tasks}
    assert "Graded essay" not in titles
    assert "Submitted, awaiting grade" not in titles
    assert "Actually blown off" in titles
