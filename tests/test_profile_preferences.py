"""Profile changes persist through the repository and retain their web contract."""
import uuid

import pytest

import App


@pytest.fixture
def client():
    App.app.config['TESTING'] = True
    enabled = App.limiter.enabled
    App.limiter.enabled = False
    with App.app.test_client() as client:
        yield client
    App.limiter.enabled = enabled


def sign_in(client):
    with App.app.app_context():
        user = App.account_repository().create(
            email=f'preferences-{uuid.uuid4().hex}@example.test', password_hash='synthetic-hash',
            attributes={'birth_year': 2000, 'parent_consent_granted': True})
        user_id = user.id
    with client.session_transaction() as cookie:
        cookie['_user_id'] = str(user_id)
        cookie['_fresh'] = True
        cookie['session_epoch'] = 0
    return user_id


def test_profile_changes_require_authentication(client):
    assert client.post('/api/profile/ai_personalization', json={'enabled': True}).status_code == 401
    assert client.post('/api/profile/phone', json={'phone': '2065550101'}).status_code == 401


def test_phone_normalization_clamps_and_removal_clear_sms_consent(client):
    user_id = sign_in(client)
    changed = client.post('/api/profile/phone', json={
        'phone': '(206) 555-0101', 'sms_reminders_opt_in': True,
        'push_reminders_opt_in': True, 'reminder_lead_minutes': 999999,
        'sms_carrier': 'unknown-carrier',
    })
    assert changed.status_code == 200
    assert changed.json['phone'] == '+12065550101'
    assert changed.json['sms_reminders_opt_in'] is True
    assert changed.json['reminder_lead_minutes'] == 10080
    assert changed.json['sms_carrier'] == 'tmobile'
    removed = client.post('/api/profile/phone', json={'phone': ''})
    assert removed.json['phone'] == ''
    assert removed.json['sms_reminders_opt_in'] is False
    assert removed.json['push_reminders_opt_in'] is True
    with App.app.app_context():
        assert App.account_repository().get(user_id).phone is None


def test_personalization_toggle_does_not_overwrite_other_preferences(client):
    user_id = sign_in(client)
    client.post('/api/profile/phone', json={'push_reminders_opt_in': True})
    for enabled in (True, False):
        response = client.post('/api/profile/ai_personalization', json={'enabled': enabled})
        assert response.status_code == 200
        assert response.json['ai_personalization_opt_in'] is enabled
    with App.app.app_context():
        user = App.account_repository().get(user_id)
        assert user.push_reminders_opt_in is True
        assert user.password_hash == 'synthetic-hash'
