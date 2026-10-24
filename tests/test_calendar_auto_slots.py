"""Outlook-only, combined and unavailable calendar Auto behavior."""
from datetime import date, datetime
import json
from unittest.mock import Mock
from uuid import uuid4

import pytest
from flask_login import login_user

import App
from intelliplan import scheduler_clock
from intelliplan.services.calendar_slots import free_time_summary


@pytest.fixture
def calendar_owner(monkeypatch):
    with App.app.app_context():
        App.db.create_all()
        user = App.User(email=f'auto-calendar-{uuid4().hex}@example.test', password_hash='x', birth_year=1990)
        App.db.session.add(user)
        App.db.session.flush()
        App.db.session.add(App.OutlookIntegration(user_id=user.id,
            token_data=json.dumps({'access_token': 'fixture', 'expires_at': 9999999999})))
        App.db.session.commit()
        uid = user.id
        monkeypatch.setattr(App, '_calendar_policy_allowed', lambda user_id=None: True)
        monkeypatch.setattr(scheduler_clock, 'timezone_for', lambda user_id=None: ('Asia/Tokyo', 540))
        monkeypatch.setattr(scheduler_clock, 'now_for', lambda user_id=None: datetime(2026, 11, 2, 6))
        monkeypatch.setattr(App, '_class_busy_by_date', lambda *args: {})
        with App.app.test_request_context('/calendar/free-slot?date=2026-11-02&days=3'):
            login_user(user)
            yield uid
        App.db.session.rollback()
        App.GoogleIntegration.query.filter_by(user_id=uid).delete()
        App.OutlookIntegration.query.filter_by(user_id=uid).delete()
        App.User.query.filter_by(id=uid).delete()
        App.db.session.commit()


def test_outlook_only_auto_checks_three_days_in_one_complete_read(calendar_owner, monkeypatch):
    busy = Mock(return_value={'2026-11-02': [(18 * 60, 20 * 60)], '2026-11-03': [(0, 1440)]})
    monkeypatch.setattr(App, '_calendar_busy_by_date', busy)
    result = App.calendar_free_slot().get_json()
    assert result['connected'] is True
    assert result['slot'] == '8:00 PM'
    assert len(result['suggestions']) == 3
    assert result['suggestions'][1]['available'] is False
    assert result['suggestions'][1]['slot'] == ''
    busy.assert_called_once_with(3, start=date(2026, 11, 2), require_complete=True)


def test_auto_outage_never_reports_unchecked_time_free(calendar_owner, monkeypatch):
    from intelliplan.services.calendar_availability import CalendarUnavailable
    monkeypatch.setattr(App, '_calendar_busy_by_date', Mock(side_effect=CalendarUnavailable(('outlook',))))
    response, status = App.calendar_free_slot()
    assert status == 503
    assert response.get_json()['error_code'] == 'calendar_unavailable'
    assert response.get_json().get('available') is False


def test_auto_respects_class_time(calendar_owner, monkeypatch):
    monkeypatch.setattr(App, '_calendar_busy_by_date', lambda *args, **kwargs: {})
    monkeypatch.setattr(App, '_class_busy_by_date', lambda *args: {'2026-11-02': [(18 * 60, 22 * 60)]})
    assert App.calendar_free_slot().get_json()['slot'] == '10:00 PM'


def test_auto_policy_revocation_during_lookup_is_not_verified_free(calendar_owner, monkeypatch):
    allowed = [True]
    monkeypatch.setattr(App, '_calendar_policy_allowed', lambda user_id=None: allowed[0])
    def revoked_lookup(*args, **kwargs):
        allowed[0] = False
        return {}
    monkeypatch.setattr(App, '_calendar_busy_by_date', revoked_lookup)
    response = App.app.make_response(App.calendar_free_slot())
    assert response.status_code == 403
    assert response.get_json()['available'] is False
    assert response.get_json().get('calendar_verified') is not True


def test_calendar_listing_outage_preserves_the_saved_google_connection(calendar_owner, monkeypatch):
    App.db.session.add(App.GoogleIntegration(user_id=calendar_owner, token_data='{}', is_active=True))
    App.db.session.commit()
    monkeypatch.setattr(App, 'GCAL_AVAILABLE', True)
    monkeypatch.setattr(App, '_outlook_upcoming_events', lambda: None)
    monkeypatch.setattr(App, 'get_google_token', lambda: {'token': 'fixture'})
    monkeypatch.setattr(App, 'get_upcoming_events', Mock(side_effect=RuntimeError('upstream unavailable')))
    response = App.app.make_response(App.calendar_events())
    assert response.status_code == 503
    assert response.get_json()['connected'] is True
    assert response.get_json()['error_code'] == 'calendar_unavailable'
    assert App.GoogleIntegration.query.filter_by(user_id=calendar_owner).count() == 1


def test_overlapping_provider_events_are_not_double_counted():
    assert free_time_summary([(18 * 60, 20 * 60), (19 * 60, 21 * 60)]) == {
        'slot': '9:00 PM', 'free_hours': 13, 'available': True}


def test_full_day_has_no_fabricated_seven_pm_slot():
    assert free_time_summary([(0, 1440)]) == {'slot': '', 'free_hours': 0, 'available': False}


def test_today_suggestions_never_offer_past_time():
    assert free_time_summary([], not_before=20 * 60 + 15)['slot'] == '8:30 PM'


@pytest.mark.parametrize('intervals', [[(0, float('nan'))], [(400, 300)], [('9', 600)], [None]])
def test_malformed_busy_data_is_not_free(intervals):
    with pytest.raises(ValueError):
        free_time_summary(intervals)
