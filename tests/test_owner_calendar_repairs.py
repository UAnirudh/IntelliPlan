"""Background repairs use each owner's calendar, timezone and fresh policy."""
import json
from datetime import date, datetime
from uuid import uuid4
from unittest.mock import Mock

import pytest
from flask import session
from flask_login import login_user

import App
from intelliplan import scheduler_clock

REAL_CALENDAR_POLICY = App._calendar_policy_allowed


@pytest.fixture
def owners(monkeypatch):
    with App.app.app_context():
        App.db.create_all()
        users = [App.User(email=f'calendar-owner-{uuid4().hex}@example.test',
                          password_hash='x', birth_year=1990) for _ in range(2)]
        App.db.session.add_all(users)
        App.db.session.flush()
        for user, zone in zip(users, ['America/Los_Angeles', 'Asia/Tokyo']):
            App.db.session.add(App.UserStreak(user_id=user.id, timezone=zone))
            App.db.session.add(App.GoogleIntegration(user_id=user.id, is_active=True,
                token_data=json.dumps({'access_token': f'google-{user.id}',
                                       'scopes': ['https://www.googleapis.com/auth/calendar.events']})))
            App.db.session.add(App.OutlookIntegration(user_id=user.id,
                token_data=json.dumps({'access_token': f'outlook-{user.id}', 'expires_at': 9999999999})))
        App.db.session.commit()
        ids = [user.id for user in users]
        monkeypatch.setattr(App, 'OUTLOOK_AVAILABLE', True)
        monkeypatch.setattr(App, '_calendar_policy_allowed', lambda user_id=None: True)
        monkeypatch.setattr(App, '_class_busy_by_date', lambda *args: {})
        monkeypatch.setattr(scheduler_clock, 'utcnow', lambda: datetime(2026, 10, 14, 0, 30))
        yield ids
        App.db.session.rollback()
        for model in (App.GoogleIntegration, App.OutlookIntegration, App.UserStreak):
            model.query.filter(model.user_id.in_(ids)).delete(synchronize_session=False)
        App.User.query.filter(App.User.id.in_(ids)).delete(synchronize_session=False)
        App.db.session.commit()


def test_cron_imports_each_owners_tokens_day_and_zone_without_a_login(owners, monkeypatch):
    import google_calendar_helper
    google, outlook = [], []
    def busy(calls, token, start, **kwargs):
        calls.append((token['access_token'], start, kwargs))
        return {start.isoformat(): [(600, 630)]}
    monkeypatch.setattr(google_calendar_helper, 'busy_minutes_by_date',
                        lambda *args, **kwargs: busy(google, *args, **kwargs))
    monkeypatch.setattr(App.outlook_calendar_helper, 'busy_minutes_by_date',
                        lambda *args, **kwargs: busy(outlook, *args, **kwargs))
    with App.app.test_request_context('/api/cron/autopilot', headers={'X-Timezone': 'Europe/London'}):
        first = App._planner_busy_by_date(user_id=owners[0])
        second = App._planner_busy_by_date(user_id=owners[1])
        assert first == {'2026-10-13': [(600, 630), (600, 630)]}
        assert second == {'2026-10-14': [(600, 630), (600, 630)]}
        assert App._planner_busy_by_date(user_id=owners[0]) == first
    assert [item[0] for item in google] == [f'google-{uid}' for uid in owners]
    assert [item[0] for item in outlook] == [f'outlook-{uid}' for uid in owners]
    assert google[0][1] == date(2026, 10, 13)
    assert google[0][2]['tz_name'] == 'America/Los_Angeles'
    assert google[1][2]['tz_name'] == 'Asia/Tokyo'


def test_explicit_owner_never_uses_callers_session_token(owners):
    App.GoogleIntegration.query.filter_by(user_id=owners[1]).delete()
    App.db.session.commit()
    with App.app.test_request_context():
        login_user(App.db.session.get(App.User, owners[0]))
        session['google_token'] = {'access_token': 'caller-session-token'}
        assert App.get_google_token(user_id=owners[1]) is None
        assert App.get_google_token(user_id=owners[0])['access_token'] == f'google-{owners[0]}'


def test_owner_denial_precedes_token_access_and_ignores_cache(owners, monkeypatch):
    allowed = {uid: True for uid in owners}
    monkeypatch.setattr(App, '_calendar_policy_allowed', lambda user_id=None: allowed.get(user_id, False))
    google, outlook = Mock(return_value=None), Mock(return_value=None)
    monkeypatch.setattr(App, 'get_google_token', google)
    monkeypatch.setattr(App, 'get_outlook_token', outlook)
    with App.app.test_request_context():
        assert App._planner_busy_by_date(user_id=owners[0]) == {}
        google.reset_mock()
        outlook.reset_mock()
        allowed[owners[0]] = False
        assert App._planner_busy_by_date(user_id=owners[0]) == {}
    google.assert_not_called()
    outlook.assert_not_called()


def test_revocation_during_outlook_refresh_does_not_persist_new_token(owners, monkeypatch):
    allowed = [True]
    monkeypatch.setattr(App, '_calendar_policy_allowed', lambda user_id=None: allowed[0])
    row = App.OutlookIntegration.query.filter_by(user_id=owners[0]).one()
    original = json.dumps({'access_token': 'old', 'refresh_token': 'refresh', 'expires_at': 0})
    row.token_data = original
    App.db.session.commit()
    def refresh(token):
        allowed[0] = False
        return {'access_token': 'new', 'expires_in': 3600}
    monkeypatch.setattr(App.outlook_calendar_helper, 'refresh_token', refresh)
    with App.app.test_request_context():
        assert App.get_outlook_token(user_id=owners[0]) is None
    App.db.session.expire_all()
    assert App.OutlookIntegration.query.filter_by(user_id=owners[0]).one().token_data == original


def test_central_policy_resolves_explicit_owner_instead_of_signed_in_caller(owners, monkeypatch):
    from intelliplan.learning import policy
    checked = []
    def decision(user, feature):
        checked.append((user.id, feature))
        return user.id == owners[0], 'fixture-policy'
    monkeypatch.setattr(policy, 'decision', decision)
    monkeypatch.setattr(App, '_calendar_policy_allowed', REAL_CALENDAR_POLICY)
    with App.app.test_request_context():
        login_user(App.db.session.get(App.User, owners[0]))
        assert App._calendar_policy_allowed(owners[1]) is False
        assert App._calendar_policy_allowed(owners[0]) is True
    assert checked == [(owners[1], 'calendar'), (owners[0], 'calendar')]


def test_followthrough_service_requests_plan_owners_busy_data(owners, monkeypatch):
    import followthrough_glue
    calls = []
    busy = {'2026-10-14': [(1080, 1140)]}
    monkeypatch.setattr(App, '_planner_busy_by_date',
                        lambda **kwargs: calls.append(kwargs) or busy)
    with App.app.test_request_context():
        login_user(App.db.session.get(App.User, owners[0]))
        service = followthrough_glue._build_service(owners[1], None, {})
        assert service._ctx.busy_by_date == busy
    assert calls == [{'user_id': owners[1]}]


@pytest.mark.parametrize('failed_provider', ['google', 'outlook'])
@pytest.mark.parametrize('failure_stage', ['token', 'fetch'])
def test_partial_outage_retains_other_calendar_and_classes(owners, monkeypatch, failed_provider, failure_stage):
    import google_calendar_helper
    day = '2026-10-13'
    secret = 'credential-and-event-description-must-not-be-logged'
    error = RuntimeError(secret)
    error.resp = Mock(status=401)
    failed = Mock(side_effect=error)
    good = Mock(return_value={day: [(600, 630)]})
    monkeypatch.setattr(google_calendar_helper, 'busy_minutes_by_date', failed if failed_provider == 'google' else good)
    monkeypatch.setattr(App.outlook_calendar_helper, 'busy_minutes_by_date', failed if failed_provider == 'outlook' else good)
    if failure_stage == 'token':
        monkeypatch.setattr(App, 'get_' + failed_provider + '_token', failed)
    log = Mock()
    monkeypatch.setattr(App.app.logger, 'warning', log)
    monkeypatch.setattr(App, '_class_busy_by_date', lambda *args: {day: [(700, 720)]})
    with App.app.test_request_context('/api/cron/autopilot'):
        assert App._planner_busy_by_date(user_id=owners[0]) == {day: [(600, 630), (700, 720)]}
    good.assert_called_once()
    assert log.call_args.args[1:] == (failed_provider, 'RuntimeError', 401)
    assert secret not in str(log.call_args)


def test_calendar_revocation_during_failure_discards_previous_provider_data(owners, monkeypatch):
    import google_calendar_helper
    allowed = [True]
    monkeypatch.setattr(App, '_calendar_policy_allowed', lambda user_id=None: allowed[0])
    monkeypatch.setattr(google_calendar_helper, 'busy_minutes_by_date', lambda *a, **kw: {'2026-10-13': [(600, 630)]})
    def revoke(*args, **kwargs):
        allowed[0] = False
        raise RuntimeError('must remain private')
    monkeypatch.setattr(App.outlook_calendar_helper, 'busy_minutes_by_date', revoke)
    with App.app.test_request_context():
        assert App._calendar_busy_by_date(user_id=owners[0]) == {}
