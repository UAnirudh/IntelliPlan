"""Reported provider errors and truncated views are unknown, not empty calendars."""
from datetime import date
from unittest.mock import Mock

import pytest

import google_calendar_helper as google
import outlook_calendar_helper as outlook


def test_calendar_connection_requests_the_freebusy_scope_required_by_the_planner():
    assert 'https://www.googleapis.com/auth/calendar.events.freebusy' in google.CALENDAR_SCOPES


@pytest.mark.parametrize('response', [
    {}, {'calendars': {}}, {'calendars': {'primary': {}}},
    {'calendars': {'primary': {'busy': [], 'errors': [{'reason': 'internalError'}]}}},
    {'calendars': {'primary': {'busy': None}}},
    {'calendars': {'primary': {'busy': [{'start': 'invalid', 'end': 'invalid'}]}}},
    {'calendars': {'primary': {'busy': [{'start': '2026-10-16T12:00:00Z', 'end': '2026-10-16T11:00:00Z'}]}}},
])
def test_google_strict_read_rejects_incomplete_or_invalid_busy(response, monkeypatch):
    service = Mock()
    service.freebusy.return_value.query.return_value.execute.return_value = response
    monkeypatch.setattr(google, 'get_calendar_service', lambda token: (service, None))
    with pytest.raises(ValueError, match='availability is incomplete'):
        google.busy_minutes_by_date({}, date(2026, 10, 16), require_complete=True)


def test_google_verified_empty_calendar_is_valid(monkeypatch):
    service = Mock()
    service.freebusy.return_value.query.return_value.execute.return_value = {'calendars': {'primary': {'busy': []}}}
    monkeypatch.setattr(google, 'get_calendar_service', lambda token: (service, None))
    assert google.busy_minutes_by_date({}, date(2026, 10, 16), require_complete=True) == {}


@pytest.mark.parametrize('response', [{}, {'value': None}, {'value': [None]},
                                       {'value': [], 'error': {'message': 'private response'}},
                                       {'value': [], '@odata.nextLink': 'https://other.example/page'}])
def test_outlook_strict_view_rejects_incomplete_data(response, monkeypatch):
    monkeypatch.setattr(outlook, 'graph_get', lambda *a, **kw: response)
    with pytest.raises(ValueError, match='availability is incomplete'):
        outlook.busy_minutes_by_date({}, date(2026, 10, 16), require_complete=True)


def test_outlook_paging_exhaustion_is_not_a_complete_calendar(monkeypatch):
    fetch = Mock(return_value={'value': [], '@odata.nextLink': outlook.GRAPH + '/me/calendarView?$skip=1'})
    monkeypatch.setattr(outlook, 'graph_get', fetch)
    with pytest.raises(ValueError, match='safe import limit'):
        outlook.busy_minutes_by_date({}, date(2026, 10, 16), require_complete=True)
    assert fetch.call_count == 5


def test_outlook_verified_empty_calendar_is_valid(monkeypatch):
    monkeypatch.setattr(outlook, 'graph_get', lambda *a, **kw: {'value': []})
    assert outlook.busy_minutes_by_date({}, date(2026, 10, 16), require_complete=True) == {}
