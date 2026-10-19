"""Native manual-task CRUD, guest boundaries and completion transitions."""
from flask import Flask, session
from flask_login import LoginManager
import mongomock
import pytest

import App
from intelliplan.persistence.codec import pack_document
from intelliplan.persistence.manual_tasks import owner
from intelliplan.persistence.mongo import MongoStore
from intelliplan.persistence.mongo_accounts import MongoAccounts
from intelliplan.persistence.mongo_manual_tasks import MongoTasks


@pytest.fixture
def environment(monkeypatch):
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-task')
    client = mongomock.MongoClient()
    store = MongoStore('mongodb://test', 'tasks', client_factory=lambda *a, **k: client)
    store.transaction = lambda work: work(store.database, None)
    store.database.users.insert_many([{'id': 1}, {'id': 2}])
    app.extensions['mongo_store'] = store
    repo = MongoTasks(store, owner(1))
    repo.ensure_indexes()
    login = LoginManager(app)
    login.user_loader(MongoAccounts(store).get)
    for path, method in [('create', App.manual_create_task), ('update', App.manual_update_task), ('delete', App.manual_delete_task)]:
        app.add_url_rule('/' + path, view_func=method, methods=['POST'])
    app.add_url_rule('/list', view_func=App.manual_list_tasks)
    monkeypatch.setattr(App, 'get_guest_session_id', lambda: session.setdefault('guest_id', 'synthetic-guest'))
    monkeypatch.setattr(App, 'NOTION_AVAILABLE', False)
    monkeypatch.setattr(App, 'invalidate_lms_cache_for_user', lambda uid: None)
    rewards = []
    monkeypatch.setattr(App, '_record_streak_qualifying_action', lambda *a: rewards.append('streak'))
    monkeypatch.setattr(App, '_award_pet_xp', lambda *a, **k: rewards.append('pet'))
    return app, store, repo, rewards


def sign_in(app, uid=1):
    client = app.test_client()
    with client.session_transaction() as cookie:
        cookie['_user_id'] = str(uid)
        cookie['_fresh'] = True
    return client


def test_native_manual_crud_and_owner_isolation(environment):
    app, store, repo, rewards = environment
    client = sign_in(app)
    identity = client.post('/create', json={'title': 'Math', 'estimated_time': 25}).json['id']
    assert client.get('/list').json[0]['title'] == 'Math'
    assert client.post('/update', json={'id': identity, 'title': 'English'}).status_code == 200
    assert repo.get(identity).title == 'English'
    assert sign_in(app, 2).get('/list').json == []
    assert sign_in(app, 2).post('/update', json={'id': identity, 'done': True}).status_code == 404
    assert sign_in(app, 2).post('/delete', json={'id': identity}).status_code == 404
    assert client.post('/delete', json={'id': identity}).status_code == 200
    assert client.post('/delete', json={'id': identity}).status_code == 200
    assert repo.get(identity) is None and 'sqlalchemy' not in app.extensions


def test_completion_rewards_only_on_false_to_true_transition(environment):
    app, store, repo, rewards = environment
    identity = repo.create({'title': 'Math'}).id
    client = sign_in(app)
    for _ in range(2):
        assert client.post('/update', json={'id': identity, 'done': True}).status_code == 200
    assert rewards == ['streak', 'pet']
    assert client.get('/list').json == []
    assert client.post('/update', json={'id': identity, 'done': 'false'}).status_code == 400
    assert repo.get(identity).done is True


def test_guest_scope_cannot_reach_authenticated_or_other_guest_tasks(environment):
    app, store, repo, rewards = environment
    owned = repo.create({'title': 'Private'})
    guest = app.test_client()
    identity = guest.post('/create', json={'title': 'Guest task'}).json['id']
    assert [row['title'] for row in guest.get('/list').json] == ['Guest task']
    assert guest.post('/update', json={'id': owned.id, 'done': True}).status_code == 404
    second = app.test_client()
    with second.session_transaction() as cookie:
        cookie['guest_id'] = 'other-guest'
    assert second.post('/delete', json={'id': identity}).status_code == 404
    assert sign_in(app).post('/delete', json={'id': identity}).status_code == 404


def test_source_unknown_fields_compressed_notes_ids_and_erasure_guard(environment):
    app, store, repo, rewards = environment
    store.database.manual_tasks.insert_one(pack_document({'id': 100, 'user_id': 1,
        'title': 'Restored', 'notes': 'n' * 20000, 'future_field': 'retain', 'done': False,
        'created_at': '2026-10-19T00:00:00.123456'}))
    store.seed_id('manual_tasks', 100)
    row, completed = repo.update(100, {'course': 'Biology'})
    assert row.notes == 'n' * 20000 and row.future_field == 'retain' and not completed
    assert row.created_at.microsecond == 123456
    assert repo.create({'title': 'New'}).id == 101
    store.database.users.delete_one({'id': 1})
    for work in (lambda: repo.create({'title': 'orphan'}), lambda: repo.update(100, {'title': 'orphan'})):
        with pytest.raises(LookupError):
            work()


def test_notion_result_cannot_recreate_a_deleted_task(environment, monkeypatch):
    app, store, repo, rewards = environment
    monkeypatch.setattr(App, 'NOTION_AVAILABLE', True)
    monkeypatch.setattr(App, 'get_notion_token_and_db', lambda: ('synthetic-token', 'synthetic-db'))
    def remote_create(*a):
        repo.delete(repo.list()[0].id)
        return 'synthetic-page'
    monkeypatch.setattr(App, 'create_notion_task', remote_create)
    assert sign_in(app).post('/create', json={'title': 'Math', 'sync_notion': True}).status_code == 200
    assert repo.list() == []


@pytest.mark.parametrize('field,value', [('title', []), ('title', None), ('priority', {}),
    ('due_date', {'date': 'tomorrow'}), ('notes', []), ('course', 'x' * 257), ('estimated_time', 'invalid')])
def test_malformed_manual_fields_are_rejected_before_persisting(environment, field, value):
    app, store, repo, rewards = environment
    client = sign_in(app)
    assert client.post('/create', json={'title': 'Math', field: value}).status_code == 400
    assert repo.list() == []
    row = repo.create({'title': 'Math'})
    assert client.post('/update', json={'id': row.id, field: value}).status_code == 400
    assert repo.get(row.id).title == 'Math'


def test_shared_sql_validation_rejects_structured_fields_without_database_access():
    from intelliplan.persistence.manual_tasks import SQLTasks
    repository = SQLTasks(None, None, None, {'user_id': 1})
    with pytest.raises(ValueError):
        repository.create({'title': 'Math', 'priority': {}})
    with pytest.raises(ValueError):
        repository.update(1, {'title': None})
