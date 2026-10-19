"""Restored timetable reads preserve rotation, bell times and owner scope."""
from datetime import date
import json

from flask import Flask
import mongomock
import pytest

from intelliplan.persistence.codec import pack_document
from intelliplan.persistence.mongo import MongoStore
from intelliplan.persistence.mongo_timetable import MongoTimetable
from intelliplan.persistence.timetable import class_busy


@pytest.fixture
def environment():
    app = Flask(__name__)
    client = mongomock.MongoClient()
    store = MongoStore('mongodb://test', 'timetable', client_factory=lambda *a, **k: client)
    store.transaction = lambda read: read(store.database, None)
    store.database.users.insert_many([{'id': 1}, {'id': 2}])
    reader = MongoTimetable(store)
    reader.ensure_indexes()
    app.extensions['mongo_store'] = store
    return app, store, reader


def seed(store):
    store.database.class_meetings.insert_many([
        {'id': 100, 'user_id': 1, 'course': 'Math', 'rotation_days': '1',
         'start_time': '08:00', 'end_time': '09:00'},
        {'id': 101, 'user_id': 1, 'course': 'English', 'rotation_days': '2', 'period': '2'},
        {'id': 102, 'user_id': 2, 'course': 'Peer', 'start_time': '12:00', 'end_time': '13:00'},
    ])
    store.database.timetable_settings.insert_one(pack_document({'id': 100, 'user_id': 1,
        'rotation_kind': 'ab', 'cycle_length': 2, 'anchor_date': '2026-09-28', 'anchor_day': 1,
        'school_weekdays': 'Mon,Tue,Wed,Thu,Fri',
        'skip_days_json': '[{"start":"2026-09-29","end":"2026-09-29","label":"Holiday"}]',
        'bell_json': json.dumps({'2': ['09:00', '10:00'], 'future': ['x' * 20000, 'unused']}),
        'future_column': 'retain'}))


def test_native_read_uses_source_rotation_holiday_bell_and_no_sql(environment):
    app, store, reader = environment
    seed(store)
    before = store.database.timetable_settings.find_one({'user_id': 1})
    with app.app_context():
        result = class_busy(1, date(2026, 9, 28), 7)
    assert result == {date(2026, 9, 28): [(480, 540)], date(2026, 9, 30): [(540, 600)],
                      date(2026, 10, 1): [(480, 540)], date(2026, 10, 2): [(540, 600)]}
    assert store.database.timetable_settings.find_one({'user_id': 1}) == before
    assert 'sqlalchemy' not in app.extensions


def test_owner_scope_missing_owner_and_missing_settings_defaults(environment):
    app, store, reader = environment
    seed(store)
    assert reader.busy(2, date(2026, 9, 28), 1) == {date(2026, 9, 28): [(720, 780)]}
    store.database.users.delete_one({'id': 1})
    assert reader.busy(1, date(2026, 9, 28), 4) == {}
    assert reader.busy(3, date(2026, 9, 28), 4) == {}


def test_native_kill_switch_and_invalid_flag_are_not_treated_as_enabled(environment):
    app, store, reader = environment
    seed(store)
    store.database.feature_flags.insert_one({'id': 1, 'key': 'timetable', 'enabled': False})
    assert reader.busy(1, date(2026, 9, 28), 1) == {}
    store.database.feature_flags.update_one({'id': 1}, {'$set': {'enabled': 'false'}})
    with pytest.raises(ValueError, match='flag'):
        reader.busy(1, date(2026, 9, 28), 1)


def test_planner_helper_avoids_sql_feature_check_and_rollback(environment, monkeypatch):
    import App
    app, store, reader = environment
    seed(store)
    def sql_must_not_run(*args):
        raise AssertionError('Native planner attempted an SQL feature lookup')
    monkeypatch.setattr(App, 'feature_enabled', sql_must_not_run)
    with app.app_context():
        assert App._class_busy_by_date(1, date(2026, 9, 28), 1) == {date(2026, 9, 28): [(480, 540)]}
        store.database.feature_flags.insert_one({'key': 'timetable', 'enabled': 'malformed'})
        assert App._class_busy_by_date(1, date(2026, 9, 28), 1) == {}


@pytest.fixture
def api_environment(environment, monkeypatch):
    from flask_login import LoginManager
    from intelliplan.persistence.mongo_accounts import MongoAccounts
    from intelliplan.api import timetable as api
    app, store, reader = environment
    app.config.update(TESTING=True, SECRET_KEY='synthetic-timetable')
    login = LoginManager(app)
    login.user_loader(MongoAccounts(store).get)
    app.register_blueprint(api.bp)
    monkeypatch.setattr(api, '_after_change', lambda uid: None)
    client = app.test_client()
    with client.session_transaction() as cookie:
        cookie['_user_id'] = '1'
        cookie['_fresh'] = True
    return client, store, reader


def test_native_api_class_crud_settings_and_foreign_owner(api_environment):
    client, store, reader = api_environment
    response = client.post('/api/timetable/classes', json={'course': 'Math', 'start': '08:00', 'end': '09:00'})
    assert response.status_code == 200
    identity = response.json['classes'][0]['id']
    store.database.class_meetings.update_one({'id': identity}, {'$set': {'future_column': 'retain'}})
    assert client.patch(f'/api/timetable/classes/{identity}', json={'room': '204'}).status_code == 200
    assert reader.list(1)[0].room == '204'
    assert store.database.class_meetings.find_one({'id': identity})['future_column'] == 'retain'
    reader.add(2, {'course': 'Peer'})
    foreign = reader.list(2)[0].id
    assert client.delete(f'/api/timetable/classes/{foreign}').status_code == 404
    assert client.patch(f'/api/timetable/classes/{foreign}', json={'course': 'forbidden'}).status_code == 404
    assert client.put('/api/timetable/settings', json={'kind': 'ab', 'today_is': 2, 'date': '2026-09-28'}).status_code == 200
    assert reader.get_settings(1).anchor_day == 2
    assert client.put('/api/timetable/settings', json={'kind': 'invalid'}).status_code == 400
    assert reader.get_settings(1).rotation_kind == 'ab'
    assert client.delete(f'/api/timetable/classes/{identity}').status_code == 200
    assert reader.list(1) == []


def test_native_import_preserves_manual_work_and_removes_only_own_source(environment):
    app, store, reader = environment
    reader.add(1, {'course': 'Math', 'period': '1', 'room': 'Student room', 'source': 'manual'})
    reader.add(1, {'course': 'Other source', 'source': 'schoology'})
    source = [{'course': 'Math', 'period': '1', 'room': 'Vendor room', 'teacher': 'Teacher'},
              {'course': 'English', 'period': '2', 'external_id': 'vendor-2'}]
    assert reader.import_classes(1, source, 'studentvue') == 2
    math = next(row for row in reader.list(1) if row.course == 'Math')
    assert math.room == 'Student room' and math.teacher == 'Teacher' and math.source == 'manual'
    assert reader.import_classes(1, [source[0]], 'studentvue') == 1
    assert {row.course for row in reader.list(1)} == {'Math', 'Other source'}
    assert source[1]['external_id'] == 'vendor-2'  # Inputs survive transaction retries.


def test_native_import_at_capacity_never_deletes_retained_classes(environment):
    from intelliplan.persistence.timetable import TimetableError
    app, store, reader = environment
    rows = [{'course': f'Class {n}', 'external_id': f'source-{n}'} for n in range(30)]
    reader.import_classes(1, rows, 'studentvue')
    originals = {row.id for row in reader.list(1)}
    with pytest.raises(TimetableError):
        reader.import_classes(1, [{'course': 'New'}, *rows], 'studentvue')
    assert {row.id for row in reader.list(1)} == originals
    assert reader.import_classes(1, [{'course': 'Replacement'}, *rows[1:]], 'studentvue') == 30
    assert len(reader.list(1)) == 30
    assert 'Class 0' not in {row.course for row in reader.list(1)}


def test_settings_partial_change_preserves_source_codec_fields_and_owner_guard(environment):
    app, store, reader = environment
    seed(store)
    reader.change_settings(1, lambda row: {'cycle_length': 4})
    saved = reader.get_settings(1)
    assert saved.cycle_length == 4 and saved.anchor_date == date(2026, 9, 28)
    assert saved.bell()['future'][0] == 'x' * 20000 and saved.future_column == 'retain'
    store.database.users.delete_one({'id': 1})
    for work in (lambda: reader.add(1, {'course': 'orphan'}),
                 lambda: reader.change_settings(1, lambda row: {'cycle_length': 3}),
                 lambda: reader.import_classes(1, [{'course': 'orphan'}], 'manual')):
        with pytest.raises(LookupError):
            work()


def test_native_import_api_settings_and_classes_share_transaction_boundary(api_environment, monkeypatch):
    from intelliplan.api import timetable as api
    client, store, reader = api_environment
    monkeypatch.setattr(api, '_linked_account', lambda kind: {'login_type': kind})
    monkeypatch.setattr(api, 'fetch_timetable', lambda *a: {
        'classes': [{'course': 'Math', 'period': '1', 'start': '08:00', 'end': '09:00'}],
        'rotation_hint': {'kind': 'ab', 'length': 2, 'date': '2026-09-28', 'today_is': 1},
        'bell': {'1': ['08:00', '09:00']}})
    response = client.post('/api/timetable/import', json={'source': 'studentvue'})
    assert response.status_code == 200 and response.json['imported'] == 1
    assert reader.get_settings(1).last_import_source == 'studentvue'
    assert reader.get_settings(1).rotation_kind == 'ab'
