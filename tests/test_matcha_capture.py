"""Todoist consent, project boundaries, and reviewed calendar capture."""
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest

import App
from intelliplan.api import todoist
from intelliplan.learning import photo_schedule
from intelliplan.persistence.lms_grants import lms_grants


@pytest.fixture
def account(monkeypatch):
    monkeypatch.setenv("TODOIST_CLIENT_ID", "local-test-client")
    monkeypatch.setenv("TODOIST_CLIENT_SECRET", "local-test-secret")
    monkeypatch.setattr(App.limiter, "enabled", False)
    with App.app.app_context():
        App.db.create_all()
        user = App.User(email=f"capture-{uuid4().hex}@example.test", birth_year=2000, email_verified=True,
                        password_hash="fixture", ai_personalization_opt_in=True)
        App.db.session.add(user)
        App.db.session.commit()
        uid = user.id
    client = App.app.test_client()
    with client.session_transaction() as session:
        session.update(_user_id=str(uid), _fresh=True, session_epoch=0)
    return client, uid


def _reply(payload):
    return SimpleNamespace(status_code=200, json=lambda: payload)


def test_oauth_and_project_scope(account, monkeypatch):
    client, uid = account
    calls = []
    token_calls = []
    def token(url, **kwargs):
        token_calls.append(kwargs["data"]["code"])
        return _reply({"access_token": "fixture-token", "refresh_token": "fixture-refresh",
                       "expires_in": 3600, "scope": "data:read"})
    def get(url, **kwargs):
        if url.endswith("/projects"):
            return _reply({"results": [{"id": "selected", "name": "Homework"},
                                       {"id": "personal", "name": "Private"}]})
        calls.append(kwargs["params"]["project_id"])
        return _reply({"results": [
            {"id": "task1", "content": "Study Biology", "project_id": "selected", "priority": 4,
             "due": {"date": "2026-11-20T18:30:00"}, "duration": {"amount": 45, "unit": "minute"}},
            {"id": "hidden", "content": "Private", "project_id": "personal"},
            {"id": "done", "content": "Already done", "project_id": "selected", "checked": True},
        ]})
    monkeypatch.setattr(todoist.requests, "post", token)
    monkeypatch.setattr(todoist.requests, "get", get)

    state = parse_qs(urlsplit(client.get("/oauth/todoist/start").location).query)
    assert state["scope"] == ["data:read"]
    client.get("/oauth/todoist/callback", query_string={"state": "invalid", "code": "bad"})
    assert token_calls == []
    state = parse_qs(urlsplit(client.get("/oauth/todoist/start").location).query)["state"][0]
    assert "connected" in client.get("/oauth/todoist/callback",
        query_string={"state": state, "code": "valid"}).location
    assert token_calls == ["valid"]
    path = "/api/todoist/connection"
    assert client.get(path).json["selected"] == []
    assert client.post(path, json={"projects": ["other"]}).status_code == 400
    assert client.post(path, json={"projects": ["selected"]}).json["selected"] == ["selected"]
    with App.app.app_context():
        user = App.db.session.get(App.User, uid)
        rows = todoist.assignments(user)
        assert len(rows) == 1 and rows[0]["id"] == "todoist:task1"
        assert rows[0]["due_date"] == "2026-11-20"
        assert rows[0]["due_at"] == "2026-11-20T18:30:00"
        assert rows[0]["estimated_time"] == 45
    scheduler_rows = client.get('/api/scheduler/assignments').json
    assert [row['id'] for row in scheduler_rows] == ['todoist:task1']
    assert calls == ["selected", "selected"]
    assert client.delete(path).status_code == 200
    with App.app.app_context():
        assert lms_grants("todoist").get(uid) is None
        assert not todoist.filter_assignments(App.db.session.get(App.User, uid), rows)


def test_scheduler_and_autopilot_read_selected_notion_database(account, monkeypatch):
    from followthrough_glue import _owner_assignments
    from intelliplan.persistence.notion import notion_accounts

    client, uid = account
    queries = []

    def pull(token, database):
        assert token == 'test-notion-token'
        queries.append(database)
        return [{'id': database, 'title': f'Task for {database}', 'course': 'Notion',
                 'due_date': '2026-11-20', 'due_at': '2026-11-20T08:40:00-08:00'}]

    monkeypatch.setattr(App, 'NOTION_AVAILABLE', True)
    monkeypatch.setattr(App, 'get_notion_tasks', pull, raising=False)
    with App.app.app_context():
        notion_accounts().connect(uid, token='test-notion-token')
        notion_accounts().select_database(uid, 'selected-database')

    path = '/api/scheduler/assignments?include_waiting=1'
    response = client.get(path)
    assert response.status_code == 200
    assert response.json[0]['source'] == 'notion'
    assert response.json[0]['due_at'] == '2026-11-20T08:40:00-08:00'
    assert 'no-store' in response.headers['Cache-Control']

    with App.app.app_context():
        assert _owner_assignments(uid, None)[0]['title'] == 'Task for selected-database'
        App.collect_lms_assignments_for_user(uid)
        notion_accounts().select_database(uid, 'replacement-database')
        # The database was changed directly, without invalidating the cache.
        assert App.collect_lms_assignments_for_user(uid) == []

    assert client.get(path).json[0]['title'] == 'Task for replacement-database'
    assert client.patch('/api/settings/lms-sources', json={'assignment_sources': []}).status_code == 200
    assert client.get(path).json == []
    assert queries == ['selected-database', 'selected-database', 'selected-database', 'replacement-database']


def test_notion_import_preserves_exact_date_time(monkeypatch):
    import notion_helper

    def row(key, start):
        return {'id': key, 'properties': {
            'Name': {'type': 'title', 'title': [{'plain_text': 'Plant science'}]},
            'Due': {'type': 'date', 'date': {'start': start}},
        }}

    tasks = [row('date-only', '2026-11-20'), row('timed', '2026-11-21T08:40:00-08:00')]
    databases = SimpleNamespace(query=lambda **kwargs: {'results': tasks, 'has_more': False})
    monkeypatch.setattr(notion_helper, 'get_notion_client', lambda token: SimpleNamespace(databases=databases))
    date_only, timed = notion_helper.get_notion_tasks('fixture', 'selected-database')
    assert date_only['due_date'] == '2026-11-20' and 'due_at' not in date_only
    assert timed['due_date'] == '2026-11-21'
    assert timed['due_at'] == '2026-11-21T08:40:00-08:00'


def test_photo_schedule_omits_unknown_values():
    assert photo_schedule.draft({
        "events": [{"title": "Game", "date": "2027-02-29", "start": "14:00"}],
        "classes": [{"title": "Math", "weekdays": "Wednesday, Mon", "start": "9am"}],
    }) == {"events": [{"title": "Game", "date": "", "start": "14:00", "end": ""}],
           "classes": [{"title": "Math", "weekdays": "Mon", "start": "", "end": ""}]}


def test_reviewed_calendar_file_and_google_dispatch(account, monkeypatch):
    client, uid = account
    path = "/api/flashcards/photo/calendar"
    event = {"title": "Science; lab", "date": "2026-11-20", "start": "15:00", "end": "16:30"}
    draft = {"events": [event, event, {"title": "No school", "date": "2026-11-26", "all_day": True}],
             "timezone": "America/Los_Angeles"}
    response = client.post(path, json=draft)
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "DTSTART:20261120T230000Z" in body
    assert "DTEND;VALUE=DATE:20261127" in body
    assert "SUMMARY:Science\\; lab" in body
    assert body.count("BEGIN:VEVENT") == 2
    assert client.post(path, json={"events": [{**event, "end": ""}]}).status_code == 400
    assert client.post(path, json={**draft, "destination": "google"}).status_code == 409
    import google_calendar_helper
    monkeypatch.setattr(App, "get_google_token", lambda user_id: {"token": str(user_id)})
    def write(token, events):
        assert int(token["token"]) == uid
        assert len(events) == 2 and events[0]["summary"] == event["title"]
        return 2, 0, token["token"]
    monkeypatch.setattr(google_calendar_helper, "add_reviewed_events", write)
    assert client.post(path, json={**draft, "destination": "google"}).json == {"created": 2, "skipped": 0}

def test_google_calendar_retries_do_not_duplicate_confirmed_events(monkeypatch):
    from googleapiclient.errors import HttpError
    from httplib2 import Response
    import google_calendar_helper as google

    rows = photo_schedule.calendar_events(
        [{"title": "Science fair", "date": "2026-12-10", "all_day": True}], zone="", offset=0)
    saved = {}

    def insert(*, body, calendarId, sendUpdates):
        identity = body["id"]
        assert calendarId == "primary" and sendUpdates == "none"
        if identity in saved:
            def conflict():
                raise HttpError(Response({"status": "409"}), b'{"error":{"message":"duplicate"}}')
            return SimpleNamespace(execute=conflict)
        saved[identity] = body
        return SimpleNamespace(execute=lambda: {"id": identity})

    def get(*, calendarId, eventId):
        return SimpleNamespace(execute=lambda: saved[eventId])

    connection = SimpleNamespace(events=lambda: SimpleNamespace(insert=insert, get=get))
    monkeypatch.setattr(google, "get_calendar_service", lambda _: (connection, SimpleNamespace(token="test")))
    grant = {"scopes": google.CALENDAR_SCOPES}
    assert google.add_reviewed_events(grant, rows) == (1, 0, "test")
    assert google.add_reviewed_events(grant, rows) == (0, 1, "test")
