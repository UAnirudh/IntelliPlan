from datetime import date

import outlook_calendar_helper as outlook


class _Response:
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


def test_export_creates_only_study_blocks(monkeypatch):
    created = []
    monkeypatch.setattr(outlook, "graph_post", lambda token, path, body: created.append(body) or {"id": "event"})
    ids = outlook.add_schedule_to_calendar({"access_token": "x"}, {"schedule": [{"date": "2026-03-02", "blocks": [
        {"assignment": "Essay", "course": "English", "time_slot": "4:30 PM - 5:30 PM", "duration_minutes": 60},
        {"is_break": True, "time_slot": "5:30 PM - 5:45 PM"},
    ]}]})
    assert ids == ["event"]
    assert created[0]["subject"] == "Study: Essay"
