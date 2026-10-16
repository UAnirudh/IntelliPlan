from datetime import date, datetime

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
