"""Google export checks exercise the Calendar service boundary without network."""
from copy import deepcopy
from types import SimpleNamespace

from googleapiclient.errors import HttpError
import httplib2
import pytest
import requests

import google_calendar_helper as google


class Call:
    def __init__(self, action):
        self.action = action

    def execute(self):
        return self.action()


class Events:
    def __init__(self, pages=None):
        self.pages = pages or {None: {'items': [], 'timeZone': 'America/Los_Angeles'}}
        self.reads, self.writes, self.gets = [], [], []
        self.on_insert = None
        self.stored = {}

    def list(self, **kwargs):
        self.reads.append(kwargs)
        def read():
            response = self.pages[kwargs.get('pageToken')]
            if isinstance(response, Exception):
                raise response
            return deepcopy(response)
        return Call(read)

    def insert(self, **kwargs):
        body = deepcopy(kwargs['body'])
        self.writes.append(body)
        def write():
            if self.on_insert:
                return self.on_insert(body)
            self.stored[body['id']] = body
            return {'id': body['id']}
        return Call(write)

    def get(self, **kwargs):
        self.gets.append(kwargs)
        return Call(lambda: deepcopy(self.stored[kwargs['eventId']]))


def install(monkeypatch, events):
    service = SimpleNamespace(events=lambda: events)
    monkeypatch.setattr(google, 'get_calendar_service', lambda token: (service, SimpleNamespace(token='fresh-token')))


def schedule(day='2026-10-23'):
    return {'schedule': [{'date': day, 'blocks': [
        {'assignment': 'Essay', 'course': 'English', 'notes': 'Draft a paragraph',
         'task_key': 'essay', 'time_slot': '4:30 PM - 5:30 PM', 'duration_minutes': 60},
    ]}]}


def event(start='2026-10-23T20:00:00Z', end='2026-10-23T22:00:00Z', **values):
    return {'id': 'meeting', 'summary': 'Meeting', 'start': {'dateTime': start},
            'end': {'dateTime': end}, **values}


def test_legacy_normalized_rfc3339_event_never_mixes_naive_and_aware(monkeypatch):
    events = Events()
    install(monkeypatch, events)
    ids, _, skipped = google.add_schedule_to_calendar({}, schedule(), [{
        'id': 'meeting', 'title': 'Meeting', 'start': '2026-10-23T23:00:00+00:00',
        'end': '2026-10-24T01:00:00+00:00',
    }])
    assert ids == [] and skipped == 1
    assert events.writes == []


def test_export_uses_student_zone_and_utc_for_the_entire_event(monkeypatch):
    events = Events()
    install(monkeypatch, events)
    ids, token, skipped = google.add_schedule_to_calendar({}, schedule(), tz_name='America/New_York')
    assert len(ids) == 1 and token == 'fresh-token' and skipped == 0
    assert events.writes[0]['start'] == {'dateTime': '2026-10-23T20:30:00Z', 'timeZone': 'UTC'}
    assert events.writes[0]['end'] == {'dateTime': '2026-10-23T21:30:00Z', 'timeZone': 'UTC'}


def test_export_paginates_the_actual_future_schedule_range(monkeypatch):
    events = Events({None: {'items': [], 'nextPageToken': 'next', 'timeZone': 'UTC'},
                     'next': {'items': [event('2026-11-20T20:00:00Z', '2026-11-20T22:00:00Z')]}})
    install(monkeypatch, events)
    ids, _, skipped = google.add_schedule_to_calendar({}, schedule('2026-11-20'),
                                                      skip_overlaps=True, utc_offset_minutes=-240)
    assert ids == [] and skipped == 1 and len(events.reads) == 2
    assert events.reads[0]['timeMin'] < '2026-11-20T20:30:00Z' < events.reads[0]['timeMax']
    assert events.reads[0]['singleEvents'] is True and events.reads[1]['pageToken'] == 'next'


@pytest.mark.parametrize('non_busy', [{'transparency': 'transparent'}, {'status': 'cancelled'},
                                   {'eventType': 'workingLocation'}])
def test_non_busy_events_do_not_prevent_export(monkeypatch, non_busy):
    events = Events({None: {'items': [event(**non_busy)], 'timeZone': 'UTC'}})
    install(monkeypatch, events)
    ids, _, skipped = google.add_schedule_to_calendar({}, schedule(),
                                                      skip_overlaps=True, utc_offset_minutes=-240)
    assert len(ids) == 1 and skipped == 0


def test_all_day_event_uses_calendar_zone_and_exclusive_end_date(monkeypatch):
    events = Events({None: {'items': [{'id': 'all-day', 'summary': 'School holiday',
                                     'start': {'date': '2026-11-01'}, 'end': {'date': '2026-11-02'}}],
                           'timeZone': 'America/Los_Angeles'}})
    install(monkeypatch, events)
    ids, _, skipped = google.add_schedule_to_calendar({}, schedule('2026-11-01'),
                                                      skip_overlaps=True, tz_name='America/Los_Angeles')
    assert ids == [] and skipped == 1 and not events.writes
    ids, _, skipped = google.add_schedule_to_calendar({}, schedule('2026-11-02'),
                                                      skip_overlaps=True, tz_name='America/Los_Angeles')
    assert len(ids) == 1 and skipped == 0


def test_student_iana_zone_tracks_dst_instead_of_a_stale_fixed_offset(monkeypatch):
    events = Events()
    install(monkeypatch, events)
    google.add_schedule_to_calendar({}, schedule('2026-11-01'),
                                     tz_name='America/Los_Angeles', utc_offset_minutes=-420)
    assert events.writes[0]['start'] == {'dateTime': '2026-11-02T00:30:00Z', 'timeZone': 'UTC'}


def test_cancelled_tombstone_does_not_require_event_boundaries(monkeypatch):
    events = Events({None: {'items': [{'id': 'deleted', 'status': 'cancelled'}]}})
    install(monkeypatch, events)
    ids, _, skipped = google.add_schedule_to_calendar({}, schedule(), skip_overlaps=True, utc_offset_minutes=-240)
    assert len(ids) == 1 and skipped == 0


@pytest.mark.parametrize('payload', [None, [], {'error': {'code': 503}}, {'items': None}, {'items': [None]},
                                     {'items': [event(end='not-a-time')]},
                                     {'items': [event(end='2026-10-22T12:00:00Z')]}])
def test_unverified_preflight_never_writes(monkeypatch, payload):
    events = Events({None: payload})
    install(monkeypatch, events)
    with pytest.raises(ValueError, match='availability'):
        google.add_schedule_to_calendar({}, schedule(), skip_overlaps=True, utc_offset_minutes=-240)
    assert not events.writes


def test_calendar_outage_never_becomes_permission_to_write(monkeypatch):
    events = Events({None: requests.Timeout('synthetic')})
    install(monkeypatch, events)
    with pytest.raises(requests.Timeout):
        google.add_schedule_to_calendar({}, schedule(), skip_overlaps=True, utc_offset_minutes=-240)
    assert not events.writes


def test_exhausted_pagination_fails_before_any_write(monkeypatch):
    events = Events({None: {'items': [], 'nextPageToken': '1'}, **{
        str(page): {'items': [], 'nextPageToken': str(page + 1)} for page in range(1, 5)}})
    install(monkeypatch, events)
    with pytest.raises(ValueError, match='availability'):
        google.add_schedule_to_calendar({}, schedule(), skip_overlaps=False, utc_offset_minutes=-240)
    assert len(events.reads) == 5 and not events.writes


def test_lost_insert_response_retry_has_stable_id_and_verifies_conflict(monkeypatch):
    events = Events()
    install(monkeypatch, events)
    def insert(body):
        if body['id'] not in events.stored:
            events.stored[body['id']] = deepcopy(body)
            raise requests.Timeout('Response lost after Google accepted the event')
        raise HttpError(httplib2.Response({'status': '409'}), b'{"error":{"code":409}}')
    events.on_insert = insert
    with pytest.raises(requests.Timeout):
        google.add_schedule_to_calendar({'token': 'original-secret'}, schedule(), utc_offset_minutes=-240)
    ids, _, skipped = google.add_schedule_to_calendar({'token': 'rotated-secret'}, schedule(), utc_offset_minutes=-240)
    assert ids == [] and skipped == 1 and len(events.gets) == 1
    assert events.writes[0]['id'] == events.writes[1]['id']
    assert len(events.writes[0]['id']) == 64 and set(events.writes[0]['id']) <= set('0123456789abcdef')


def test_a_409_without_matching_managed_identity_is_not_claimed_success(monkeypatch):
    events = Events()
    install(monkeypatch, events)
    def insert(body):
        events.stored[body['id']] = dict(body, extendedProperties={'private': {'intelliplan_export': 'different'}})
        raise HttpError(httplib2.Response({'status': '409'}), b'{"error":{"code":409}}')
    events.on_insert = insert
    with pytest.raises((ValueError, HttpError)):
        google.add_schedule_to_calendar({}, schedule(), utc_offset_minutes=-240)


def test_repeated_export_skips_existing_managed_event(monkeypatch):
    events = Events()
    install(monkeypatch, events)
    google.add_schedule_to_calendar({}, schedule(), utc_offset_minutes=-240)
    events.pages[None] = {'items': [deepcopy(events.writes[0])], 'timeZone': 'UTC'}
    ids, _, skipped = google.add_schedule_to_calendar({}, schedule(), utc_offset_minutes=-240)
    assert ids == [] and skipped == 1 and len(events.writes) == 1


@pytest.mark.parametrize('change', ['date', 'notes', 'duration_minutes', 'task_key'])
def test_event_identity_changes_with_timestamp_or_content(monkeypatch, change):
    events = Events()
    install(monkeypatch, events)
    google.add_schedule_to_calendar({}, schedule(), utc_offset_minutes=-240)
    changed = schedule()
    if change == 'date':
        changed['schedule'][0]['date'] = '2026-10-24'
    else:
        changed['schedule'][0]['blocks'][0][change] = 45 if change == 'duration_minutes' else 'changed'
    google.add_schedule_to_calendar({}, changed, utc_offset_minutes=-240)
    assert events.writes[0]['id'] != events.writes[1]['id']
