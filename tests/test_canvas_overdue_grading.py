"""A submitted-but-ungraded Canvas assignment is not "overdue".

Canvas's assignments endpoint says nothing about whether the student has
turned work in unless the request asks for ``include[]=submission``. Without
that, every past-due Canvas assignment reads as overdue forever — even one
turned in on time that's just sitting in a teacher's queue waiting for a
grade. Only individual submission evidence or an explicit student report
can distinguish that from work which still needs the student's attention.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import App
from intelliplan.services.assignment_status import requires_student_action
from App import CustomDescription, LinkedAccount, User, bcrypt, db, utcnow, _canvas_submission_done_or_pending_grade

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


@pytest.mark.parametrize("submission", [
    {"workflow_state": "unsubmitted", "submitted_at": "2026-01-01T00:00:00Z"},
    {"workflow_state": "graded", "redo_request": True},
    {"workflow_state": "unknown", "submitted_at": "not a timestamp"},
])
def test_reopened_work_or_invalid_proof_is_not_hidden(submission):
    assert _canvas_submission_done_or_pending_grade({"submission": submission}) is False


def test_classwide_submission_flag_is_not_individual_evidence():
    assert _canvas_submission_done_or_pending_grade({"has_submitted_submissions": True}) is False


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
        CustomDescription.query.filter(CustomDescription.user_id.in_(ids)).delete(synchronize_session=False)
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
    # The endpoint compares calendar dates in the server's local timezone.
    # Anchor this fixture to that same calendar day so it stays overdue
    # even when UTC has already crossed midnight but Pacific has not.
    return (date.today() - timedelta(days=days_ago)).strftime("%Y-%m-%dT00:00:00Z")


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


def _signin(client, uid):
    with client.session_transaction() as session:
        session['_user_id'] = str(uid)
        session['_fresh'] = True


def _canvas_feed(monkeypatch):
    rows = [
        {'id': 10, 'name': 'Essay awaiting grade', 'course_id': 1, 'due_at': _due(5),
         'points_possible': 30, 'submission': {'workflow_state': 'submitted', 'submitted_at': _due(6)}},
        {'id': 11, 'name': 'Unsubmitted practice', 'course_id': 1, 'due_at': _due(1),
         'points_possible': 10, 'submission': {'workflow_state': 'unsubmitted', 'submitted_at': None}},
        {'id': 12, 'name': 'Essay reopened', 'course_id': 1, 'due_at': _due(1),
         'points_possible': 20, 'submission': {'workflow_state': 'graded', 'redo_request': True}},
    ]
    def get(url, **kwargs):
        if url.endswith('/courses'):
            return _Resp([{'id': 1, 'name': 'History'}])
        assert 'include[]=submission' in url
        return _Resp(rows)
    monkeypatch.setattr(App.requests, 'get', get)
    return rows


def test_waiting_filter_retains_deadline_without_overdue_or_new_work(client, monkeypatch):
    rows = _canvas_feed(monkeypatch)
    uid = _canvas_student()
    _signin(client, uid)
    monkeypatch.setattr(App, 'get_active_account', lambda: {
        'login_type': 'canvas', 'canvas_url': 'https://school.instructure.com', 'canvas_token': 'fake'})
    visible = client.get('/live?include_waiting=1').get_json()
    by_title = {a['title']: a for a in visible}
    assert by_title['Essay awaiting grade']['awaiting_grade'] is True
    assert by_title['Essay awaiting grade']['due_date'] == rows[0]['due_at'][:10]
    assert by_title['Essay reopened']['requires_action'] is True
    assert 'Essay awaiting grade' not in {a['title'] for a in client.get('/live').get_json()}
    with App.app.app_context():
        App.invalidate_lms_cache_for_user(uid)
        first = App.collect_lms_assignments_for_user(uid, include_waiting=True)
        cached = App.collect_lms_assignments_for_user(uid)
        assert len(first) == 3
        assert {a['title'] for a in cached} == {'Unsubmitted practice', 'Essay reopened'}
    unified = client.get('/tasks/unified?include_waiting=1').get_json()
    assert [a['title'] for a in unified['waiting']] == ['Essay awaiting grade']
    assert {a['title'] for a in unified['overdue']} == {'Unsubmitted practice', 'Essay reopened'}


def test_assignments_do_not_vanish_after_fourteen_days(client, monkeypatch):
    rows = _canvas_feed(monkeypatch)
    for row in rows:
        row['due_at'] = _due(30)
    uid = _canvas_student()
    with App.app.app_context():
        visible = App.collect_lms_assignments_for_user(uid, use_cache=False, include_waiting=True)
        by_title = {row['title']: row for row in visible}
        assert by_title['Essay awaiting grade']['awaiting_grade'] is True
        assert by_title['Unsubmitted practice']['requires_action'] is True
        assert by_title['Essay reopened']['requires_action'] is True
        assert all(row['due_date'] == _due(30)[:10] for row in visible)


def test_extension_keeps_old_actionable_work_and_respects_student_report(client, monkeypatch):
    rows = _canvas_feed(monkeypatch)
    for row in rows:
        row['due_at'] = _due(30)
    uid = _canvas_student()
    _signin(client, uid)
    monkeypatch.setattr(App, 'get_extension_user', lambda token: db.session.get(User, uid))
    response = client.get('/extension/tasks', headers={'X-Extension-Token': 'test-only'})
    assert response.status_code == 200
    assert {a['title'] for a in response.get_json()['overdue']} == {'Unsubmitted practice', 'Essay reopened'}
    with App.app.app_context():
        task = next(a for a in App.collect_lms_assignments_for_user(uid, use_cache=False) if a['title'] == 'Unsubmitted practice')
    assert client.post('/api/assignment/submission', json={'action': 'submitted', 'assignment': task}).status_code == 200
    response = client.get('/extension/tasks', headers={'X-Extension-Token': 'test-only'})
    assert {a['title'] for a in response.get_json()['overdue']} == {'Essay reopened'}


def test_student_report_persists_with_undo_and_does_not_claim_lms_verification(client, monkeypatch):
    _canvas_feed(monkeypatch)
    uid = _canvas_student()
    _signin(client, uid)
    with App.app.app_context():
        actionable = App.collect_lms_assignments_for_user(uid, use_cache=False)
    assignment = next(a for a in actionable if a['title'] == 'Unsubmitted practice')
    result = client.post('/api/assignment/submission', json={'action': 'submitted', 'assignment': assignment})
    assert result.status_code == 200
    with App.app.app_context():
        visible = App.collect_lms_assignments_for_user(uid, use_cache=False, include_waiting=True)
        reported = next(a for a in visible if a['title'] == assignment['title'])
        assert reported['submission_status'] == 'student_reported'
        assert reported['submission_verified'] is False
        assert not requires_student_action(reported)
        assert assignment['title'] not in {a['title'] for a in App.collect_lms_assignments_for_user(uid)}
    assert client.post('/api/assignment/submission', json={'action': 'reopen', 'assignment': reported}).status_code == 200
    with App.app.app_context():
        assert assignment['title'] in {a['title'] for a in App.collect_lms_assignments_for_user(uid, use_cache=False)}


@pytest.mark.parametrize('role', ['parent', 'teacher'])
def test_adults_cannot_mutate_student_submission_metadata(client, role):
    uid = _canvas_student()
    with App.app.app_context():
        db.session.get(User, uid).role = role
        db.session.commit()
    _signin(client, uid)
    response = client.post('/api/assignment/submission', json={
        'action': 'submitted', 'assignment': {'title': 'Student work'}})
    assert response.status_code == 403
    with App.app.app_context():
        assert CustomDescription.query.filter_by(user_id=uid).count() == 0


def test_undo_clears_historical_duplicate_reports_without_deleting_rows(client, monkeypatch):
    _canvas_feed(monkeypatch)
    uid = _canvas_student()
    _signin(client, uid)
    with App.app.app_context():
        task = next(a for a in App.collect_lms_assignments_for_user(uid, use_cache=False) if a['title'] == 'Unsubmitted practice')
    client.post('/api/assignment/submission', json={'action': 'submitted', 'assignment': task})
    with App.app.app_context():
        note = CustomDescription.query.filter_by(user_id=uid).one()
        db.session.add(CustomDescription(user_id=uid, assignment_title=note.assignment_title, description=note.description))
        db.session.commit()
    assert client.post('/api/assignment/submission', json={'action': 'reopen', 'assignment': task}).status_code == 200
    with App.app.app_context():
        import json
        notes = CustomDescription.query.filter_by(user_id=uid).all()
        assert len(notes) == 2 and all(json.loads(note.description)['submitted'] is False for note in notes)
        assert task['title'] in {row['title'] for row in App.collect_lms_assignments_for_user(uid, use_cache=False)}


def test_student_report_is_owner_scoped_and_teacher_reopening_wins(client, monkeypatch):
    rows = _canvas_feed(monkeypatch)
    uid = _canvas_student()
    _signin(client, uid)
    with App.app.app_context():
        task = next(a for a in App.collect_lms_assignments_for_user(uid, use_cache=False) if a['title'] == 'Unsubmitted practice')
    client.post('/api/assignment/submission', json={'action': 'submitted', 'assignment': task})
    with App.app.app_context():
        # Same task identity under a different owner cannot inherit this student's report.
        assert App._assignment_display_rows([task], user_id=uid + 100)[0]['requires_action'] is True
        rows[1]['submission']['redo_request'] = True
        changed = App.collect_lms_assignments_for_user(uid, use_cache=False, include_waiting=True)
        assert next(a for a in changed if a['title'] == task['title'])['requires_action'] is True
    reopened = next(a for a in changed if a['title'] == task['title'])
    assert client.post('/api/assignment/submission', json={'action': 'submitted', 'assignment': reopened}).status_code == 409


def test_lms_to_repository_to_real_clock_plan_excludes_waiting_and_schedules_redo(client, monkeypatch):
    from datetime import date, datetime
    from flask_login import login_user
    from intelliplan.repositories.assignments import AssignmentRepository
    from intelliplan import scheduler_clock
    _canvas_feed(monkeypatch)
    uid = _canvas_student()
    # Real clock placement remains enabled; only external/calendar/history reads are isolated.
    monkeypatch.setattr(scheduler_clock, 'utcnow', lambda: datetime.combine(date.today(), datetime.min.time()).replace(hour=9))
    monkeypatch.setattr(App, '_planner_busy_by_date', lambda **kwargs: {})
    monkeypatch.setattr(App, '_planner_feedback_rows', lambda *args: [])
    monkeypatch.setattr(App, '_planner_session_rows', lambda *args: [])
    monkeypatch.setattr(App, '_planner_concept_mastery', lambda *args: {})
    monkeypatch.setattr(App, '_followthrough_extras', lambda *args: {})
    with App.app.test_request_context():
        login_user(db.session.get(User, uid))
        repo = AssignmentRepository(App.ManualTask, db.session,
                                    lms_fetcher=lambda user_id: App.collect_lms_assignments_for_user(user_id, use_cache=False, include_waiting=True))
        active = repo.for_user(uid, date.today())
        assert {a.title for a in active} == {'Unsubmitted practice', 'Essay reopened'}
        assignments = App.collect_lms_assignments_for_user(uid, use_cache=False, include_waiting=True)
        availability = {day: {'start': '09:00', 'end': '18:00'} for day in App.scheduler_engine.DAY_ABBR}
        plan = App._build_planner_schedule(assignments, [], uid, None, availability, '', 'morning', 4, record_version=False)
    assert plan is not None and plan['feasibility']['feasible']
    blocks = [b for d in plan['schedule'] for b in d['blocks'] if not b.get('is_break')]
    assert blocks, plan
    assert {'Unsubmitted practice', 'Essay reopened'} <= {b['assignment'] for b in blocks}
    assert 'Essay awaiting grade' not in {b['assignment'] for b in blocks}
    assert all(b['start_iso'] < b['end_iso'] for b in blocks)
