"""Bounded transcript reads and incremental catch-up at the actual room API."""
from datetime import datetime
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest
from sqlalchemy import event

import App


@pytest.fixture
def room_client():
    previous_testing = App.app.config.get('TESTING')
    previous_limiter = App.limiter.enabled
    App.app.config['TESTING'] = True
    App.limiter.enabled = False
    with App.app.app_context():
        room = App.LiveSession(title='Polling fixture', room_slug='poll-' + uuid.uuid4().hex)
        App.db.session.add(room)
        App.db.session.flush()
        rid = room.id
        rows = [App.SessionMessage(context_type='live', context_id=rid,
                                  author_name='Synthetic', body='x' * 4000,
                                  created_at=datetime(2026, 10, 22)) for _ in range(205)]
        App.db.session.add_all(rows)
        App.db.session.commit()
        ids = [row.id for row in rows]
    with App.app.test_client() as client:
        yield client, rid, ids
    with App.app.app_context():
        App.SessionMessage.query.filter_by(context_type='live', context_id=rid).delete()
        App.LiveSession.query.filter_by(id=rid).delete()
        App.db.session.commit()
    App.app.config['TESTING'] = previous_testing
    App.limiter.enabled = previous_limiter


def url(room):
    return f'/api/sessions/live/{room}/messages'


def test_initial_window_is_bounded_and_older_history_remains_reachable(room_client):
    client, rid, ids = room_client
    first = client.get(url(rid)).get_json()
    assert [m['id'] for m in first['messages']] == ids[-100:]
    assert first['has_more_before'] is True
    assert first['next_after_id'] == ids[-1]
    second = client.get(url(rid), query_string={'before_id': first['messages'][0]['id']}).get_json()
    assert [m['id'] for m in second['messages']] == ids[5:105]
    oldest = client.get(url(rid), query_string={'before_id': second['messages'][0]['id']}).get_json()
    assert [m['id'] for m in oldest['messages']] == ids[:5]
    assert oldest['has_more_before'] is False


def test_unchanged_poll_fetches_no_message_body_rows(room_client):
    client, rid, ids = room_client
    selects = []
    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith('SELECT') and 'session_messages' in statement:
            selects.append(statement)
    with App.app.app_context():
        engine = App.db.engine
        event.listen(engine, 'before_cursor_execute', observe)
    try:
        response = client.get(url(rid), query_string={'after_id': ids[-1]})
    finally:
        event.remove(engine, 'before_cursor_execute', observe)
    assert response.status_code == 200
    assert response.json['messages'] == []
    assert response.json['next_after_id'] == ids[-1]
    assert len(response.data) < 300
    assert len(selects) == 1
    assert 'session_messages.id >' in selects[0] and 'LIMIT' in selects[0]


def test_bounded_forward_pages_have_no_gaps_or_duplicates(room_client):
    client, rid, ids = room_client
    cursor = 0
    found = []
    for _ in range(4):
        response = client.get(url(rid), query_string={'after_id': cursor}).get_json()
        found.extend(row['id'] for row in response['messages'])
        cursor = response['next_after_id']
        if not response['has_more']:
            break
    assert found == ids
    assert len(set(found)) == len(ids)
    assert cursor == ids[-1]


@pytest.mark.parametrize('query', [
    {'after_id': '-1'}, {'after_id': 'oops'}, {'after_id': '1.0'},
    {'after_id': '9' * 50}, {'before_id': '0'}, {'before_id': 'oops'},
    {'after_id': '1', 'before_id': '2'}, {'limit': '1000'}, {'limit': '0'},
])
def test_invalid_cursors_and_page_sizes_are_rejected(room_client, query):
    client, rid, _ = room_client
    assert client.get(url(rid), query_string=query).status_code == 400


def test_cursor_cannot_cross_room_boundaries(room_client):
    client, rid, ids = room_client
    with App.app.app_context():
        other = App.LiveSession(title='Other room', room_slug='other-' + uuid.uuid4().hex)
        App.db.session.add(other)
        App.db.session.flush()
        other_id = other.id
        App.db.session.add(App.SessionMessage(context_type='live', context_id=other_id,
                                             body='OTHER PRIVATE CONTEXT', author_name='Synthetic'))
        App.db.session.commit()
    try:
        response = client.get(url(rid), query_string={'after_id': ids[-1]})
        assert response.json['messages'] == []
        assert b'OTHER PRIVATE CONTEXT' not in response.data
    finally:
        with App.app.app_context():
            App.SessionMessage.query.filter_by(context_type='live', context_id=other_id).delete()
            App.LiveSession.query.filter_by(id=other_id).delete()
            App.db.session.commit()


@pytest.mark.skipif(shutil.which('node') is None, reason='needs Node for real session panel execution')
def test_incremental_client_and_retry_behaviour():
    script = Path(__file__).parent / 'browser/session_chat_polling.test.cjs'
    result = subprocess.run(['node', '--test', str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
