"""Known-child and missing-age denials must apply to every credential family."""
import uuid
import hashlib
from datetime import datetime
from types import SimpleNamespace

import pytest

import App
import chatbot_api
import child_privacy
from auth_api import make_token


@pytest.fixture
def client():
    App.app.config['TESTING'] = True
    enabled = App.limiter.enabled
    App.limiter.enabled = False
    with App.app.app_context():
        App.db.create_all()
    with App.app.test_client() as client:
        yield client
    App.limiter.enabled = enabled


def account(year=None, legacy_approval=False):
    with App.app.app_context():
        user = App.User(email=f'childcheck-{uuid.uuid4().hex}@example.test',
            password_hash=App.bcrypt.generate_password_hash('sample-password').decode(),
            birth_year=year, parent_consent_granted=legacy_approval)
        App.db.session.add(user)
        App.db.session.commit()
        return user.id, user.email


@pytest.mark.parametrize('value', [None, '', True, [], 2014.5, '2014.5', '1899', '9999', '٢٠٠٠'])
def test_invalid_or_missing_year_cannot_register(value):
    with pytest.raises(ValueError):
        child_privacy.registration_birth_year(value, datetime(2026, 11, 13))


def test_conservative_thirteenth_birthday_boundary():
    now = datetime(2026, 11, 13)
    with pytest.raises(ValueError):
        child_privacy.registration_birth_year(2013, now)
    assert child_privacy.registration_birth_year(2012, now) == 2012
    assert child_privacy.hold_reason(SimpleNamespace(birth_year=2015,
        parent_consent_granted=True), now) == 'child_verification_required'


def test_parent_consent_tokens_are_hashed_without_confusing_hex_tokens():
    token = 'a' * 64
    digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
    assert child_privacy.parent_consent_token_hash(token) == digest
    assert child_privacy.parent_consent_token_values(token) == (digest, token)


@pytest.mark.parametrize('path', ['/api/auth/register', '/api/ext/register', '/extension/register'])
@pytest.mark.parametrize('year', [None, datetime.utcnow().year - 9])
def test_api_registration_denies_before_storing_or_issuing_token(client, path, year):
    email = f'nocreate-{uuid.uuid4().hex}@example.test'
    payload = {'email': email, 'password': 'sample-password', 'birth_year': year}
    response = client.post(path, json=payload)
    assert response.status_code == 400
    assert 'token' not in response.get_json()
    with App.app.app_context():
        assert App.User.query.filter_by(email=email).first() is None


def test_web_signup_rejects_child_without_storing_parent_details(client, monkeypatch):
    calls = []
    monkeypatch.setattr(App, '_send_email', lambda *a, **k: calls.append(a))
    email = f'nocreate-{uuid.uuid4().hex}@example.test'
    response = client.post('/register', data={'email': email, 'password': 'sample-password',
        'confirm_password': 'sample-password', 'birth_year': datetime.utcnow().year - 9,
        'parent_email': 'guardian@example.test'})
    assert response.status_code == 200
    assert b'currently unavailable' in response.data or b'Independent accounts' in response.data
    assert calls == []
    with App.app.app_context():
        assert App.User.query.filter_by(email=email).first() is None


@pytest.mark.parametrize('path', ['/api/auth/login', '/api/ext/login', '/extension/login', '/api/v1/auth/token'])
@pytest.mark.parametrize('year', [None, datetime.utcnow().year - 9])
def test_child_login_is_held_and_unknown_age_login_is_recovery_only(client, path, year):
    _, email = account(year, legacy_approval=True)
    response = client.post(path, json={'email': email, 'password': 'sample-password'})
    if year is None and path in {'/api/v1/auth/token', '/api/auth/login', '/api/ext/login'}:
        assert response.status_code == 200
        token = response.get_json()['token']
        assert client.get('/api/v1/tasks', headers={'Authorization': 'Bearer ' + token}).status_code == 403
        return
    assert response.status_code == 403
    assert 'token' not in response.get_json()


def test_existing_bearer_and_extension_token_cannot_bypass_child_hold(client):
    uid, _ = account(datetime.utcnow().year - 9, legacy_approval=True)
    with App.app.app_context():
        token = make_token(App.db.session.get(App.User, uid))
        App.db.session.add(App.ExtensionToken(user_id=uid, token='test-' + str(uid)))
        App.db.session.commit()
    response = client.get('/api/v1/me', headers={'Authorization': 'Bearer ' + token})
    assert response.status_code == 200  # Minimal recovery status, no coursework.
    assert response.get_json()['age_consent_status'] == 'parent_consent_pending'
    assert client.get('/api/v1/tasks', headers={'Authorization': 'Bearer ' + token}).status_code == 403
    assert client.get('/api/auth/me', headers={'Authorization': 'Bearer ' + token}).status_code == 403
    with App.app.app_context():
        assert App.get_extension_user('test-' + str(uid)) is None


def test_old_cookie_approval_cannot_unlock_private_features(client):
    uid, _ = account(datetime.utcnow().year - 9, legacy_approval=True)
    with client.session_transaction() as session:
        session['_user_id'] = str(uid)
        session['_fresh'] = True
    assert client.post('/api/syllabus/import', json={'text': 'synthetic'}).status_code == 403
    assert client.get('/legal').status_code == 200


@pytest.mark.parametrize('path,payload', [
    ('/api/tutor', {'message': 'synthetic question'}),
    ('/api/chatbot', {'message': 'synthetic question'}),
    ('/api/tutor/vision', {'question': 'synthetic question', 'image': 'AA=='}),
    ('/api/tutor/voice', {'audio': 'AA=='}),
])
@pytest.mark.parametrize('birth_year', [None, datetime.utcnow().year - 10])
def test_unknown_age_or_pending_child_is_blocked_before_ai_routes(
        client, monkeypatch, path, payload, birth_year):
    uid, _ = account(birth_year)
    with client.session_transaction() as session:
        session['_user_id'] = str(uid)
        session['_fresh'] = True
    provider_calls = []
    monkeypatch.setattr(chatbot_api.ai_firewall, 'guard',
                        lambda *args, **kwargs: provider_calls.append('firewall'))
    response = client.post(path, json=payload)
    assert response.status_code == 403
    body = response.get_json()
    assert body.get('error', body.get('reason')) in {'age_required', 'parent_consent_required'}
    assert provider_calls == []


@pytest.mark.parametrize('path,payload', [
    ('/api/tutor', {'message': 'synthetic question'}),
    ('/api/tutor/vision', {'question': 'synthetic question', 'image': 'AA=='}),
    ('/api/import/smart_paste', {'text': 'synthetic assignment list'}),
])
def test_guest_cannot_reach_tutor_provider_routes(client, path, payload):
    response = client.post(path, json=payload)
    assert response.status_code == 401
    assert response.get_json()['error'] == 'authentication_required'


def test_unknown_age_provider_policy_fails_closed():
    from intelliplan.learning import policy
    user = SimpleNamespace(birth_year=None, parent_consent_granted=False, parent_email=None)
    assert policy.permitted_providers(user) == set()


def test_ai_request_gate_excludes_deterministic_study_and_group_updates():
    from intelliplan.learning.enforcement import is_ai_request
    assert not is_ai_request('/study/points/update', 'POST')
    assert not is_ai_request('/study/shop/buy', 'POST')
    assert not is_ai_request('/api/groups/12/join', 'POST')
    assert is_ai_request('/api/groups', 'POST')
    assert is_ai_request('/api/tutor', 'POST')
    assert not is_ai_request('/api/tutor', 'GET')


def test_unknown_age_can_still_use_deterministic_study_updates(client):
    uid, _ = account(None)
    with client.session_transaction() as session:
        session['_user_id'] = str(uid)
        session['_fresh'] = True
    response = client.post('/study/points/update', json={'delta': 1})
    assert response.status_code == 200
    assert response.get_json()['status'] == 'ok'


def test_existing_api_key_cannot_bypass_known_child_hold(client):
    import api_keys
    uid, _ = account(datetime.utcnow().year - 9, legacy_approval=True)
    raw = 'ip_live_synthetic_' + uuid.uuid4().hex
    with App.app.app_context():
        App.db.session.add(App.ApiKey(user_id=uid, app_name='Synthetic privacy test',
            scopes='read:profile', key_hash=api_keys.hash_key(raw), status='active'))
        App.db.session.commit()
    response = client.get('/api/v1/me', headers={'X-API-Key': raw})
    assert response.status_code == 403
    assert response.get_json().get('email') is None


def test_child_background_provider_and_reminder_denied():
    from intelliplan.email.eligibility import is_reminder_eligible
    from intelliplan.learning import policy
    user = SimpleNamespace(birth_year=datetime.utcnow().year - 9,
        parent_consent_granted=True, email='child@example.test')
    assert policy.permitted_providers(user) == set()
    assert is_reminder_eligible(user) == (False, 'under_13_verification_unavailable')


def test_eligible_api_signup_stores_age_and_can_sign_in(client):
    email = f'eligible-{uuid.uuid4().hex}@example.test'
    response = client.post('/api/auth/register', json={'email': email,
        'password': 'sample-password', 'birth_year': 2000})
    assert response.status_code == 201
    assert response.get_json()['token']
    assert client.post('/api/auth/login', json={'email': email,
        'password': 'sample-password'}).status_code == 200


def test_contact_details_and_restricted_access_notice_are_rendered(client):
    page = client.get('/legal').get_data(as_text=True)
    assert '425-286-3759' in page
    assert 'legacy email-approval flag' in page
