"""Timeline moves retain restored plan fields and refuse stale writes."""
from datetime import date
import json

from flask import Flask
from flask_login import LoginManager
import mongomock
import pytest

from intelliplan.persistence.codec import pack_document, unpack_document
from intelliplan.persistence.mongo import MongoStore
from intelliplan.persistence.mongo_accounts import MongoAccounts
from intelliplan.persistence.mongo_schedule_plans import MongoPlans


@pytest.fixture
def environment(monkeypatch):
    from intelliplan.api import timetable as api
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-plan')
    client = mongomock.MongoClient()
    store = MongoStore('mongodb://test', 'plans', client_factory=lambda *a, **k: client)
    store.transaction = lambda work: work(store.database, None)
    store.database.users.insert_many([{'id': 1}, {'id': 2}])
    repo = MongoPlans(store)
    repo.ensure_indexes()
    app.extensions['mongo_store'] = store
    login = LoginManager(app)
    login.user_loader(MongoAccounts(store).get)
    app.register_blueprint(api.bp)
    monkeypatch.setattr(api, '_after_change', lambda uid: None)
    monkeypatch.setattr(api, '_calendar_busy', lambda day: [])
    browser = app.test_client()
    with browser.session_transaction() as cookie:
        cookie['_user_id'] = '1'
        cookie['_fresh'] = True
    return browser, store, repo


def seed(store, **changes):
    block = {'block_id': 'synthetic-block', 'assignment': 'Math', 'course': 'Math',
        'duration_minutes': 60, 'start_iso': '2026-09-28T15:00:00', 'end_iso': '2026-09-28T16:00:00',
        'is_break': False, 'time_slot': ''}
    plan = {'schedule': [{'date': '2026-09-28', 'blocks': [block]}], 'unknown_metadata': 'x' * 20000}
    store.database.saved_schedules.insert_one(pack_document({'id': 100, 'user_id': 1,
        'is_active': True, 'created_at': '2026-09-28T00:00:00.123456',
        'schedule_data': json.dumps(plan), 'progress_json': '{}', 'future_field': 'retain', **changes}))


def test_active_plan_reads_owner_and_codec_fields(environment):
    browser, store, repo = environment
    seed(store)
    original = repo.active(1)
    assert original.id == 100 and original.created_at.microsecond == 123456
    assert json.loads(original.schedule_data)['unknown_metadata'] == 'x' * 20000
    assert repo.active(2) is None
    assert not repo.replace(2, original, '{}')


def test_plan_replace_rejects_stale_progress_inactive_and_deleted_owner(environment):
    browser, store, repo = environment
    seed(store)
    original = repo.active(1)
    store.database.saved_schedules.update_one({'id': 100}, {'$set': {'progress_json': '{"done":true}'}})
    assert not repo.replace(1, original, '{}')
    current = repo.active(1)
    store.database.saved_schedules.update_one({'id': 100}, {'$set': {'is_active': False}})
    assert not repo.replace(1, current, '{}')
    store.database.saved_schedules.update_one({'id': 100}, {'$set': {'is_active': True}})
    store.database.users.delete_one({'id': 1})
    assert not repo.replace(1, current, '{}')


def test_plan_replace_preserves_unknown_fields_and_clears_compression_metadata(environment):
    browser, store, repo = environment
    seed(store)
    original = repo.active(1)
    assert repo.replace(1, original, '{"schedule":[]}')
    assert not repo.replace(1, original, '{}')
    raw = store.database.saved_schedules.find_one({'id': 100})
    assert raw['future_field'] == 'retain' and raw['progress_json'] == '{}'
    assert unpack_document(raw)['schedule_data'] == '{"schedule":[]}'


def test_native_timeline_preview_commit_and_class_conflict(environment):
    browser, store, repo = environment
    seed(store)
    original = repo.active(1).schedule_data
    payload = {'block_id': 'synthetic-block', 'date': '2026-09-28', 'start': '16:00', 'preview': True}
    response = browser.post('/api/today/timeline/move', json=payload)
    assert response.status_code == 200 and response.json['saved'] is False
    assert repo.active(1).schedule_data == original
    response = browser.post('/api/today/timeline/move', json={**payload, 'preview': False})
    assert response.status_code == 200 and response.json['saved'] is True
    assert json.loads(repo.active(1).schedule_data)['schedule'][0]['blocks'][0]['start_iso'] == '2026-09-28T16:00:00'
    store.database.class_meetings.insert_one({'id': 1, 'user_id': 1, 'course': 'English',
        'start_time': '18:00', 'end_time': '19:00'})
    before = repo.active(1).schedule_data
    assert browser.post('/api/today/timeline/move', json={**payload, 'preview': False, 'start': '18:00'}).status_code == 409
    assert repo.active(1).schedule_data == before
    assert browser.get('/api/today/timeline?date=' + date(2026, 9, 28).isoformat()).status_code == 200


def test_native_timeline_refuses_plan_changed_after_validation(environment, monkeypatch):
    from intelliplan.api import timetable as api
    browser, store, repo = environment
    seed(store)
    apply_move = api.timeline_service.apply_move
    def concurrent_edit(*args):
        apply_move(*args)
        store.database.saved_schedules.update_one({'id': 100}, {'$set': {'progress_json': '{"changed":true}'}})
    monkeypatch.setattr(api.timeline_service, 'apply_move', concurrent_edit)
    response = browser.post('/api/today/timeline/move', json={
        'block_id': 'synthetic-block', 'date': '2026-09-28', 'start': '16:00'})
    assert response.status_code == 409
    assert '15:00:00' in repo.active(1).schedule_data


def test_timeline_move_preserves_unknown_exact_json_numbers(environment):
    from decimal import Decimal
    from intelliplan.persistence.codec import update_fields
    from intelliplan.persistence.json_values import loads_exact
    browser, store, repo = environment
    seed(store)
    text = repo.active(1).schedule_data
    precise = '0.12345678901234567890123456789'
    store.database.saved_schedules.update_one({'id': 100}, update_fields({
        'schedule_data': text[:-1] + ',"precise":' + precise + '}'}))
    response = browser.post('/api/today/timeline/move', json={
        'block_id': 'synthetic-block', 'date': '2026-09-28', 'start': '16:00'})
    assert response.status_code == 200
    assert loads_exact(repo.active(1).schedule_data)['precise'] == Decimal(precise)
