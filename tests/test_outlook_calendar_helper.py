from datetime import date, datetime
from unittest.mock import Mock

import pytest
import requests

import outlook_calendar_helper as outlook


class _Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload
    def raise_for_status(self):
        return None
    def json(self):
        return self.payload


def test_auth_url_requests_calendar_write_and_offline_access(monkeypatch):
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "client")
    monkeypatch.setenv("MICROSOFT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("MICROSOFT_REDIRECT_URI", "https://example.test/callback")
    url = outlook.get_auth_url("state-value")
    assert "Calendars.ReadWrite" in url
    assert "offline_access" in url
    assert "state=state-value" in url


def test_busy_calendar_view_is_translated_to_local_minutes(monkeypatch):
    monkeypatch.setattr(outlook.requests, "get", lambda *a, **k: _Response({"value": [{
        "start": {"dateTime": "2026-03-02T18:00:00"},
        "end": {"dateTime": "2026-03-02T19:30:00"}, "isAllDay": False,
    }]}))
    busy = outlook.busy_minutes_by_date({"access_token": "x"}, date(2026, 3, 2), days=2)
    assert busy[date(2026, 3, 2)] == [(18 * 60, 19 * 60 + 30)]


def test_iana_zone_tracks_dst_and_preserves_all_day_dates(monkeypatch):
    requested = []
    def calendar_view(token, start, end):
        requested.append((start, end))
        return [
            {'start': {'dateTime': '2026-11-02T20:00:00'},
             'end': {'dateTime': '2026-11-02T21:00:00'}},
            {'isAllDay': True, 'start': {'dateTime': '2026-11-01T07:00:00'},
             'end': {'dateTime': '2026-11-02T08:00:00'}},
        ]
    monkeypatch.setattr(outlook, '_calendar_view', calendar_view)
    busy = outlook.busy_minutes_by_date({}, date(2026, 10, 31), days=3,
                                        utc_offset_minutes=-420, tz_name='America/Los_Angeles')
    assert requested == [(datetime(2026, 10, 31, 7), datetime(2026, 11, 3, 8))]
    assert busy[date(2026, 11, 2)] == [(12 * 60, 13 * 60)]
    assert busy[date(2026, 11, 1)] == [(0, 1440)]


def test_repeated_hour_and_cross_zone_all_day_boundaries(monkeypatch):
    monkeypatch.setattr(outlook, '_calendar_view', lambda *args: [
        {'start': {'dateTime': '2026-11-01T08:30:00'},
         'end': {'dateTime': '2026-11-01T09:15:00'}},
        {'isAllDay': True, 'start': {'dateTime': '2026-11-02T05:00:00'},
         'end': {'dateTime': '2026-11-03T05:00:00'}},
    ])
    busy = outlook.busy_minutes_by_date({}, date(2026, 11, 1), days=3,
                                        tz_name='America/Los_Angeles')
    assert busy[date(2026, 11, 1)] == [(60, 120), (21 * 60, 1440)]
    assert busy[date(2026, 11, 2)] == [(0, 21 * 60)]


def test_export_creates_only_study_blocks(monkeypatch):
    created = []
    # The export reads the calendar first to skip duplicates; no events here.
    monkeypatch.setattr(outlook, "graph_get", lambda *a, **k: {"value": []})
    monkeypatch.setattr(outlook, "graph_post", lambda token, path, body: created.append(body) or {"id": "event"})
    ids = outlook.add_schedule_to_calendar({"access_token": "x"}, {"schedule": [{"date": "2026-03-02", "blocks": [
        {"assignment": "Essay", "course": "English", "time_slot": "4:30 PM - 5:30 PM", "duration_minutes": 60},
        {"is_break": True, "time_slot": "5:30 PM - 5:45 PM"},
    ]}]})
    assert ids == ["event"]
    assert created[0]["subject"] == "Study: Essay"


@pytest.mark.parametrize(('raw', 'expected'), [
    ('2026-10-23T12:30:00.1234567+02:00', datetime(2026, 10, 23, 10, 30, 0, 123456)),
    ('2026-10-23T12:30:00.0000000-07:00', datetime(2026, 10, 23, 19, 30)),
    ('2026-10-23T12:30:00.1234567Z', datetime(2026, 10, 23, 12, 30, 0, 123456)),
])
def test_fractional_graph_datetime_preserves_explicit_offset(raw, expected):
    assert outlook._graph_datetime({'dateTime': raw, 'timeZone': 'UTC'}) == expected


@pytest.mark.parametrize(('name', 'expected'), [
    ('UTC', datetime(2026, 11, 2, 12, 30)),
    ('America/Los_Angeles', datetime(2026, 11, 2, 20, 30)),
    ('Pacific Standard Time', datetime(2026, 11, 2, 20, 30)),
    ('Eastern Standard Time', datetime(2026, 11, 2, 17, 30)),
    ('India Standard Time', datetime(2026, 11, 2, 7)),
])
def test_graph_naive_datetime_uses_its_declared_zone(name, expected):
    assert outlook._graph_datetime({'dateTime': '2026-11-02T12:30:00.0000000',
                                    'timeZone': name}) == expected


def test_windows_all_day_event_blocks_its_whole_local_day_across_dst(monkeypatch):
    monkeypatch.setattr(outlook, 'graph_get', Mock(return_value={'value': [{
        'isAllDay': True, 'showAs': 'busy',
        'start': {'dateTime': '2026-11-01T00:00:00.0000000', 'timeZone': 'Pacific Standard Time'},
        'end': {'dateTime': '2026-11-02T00:00:00.0000000', 'timeZone': 'Pacific Standard Time'},
    }]}))
    assert outlook.busy_minutes_by_date({}, date(2026, 10, 31), days=4,
                                       tz_name='America/Los_Angeles', require_complete=True) == {
        date(2026, 11, 1): [(0, 1440)],
    }


def test_unknown_event_timezone_cannot_be_claimed_as_verified_free_time(monkeypatch):
    monkeypatch.setattr(outlook, 'graph_get', Mock(return_value={'value': [{
        'start': {'dateTime': '2026-11-02T12:30:00', 'timeZone': 'Unknown/Private'},
        'end': {'dateTime': '2026-11-02T13:30:00', 'timeZone': 'Unknown/Private'},
    }]}))
    with pytest.raises(ValueError, match='availability is incomplete'):
        outlook.busy_minutes_by_date({}, date(2026, 11, 2), require_complete=True)


def test_cancelled_and_free_events_do_not_block_even_if_zone_is_unknown(monkeypatch):
    event = {'start': {'dateTime': '2026-11-02T12:30:00', 'timeZone': 'Unknown/Private'},
             'end': {'dateTime': '2026-11-02T13:30:00', 'timeZone': 'Unknown/Private'}}
    monkeypatch.setattr(outlook, 'graph_get', Mock(return_value={'value': [
        dict(event, isCancelled=True), dict(event, showAs='free'), dict(event, showAs='workingElsewhere'),
    ]}))
    assert outlook.busy_minutes_by_date({}, date(2026, 11, 2), require_complete=True) == {}


@pytest.mark.parametrize('utc_response', [False, True])
def test_upcoming_all_day_event_preserves_its_source_calendar_dates(monkeypatch, utc_response):
    start = {'dateTime': '2026-11-02T00:00:00.0000000', 'timeZone': 'Tokyo Standard Time'}
    end = {'dateTime': '2026-11-03T00:00:00.0000000', 'timeZone': 'Tokyo Standard Time'}
    if utc_response:
        start = {'dateTime': '2026-11-01T15:00:00.0000000', 'timeZone': 'UTC'}
        end = {'dateTime': '2026-11-02T15:00:00.0000000', 'timeZone': 'UTC'}
    monkeypatch.setattr(outlook, 'graph_get', Mock(return_value={'value': [{
        'id': 'school-event', 'subject': 'School event', 'isAllDay': True,
        'start': start, 'end': end,
        'originalStartTimeZone': 'Tokyo Standard Time', 'originalEndTimeZone': 'Tokyo Standard Time',
    }]}))
    event, = outlook.get_upcoming_events({})
    assert event['start'] == '2026-11-02' and event['end'] == '2026-11-03'


def _export_fixture():
    return {'schedule': [{'date': '2026-10-23', 'blocks': [
        {'assignment': 'Essay', 'course': 'English',
         'time_slot': '4:30 PM - 5:30 PM', 'duration_minutes': 60},
    ]}]}


@pytest.mark.parametrize('skip_overlaps', [False, True])
def test_export_never_writes_when_calendar_read_fails(monkeypatch, skip_overlaps):
    monkeypatch.setattr(outlook, 'graph_get', Mock(side_effect=requests.Timeout('synthetic outage')))
    write = Mock(return_value={'id': 'unexpected-write'})
    monkeypatch.setattr(outlook, 'graph_post', write)
    with pytest.raises(requests.Timeout):
        outlook.export_schedule({'access_token': 'synthetic'}, _export_fixture(),
                                utc_offset_minutes=-420, skip_overlaps=skip_overlaps)
    write.assert_not_called()


@pytest.mark.parametrize('payload', [
    None, [], {'error': {'code': 'synthetic'}}, {'value': None}, {'value': [None]},
    {'value': [{'start': {}, 'end': {}}]},
    {'value': [{'start': {'dateTime': 12}, 'end': {'dateTime': 13}}]},
    {'value': [{'start': {'dateTime': '2026-10-23T23:00:00'},
                'end': {'dateTime': '2026-10-23T22:00:00'}}]},
])
@pytest.mark.parametrize('skip_overlaps', [False, True])
def test_export_rejects_unverified_calendar_payload(monkeypatch, payload, skip_overlaps):
    monkeypatch.setattr(outlook, 'graph_get', Mock(return_value=payload))
    write = Mock(return_value={'id': 'unexpected-write'})
    monkeypatch.setattr(outlook, 'graph_post', write)
    with pytest.raises(ValueError):
        outlook.export_schedule({'access_token': 'synthetic'}, _export_fixture(),
                                utc_offset_minutes=-420, skip_overlaps=skip_overlaps)
    write.assert_not_called()


@pytest.mark.parametrize('skip_overlaps', [False, True])
def test_export_rejects_incomplete_pagination(monkeypatch, skip_overlaps):
    fetch = Mock(return_value={'value': [], '@odata.nextLink': outlook.GRAPH + '/me/calendarView?$skip=1'})
    monkeypatch.setattr(outlook, 'graph_get', fetch)
    write = Mock(return_value={'id': 'unexpected-write'})
    monkeypatch.setattr(outlook, 'graph_post', write)
    with pytest.raises(ValueError, match='safe import limit'):
        outlook.export_schedule({'access_token': 'synthetic'}, _export_fixture(),
                                utc_offset_minutes=-420, skip_overlaps=skip_overlaps)
    assert fetch.call_count == 5
    write.assert_not_called()


def test_export_retry_reuses_transaction_id_without_credentials(monkeypatch):
    monkeypatch.setattr(outlook, 'graph_get', Mock(return_value={'value': []}))
    requests_sent = []
    def write(token, path, body):
        requests_sent.append(body)
        if len(requests_sent) == 1:
            raise requests.Timeout('Synthetic response lost after provider accepted the event')
        return {'id': 'provider-event'}
    monkeypatch.setattr(outlook, 'graph_post', write)
    with pytest.raises(requests.Timeout):
        outlook.export_schedule({'access_token': 'original-synthetic-secret'}, _export_fixture(),
                                utc_offset_minutes=-420)
    outlook.export_schedule({'access_token': 'rotated-synthetic-secret'}, _export_fixture(),
                            utc_offset_minutes=-420)
    transaction = requests_sent[0]['transactionId']
    assert requests_sent[1]['transactionId'] == transaction
    from uuid import UUID
    assert str(UUID(transaction)) == transaction
    assert 'synthetic' not in transaction


@pytest.mark.parametrize('change', ['date', 'duration_minutes', 'notes', 'task_key'])
def test_export_transaction_identity_changes_with_event_content(monkeypatch, change):
    monkeypatch.setattr(outlook, 'graph_get', Mock(return_value={'value': []}))
    sent = []
    monkeypatch.setattr(outlook, 'graph_post', lambda token, path, body: sent.append(body) or {'id': 'event'})
    outlook.export_schedule({}, _export_fixture(), utc_offset_minutes=-420)
    changed = _export_fixture()
    block = changed['schedule'][0]['blocks'][0]
    if change == 'date':
        changed['schedule'][0]['date'] = '2026-10-24'
    elif change == 'duration_minutes':
        block[change] = 45
    else:
        block[change] = 'changed'
    outlook.export_schedule({}, changed, utc_offset_minutes=-420)
    assert sent[0]['transactionId'] != sent[1]['transactionId']


def test_legacy_windows_zone_export_deduplicates_against_utc_calendar(monkeypatch):
    monkeypatch.setattr(outlook, 'graph_get', Mock(return_value={'value': [{
        'subject': 'Study: Essay',
        'start': {'dateTime': '2026-10-23T23:30:00.0000000', 'timeZone': 'UTC'},
        'end': {'dateTime': '2026-10-24T00:30:00.0000000', 'timeZone': 'UTC'},
    }]}))
    write = Mock(return_value={'id': 'unexpected-duplicate'})
    monkeypatch.setattr(outlook, 'graph_post', write)
    result = outlook.export_schedule({}, _export_fixture(), timezone_name='Pacific Standard Time')
    assert result == {'created': [], 'skipped': 1}
    write.assert_not_called()


def test_iana_and_windows_exports_identify_the_same_utc_event(monkeypatch):
    monkeypatch.setattr(outlook, 'graph_get', Mock(return_value={'value': []}))
    sent = []
    monkeypatch.setattr(outlook, 'graph_post', lambda token, path, body: sent.append(body) or {'id': 'event'})
    outlook.export_schedule({}, _export_fixture(), timezone_name='Pacific Standard Time')
    outlook.export_schedule({}, _export_fixture(), tz_name='America/Los_Angeles')
    assert sent[0]['transactionId'] == sent[1]['transactionId']
    assert sent[0]['start'] == sent[1]['start'] == {'dateTime': '2026-10-23T23:30:00', 'timeZone': 'UTC'}
