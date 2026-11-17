"""Native tutor history, owner isolation, compressed snapshots and web routes."""
from decimal import Decimal
import json

from flask import Flask
from flask_login import LoginManager
import mongomock
import pytest

import chatbot_api as tutor
from intelliplan.persistence.codec import update_fields
from intelliplan.persistence.mongo import MongoStore
from intelliplan.persistence.mongo_accounts import MongoAccounts
from intelliplan.persistence.mongo_tutor_history import MongoTutorHistory


@pytest.fixture
def history_app():
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-history-test')
    client = mongomock.MongoClient()
    store = MongoStore('mongodb://test', 'history', client_factory=lambda *a, **k: client)
    store.transaction = lambda work: work(store.database, None)
    store.database.users.insert_many([{'id': 1, 'email': 'one@example.invalid'}, {'id': 2, 'email': 'two@example.invalid'}])
    app.extensions['mongo_store'] = store
    login = LoginManager(app)
    login.user_loader(MongoAccounts(store).get)
    app.register_blueprint(tutor.chatbot_bp)
    return app, store


def repository(store, user_id=1, guest_id=None):
    result = MongoTutorHistory(store, lambda: (user_id, guest_id), tutor._normalize_tutor_messages,
                               tutor._merge_tutor_messages, tutor._default_tutor_profile)
    result.ensure_indexes()
    return result


def test_native_memory_and_conversations_are_owner_scoped(history_app):
    _, store = history_app
    own, peer = repository(store), repository(store, 2)
    guest, other_guest = repository(store, None, 'guest-one'), repository(store, None, 'guest-two')
    memory = own.load_memory()
    assert own.load_memory()['id'] == memory['id']
    assert memory['created_at'].microsecond >= 0
    transcript = [{'role': 'user', 'content': 'Hello'}, {'role': 'assistant', 'content': 'Synthetic reply'}]
    identity = own.create_conversation('School chat')
    own.save_conversation(identity, transcript)
    assert json.loads(own.get_conversation(identity)['messages_json']) == transcript
    assert peer.get_conversation(identity) is None
    assert guest.get_conversation(identity) is None
    for outsider in (peer, guest):
        with pytest.raises(LookupError):
            outsider.save_conversation(identity, [{'role': 'user', 'content': 'Wrong owner'}])
        with pytest.raises(LookupError):
            outsider.save_profile(memory['id'], {'turns': 999})
        assert not outsider.delete_conversation(identity)
    assert own.rename_conversation(identity, 'Renamed')['title'] == 'Renamed'
    assert own.list_conversations()[0]['title'] == 'Renamed'
    guest_id = guest.create_conversation('Guest conversation')
    assert other_guest.get_conversation(guest_id) is None
    assert own.get_conversation(guest_id) is None
    assert own.delete_conversation(identity)
    assert own.get_conversation(identity) is None


def test_compressed_transcripts_and_exact_profile_values_survive_updates(history_app):
    _, store = history_app
    repo = repository(store)
    memory = repo.load_memory()
    raw = '{"future_precise":0.12345678901234567890123456789,"turns":0}'
    store.database.tutor_memory.update_one({'id': memory['id']}, update_fields({'profile_json': raw}))
    for index in range(3):
        repo.record_profile_turn(memory['id'], '[Subject: Math] help me with algebra', 'Example', tutor._update_tutor_profile)
    profile = json.loads(repo.load_memory()['profile_json'], parse_float=Decimal)
    assert profile['future_precise'] == Decimal('0.12345678901234567890123456789')
    assert profile['turns'] == 3
    identity = repo.create_conversation()
    messages = [{'role': 'user', 'content': 'x' * 6000}, {'role': 'assistant', 'content': 'y' * 6000}]
    repo.save_conversation(identity, messages)
    assert store.database.tutor_conversations.find_one({'id': identity})['_ip_encoding']['messages_json']
    assert repo.get_conversation(identity)['messages_json'] == json.dumps(messages)
    assert repo.rename_conversation(identity, 'Short')['messages_json'] == json.dumps(messages)
    store.database.tutor_conversations.update_one({'id': identity}, {'$set': {'unknown_future': 'keep'}})
    repo.save_conversation(identity, messages + [{'role': 'user', 'content': 'Next'}])
    assert store.database.tutor_conversations.find_one({'id': identity})['unknown_future'] == 'keep'


def test_shared_prefix_saves_preserve_both_turns_without_duplicating_context(history_app):
    _, store = history_app
    repo = repository(store)
    identity = repo.create_conversation()
    common = [{'role': 'user', 'content': 'Start'}, {'role': 'assistant', 'content': 'Ready'}]
    branch_a = common + [{'role': 'user', 'content': 'First'}]
    branch_b = common + [{'role': 'user', 'content': 'Second'}]
    repo.save_conversation(identity, branch_a)
    repo.save_conversation(identity, branch_b)
    assert json.loads(repo.get_conversation(identity)['messages_json']) == branch_a + branch_b[len(common):]
    repo.save_conversation(identity, common)
    assert json.loads(repo.get_conversation(identity)['messages_json']) == branch_a + branch_b[len(common):]


def test_deleted_user_cannot_recreate_tutor_memory_or_conversation(history_app):
    _, store = history_app
    repo = repository(store)
    memory = repo.load_memory()
    identity = repo.create_conversation()
    store.database.users.delete_one({'id': 1})
    for write in (repo.load_memory, repo.create_conversation,
                  lambda: repo.save_profile(memory['id'], {}),
                  lambda: repo.rename_conversation(identity, 'Wrong')):
        with pytest.raises(LookupError):
            write()


def test_native_conversation_http_routes_and_guest_separation(history_app):
    app, store = history_app
    client = app.test_client()
    with client.session_transaction() as cookie:
        cookie['_user_id'] = '1'
        cookie['_fresh'] = True
    created = client.post('/api/tutor/conversations', json={'title': 'Native conversation'})
    assert created.status_code == 200
    identity = created.get_json()['id']
    assert client.get('/api/tutor/conversations').get_json()['conversations'][0]['id'] == identity
    assert client.get(f'/api/tutor/conversations/{identity}').get_json()['title'] == 'Native conversation'
    assert client.post(f'/api/tutor/conversations/{identity}/rename', json={'title': 'Renamed'}).get_json()['ok']
    guest = app.test_client()
    assert guest.get(f'/api/tutor/conversations/{identity}').status_code == 404
    assert guest.get('/api/tutor/conversations').get_json()['conversations'] == []
    assert client.delete(f'/api/tutor/conversations/{identity}').get_json()['ok']
    assert client.get(f'/api/tutor/conversations/{identity}').status_code == 404


def test_native_tutor_account_and_identity_helpers_do_not_require_sql(history_app):
    app, store = history_app
    from flask_login import login_user
    from intelliplan.persistence.accounts import accounts
    from intelliplan.persistence.identity import identities
    with app.test_request_context('/'):
        login_user(accounts().get(1))
        identities().save(1, {'grade_level': 'Grade 9', 'goals': 'Synthetic learning goal'})
        assert tutor._load_user_identity()['goals'] == 'Synthetic learning goal'
        assert tutor._check_and_increment_tutor_limit() == (True, 49, 50)
        assert 'sqlalchemy' not in app.extensions
