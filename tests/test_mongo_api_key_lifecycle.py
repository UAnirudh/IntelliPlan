"""Native developer-key workflows without importing a SQL application."""
from datetime import datetime, timedelta, timezone

from flask import Flask
from flask_login import LoginManager
import mongomock
import pytest

import api_keys
from intelliplan_api import api_bp
from intelliplan.persistence.mongo import MongoStore
from intelliplan.persistence.mongo_accounts import MongoAccounts
from intelliplan.persistence.mongo_api_keys import KeySlotsFull, MongoAPIKeys


APPLICATION = {'app_name': 'Synthetic study widget', 'app_url': 'https://example.invalid',
    'use_case': 'A synthetic widget that displays assignments in a local test.',
    'expected_volume': 'low', 'accepted_terms': True, 'scopes': ['read:profile']}


@pytest.fixture
def environment():
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-native-key-test', ADMIN_EMAILS=['reviewer@example.invalid'])
    client = mongomock.MongoClient()
    store = MongoStore('mongodb://test', 'api-keys', client_factory=lambda *a, **k: client)
    store.transaction = lambda work: work(store.database, None)
    created = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    store.database.users.insert_many([
        {'id': 1, 'email': 'student@example.invalid', 'created_at': created},
        {'id': 2, 'email': 'admin@example.invalid', 'created_at': created},
    ])
    repo = MongoAPIKeys(store)
    repo.ensure_indexes()
    app.extensions['mongo_store'] = store
    app.extensions['admin_checker'] = lambda user: user.id == 2
    delivered = []
    def sender(recipient, subject, body):
        delivered.append((recipient, subject, body))
        return True
    app.extensions['email_sender'] = sender
    login = LoginManager(app)
    login.user_loader(MongoAccounts(store).get)
    app.register_blueprint(api_keys.developer_api_bp)
    app.register_blueprint(api_bp)
    return app, store, repo, delivered


def sign_in(app, identity=1):
    client = app.test_client()
    with client.session_transaction() as cookie:
        cookie['_user_id'] = str(identity)
        cookie['_fresh'] = True
    return client


def test_native_read_only_application_rotation_revocation_and_safe_views(environment):
    app, store, repo, delivered = environment
    client = sign_in(app)
    approved = client.post('/developers/apply', json=APPLICATION)
    assert approved.status_code == 201
    payload = approved.get_json()
    secret = payload['key']
    identity = payload['application']['id']
    assert 'key_hash' not in payload['application']
    with app.app_context():
        assert api_keys.resolve_api_key(secret).user_id == 1
    api_client = app.test_client()
    headers = {'X-API-Key': secret}
    assert api_client.get('/api/v1/me', headers=headers).get_json()['credential']['type'] == 'api_key'
    assert api_client.get('/api/v1/identity', headers=headers).status_code == 403
    assert api_client.post('/api/v1/link/session', json={'provider': 'settings'}, headers=headers).status_code == 403
    assert client.get('/developers/applications').get_json()['slots_remaining'] == 4
    assert store.database.api_keys.find_one({'id': identity})['key_hash'] != secret
    rolled = client.post(f'/developers/keys/{identity}/roll').get_json()
    assert repo.resolve_digest(api_keys.hash_key(secret)) is None
    assert repo.resolve_digest(api_keys.hash_key(rolled['key'])) is not None
    assert sign_in(app, 2).post(f'/developers/keys/{identity}/revoke').status_code == 404
    assert client.post(f'/developers/keys/{identity}/revoke').get_json()['application']['status'] == 'revoked'
    assert repo.resolve_digest(api_keys.hash_key(rolled['key'])) is None
    assert client.post(f'/developers/keys/{identity}/roll').status_code == 409
    assert delivered[0][0] == 'student@example.invalid'
    assert 'sqlalchemy' not in app.extensions


def test_native_write_review_is_admin_only_and_secret_is_sent_only_to_applicant(environment):
    app, store, repo, delivered = environment
    student, admin = sign_in(app), sign_in(app, 2)
    pending = student.post('/developers/apply', json={**APPLICATION, 'scopes': ['read:profile', 'write:tasks'], 'expected_volume': 'high'})
    assert pending.status_code == 202
    identity = pending.get_json()['application']['id']
    assert student.get('/developers/admin/applications').status_code == 403
    assert student.post(f'/developers/admin/applications/{identity}/decide', json={'decision': 'approve'}).status_code == 403
    assert admin.get('/developers/admin/applications').get_json()['applications'][0]['owner']['email'] == 'student@example.invalid'
    result = admin.post(f'/developers/admin/applications/{identity}/decide', json={'decision': 'approve', 'note': 'Reviewed synthetic application'})
    assert result.status_code == 200
    assert result.get_json()['emailed_secret'] is True
    assert 'key' not in result.get_json()
    assert result.get_json()['application']['rate_limit_per_min'] == 1000
    assert delivered[-1][0] == 'student@example.invalid'
    assert 'ip_live_' in delivered[-1][2]
    assert admin.post(f'/developers/admin/applications/{identity}/decide', json={'decision': 'approve'}).status_code == 409
    assert repo.get(identity).status == 'active'


def test_native_age_and_slots_are_checked_against_the_saved_owner(environment):
    app, store, repo, delivered = environment
    store.database.users.update_one({'id': 1}, {'$set': {'created_at': datetime.utcnow().isoformat()}})
    client = sign_in(app)
    for _ in range(5):
        assert client.post('/developers/apply', json=APPLICATION).status_code == 202
    assert client.post('/developers/apply', json=APPLICATION).status_code == 409
    assert repo.list(user_id=1)[0].status == 'pending'
    with pytest.raises(KeySlotsFull):
        repo.submit(1, {'app_name': 'Synthetic', 'scopes': 'read:profile'})
    with pytest.raises(ValueError):
        repo.submit(2, {'app_name': 'Synthetic', 'scopes': 'admin:everything'})
    store.database.users.delete_one({'id': 1})
    with pytest.raises(LookupError):
        repo.submit(1, {'app_name': 'Synthetic', 'scopes': 'read:profile'})


def test_native_approval_reports_unavailable_delivery_and_denial_is_retained(environment):
    app, store, repo, delivered = environment
    student, admin = sign_in(app), sign_in(app, 2)
    first = student.post('/developers/apply', json={**APPLICATION, 'scopes': ['write:tasks']}).get_json()['application']['id']
    app.extensions.pop('email_sender')
    approved = admin.post(f'/developers/admin/applications/{first}/decide', json={'decision': 'approve'})
    assert approved.get_json()['emailed_secret'] is False
    second = student.post('/developers/apply', json={**APPLICATION, 'scopes': ['write:tasks']}).get_json()['application']['id']
    denied = admin.post(f'/developers/admin/applications/{second}/decide', json={'decision': 'deny', 'note': 'Narrow the scope'})
    assert denied.get_json()['application']['status'] == 'denied'
    assert repo.get(second).review_note == 'Narrow the scope'
    assert repo.get(second).key_hash == ''


def test_native_admin_access_fails_closed_without_a_checker(environment):
    app, *_ = environment
    app.extensions.pop('admin_checker')
    assert sign_in(app, 2).get('/developers/admin/applications').status_code == 403
