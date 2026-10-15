"""Planning uses the student's day, including background repairs."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from flask import Flask
from flask_login import login_user

import App
from intelliplan import scheduler_clock as clock


@pytest.fixture
def student():
    with App.app.app_context():
        App.db.create_all()
        user = App.User(email=f'clock-{uuid4().hex}@example.test', password_hash='x',
                        utc_offset_minutes=-420)
        App.db.session.add(user)
        App.db.session.flush()
        App.db.session.add(App.UserStreak(user_id=user.id, timezone='America/Los_Angeles'))
        App.db.session.commit()
        uid = user.id
        yield uid
        App.db.session.rollback()
        App.UserStreak.query.filter_by(user_id=uid).delete()
        App.User.query.filter_by(id=uid).delete()
        App.db.session.commit()


def test_stored_iana_clock_crosses_utc_day_and_tracks_dst(student):
    assert clock.now_for(student, utc=datetime(2026, 10, 14, 0, 30)) == datetime(2026, 10, 13, 17, 30)
    assert clock.now_for(student, utc=datetime(2026, 11, 3, 0, 30)) == datetime(2026, 11, 2, 16, 30)


def test_request_clock_is_captured_once(student, monkeypatch):
    ticks = Mock(side_effect=[datetime(2026, 10, 14, 6, 59), datetime(2026, 10, 14, 7, 1)])
    monkeypatch.setattr(clock, 'utcnow', ticks)
    with App.app.test_request_context():
        login_user(App.db.session.get(App.User, student))
        assert clock.now_for(student) == datetime(2026, 10, 13, 23, 59)
        assert clock.now_for(student) == datetime(2026, 10, 13, 23, 59)
    assert ticks.call_count == 1


@pytest.mark.parametrize(('header', 'expected'), [
    ('Asia/Kolkata', 'Asia/Kolkata'), ('Not/AZone', 'America/Los_Angeles'),
])
def test_owner_browser_zone_precedes_stored_zone(student, header, expected):
    with App.app.test_request_context(headers={'X-Timezone': header}):
        login_user(App.db.session.get(App.User, student))
        assert clock.timezone_for(student)[0] == expected


def test_cron_caller_cannot_override_another_owners_clock(student):
    with App.app.test_request_context(headers={'X-Timezone': 'Asia/Tokyo'}):
        assert clock.timezone_for(student)[0] == 'America/Los_Angeles'


def test_fixed_offset_remains_supported_for_older_accounts(student):
    App.UserStreak.query.filter_by(user_id=student).one().timezone = ''
    App.db.session.commit()
    assert clock.now_for(student, utc=datetime(2026, 10, 14, 0, 30)) == datetime(2026, 10, 13, 17, 30)


def test_real_schedule_build_keeps_usable_local_evening(student, monkeypatch):
    monkeypatch.setattr(clock, 'utcnow', lambda: datetime(2026, 10, 14, 0, 30))
    monkeypatch.setattr(App, '_planner_busy_by_date', lambda **kwargs: {})
    monkeypatch.setattr(App, '_planner_feedback_rows', lambda *args: [])
    monkeypatch.setattr(App, '_planner_session_rows', lambda *args: [])
    monkeypatch.setattr(App, '_planner_concept_mastery', lambda *args: {})
    monkeypatch.setattr(App, '_followthrough_extras', lambda *args: {})
    with App.app.test_request_context():
        login_user(App.db.session.get(App.User, student))
        data = App._build_planner_schedule(
            [{'id': 'local-evening', 'title': 'Algebra problems', 'course': 'Math',
              'due_date': '2026-10-13', 'estimated_time': 30}], [], student, None,
            {'Tue': {'start': '17:00', 'end': '20:00'}}, '', 'evening', 2,
            record_version=False,
        )
    assert data is not None
    assert data['planned_at_local'] == '2026-10-13T17:30:00'
    assert data['feasibility']['feasible']
    work = [b for d in data['schedule'] for b in d['blocks'] if not b.get('is_break')]
    assert work
    assert all(b['start_iso'].startswith('2026-10-13T') for b in work)


def test_cron_autopilot_passes_explicit_owners_to_clock():
    from intelliplan.api.adjust import AdjustDeps, create_adjust_blueprint
    clocks = {1: datetime(2026, 10, 13, 17), 2: datetime(2026, 10, 14, 9)}
    seen = []
    def service(uid, gid, payload):
        def autopilot(*args, now, **kwargs):
            seen.append((uid, now))
            return None
        return SimpleNamespace(autopilot=autopilot)
    deps = AdjustDeps(
        identity=lambda: (None, None), feature_enabled=lambda uid: True,
        now=Mock(side_effect=AssertionError('anonymous clock must not run')),
        owner_now=lambda uid: clocks[uid], build_service=service,
        load_plan=lambda uid, gid: ({'schedule': [{'blocks': []}]}, {}),
        active_owners=lambda: [(1, None), (2, None)],
    )
    app = Flask(__name__)
    bp = create_adjust_blueprint(deps)
    with app.test_request_context():
        result = bp.sweep_autopilot()
    assert seen == list(clocks.items())
    assert result['checked'] == 2
    assert result['failed'] == 0
