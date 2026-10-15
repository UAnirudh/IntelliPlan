"""Real private signaling requests against disposable app storage; no media."""
from datetime import timedelta
from uuid import uuid4

from cryptography.fernet import Fernet, MultiFernet
import pytest

import App
import secret_box
from intelliplan.learning import policy
from time_utils import utcnow

OFFER = 'v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=recvonly\r\n'
ANSWER = OFFER.replace('recvonly', 'sendonly')
BASE = '/api/active/camera-pairs'


@pytest.fixture
def pair_owner(monkeypatch):
    previous = App.limiter.enabled
    App.limiter.enabled = False
    monkeypatch.setattr(secret_box, '_fernet', MultiFernet([Fernet(Fernet.generate_key())]))
    monkeypatch.setattr(secret_box, '_looked_up', True)
    with App.app.app_context():
        App.db.create_all()
        user = App.User(email=f'camera-pair-{uuid4().hex}@example.test', birth_year=2000, password_hash='x')
        App.db.session.add(user)
        App.db.session.commit()
        uid = user.id
        App._get_or_create_identity(uid).completed = True
        App.db.session.commit()
    def client(owner=uid):
        c = App.app.test_client()
        with c.session_transaction() as s:
            s['_user_id'] = str(owner)
            s['_fresh'] = True
        return c
    yield uid, client, client()
    with App.app.app_context():
        App.StudyCameraPair.query.filter_by(user_id=uid).delete()
        membership = policy.models()['Membership']
        orgs = [m.organization_id for m in membership.query.filter_by(user_id=uid)]
        membership.query.filter_by(user_id=uid).delete()
        if orgs:
            policy.models()['Organization'].query.filter(
                policy.models()['Organization'].id.in_(orgs)).delete(synchronize_session=False)
        App.UserIdentity.query.filter_by(user_id=uid).delete()
        App.User.query.filter_by(id=uid).delete()
        App.db.session.commit()
    App.limiter.enabled = previous


def create(client):
    response = client.post(BASE, json={'share_video': True})
    assert response.status_code == 201, response.get_json()
    assert response.headers['Cache-Control'] == 'no-store'
    return response.get_json()


def call(client, pair, role='receiver', key=None, **body):
    return client.post(BASE + '/' + pair['id'] + '/sync',
                       headers={'X-Camera-Key': key or pair[role + '_key']}, json={'role': role, **body})


def claim(client, pair):
    return client.post(BASE + '/' + pair['id'] + '/claim', json={'share_video': True},
                       headers={'X-Camera-Key': pair['invite_key']})


def test_video_only_exchange_is_encrypted_and_erased_after_connection(pair_owner):
    uid, factory, receiver = pair_owner
    phone = factory()
    pair = create(receiver)
    assert call(receiver, pair, description={'type': 'offer', 'sdp': OFFER}).status_code == 200
    with App.app.app_context():
        row = App.db.session.get(App.StudyCameraPair, pair['id'])
        assert secret_box.is_encrypted(row.offer) and OFFER not in row.offer
        assert row.invite_hash != pair['invite_key'] and row.receiver_hash != pair['receiver_key']
    claimed = claim(phone, pair)
    assert claimed.status_code == 200
    pair.update(claimed.get_json())
    assert claim(phone, pair).status_code == 404
    assert call(phone, pair, 'sender').get_json()['description']['sdp'] == OFFER
    assert call(phone, pair, 'sender', description={'type': 'answer', 'sdp': ANSWER}).status_code == 200
    assert call(receiver, pair).get_json()['description']['sdp'] == ANSWER
    assert call(receiver, pair, connected=True).status_code == 200
    with App.app.app_context():
        row = App.db.session.get(App.StudyCameraPair, pair['id'])
        assert row.offer is None and row.answer is None and row.connected


def test_only_same_owner_and_correct_role_key_can_use_pair(pair_owner):
    uid, factory, client = pair_owner
    pair = create(client)
    assert App.app.test_client().post(BASE, json={'share_video': True}).status_code == 401
    assert call(client, pair, key=pair['invite_key']).status_code == 404
    with App.app.app_context():
        other = App.User(email=f'other-pair-{uuid4().hex}@example.test', birth_year=2000, password_hash='x')
        App.db.session.add(other)
        App.db.session.commit()
        oid = other.id
        App._get_or_create_identity(oid).completed = True
        App.db.session.commit()
    try:
        assert call(factory(oid), pair).status_code == 404
        assert claim(factory(oid), pair).status_code == 404
    finally:
        with App.app.app_context():
            App.UserIdentity.query.filter_by(user_id=oid).delete()
            App.User.query.filter_by(id=oid).delete()
            App.db.session.commit()


@pytest.mark.parametrize('field', ['expires_at', 'receiver_seen_at', 'sender_seen_at'])
def test_expiry_or_lost_device_lease_revokes_pair(pair_owner, field):
    _, _, client = pair_owner
    pair = create(client)
    with App.app.app_context():
        row = App.db.session.get(App.StudyCameraPair, pair['id'])
        row.connected = True
        setattr(row, field, utcnow() - timedelta(seconds=31))
        App.db.session.commit()
    assert call(client, pair).status_code == 410
    with App.app.app_context():
        assert App.db.session.get(App.StudyCameraPair, pair['id']) is None


@pytest.mark.parametrize('description', [
    {'type': 'answer', 'sdp': ANSWER}, {'type': 'offer', 'sdp': OFFER + 'm=audio 9 RTP 0\r\n'},
    {'type': 'offer', 'sdp': OFFER + 'm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n'},
    {'type': 'offer', 'sdp': 'x' * 50000}, {'type': 'offer', 'sdp': 3}, 'invalid',
])
def test_rejects_other_roles_audio_data_or_oversized_payload(pair_owner, description):
    _, _, client = pair_owner
    assert call(client, create(client), description=description).status_code == 400


def test_creation_requires_explicit_opt_in_and_valid_encryption(pair_owner, monkeypatch):
    _, _, client = pair_owner
    assert client.post(BASE, json={}).status_code == 400
    monkeypatch.setattr(secret_box, 'is_enabled', lambda: False)
    assert client.post(BASE, json={'share_video': True}).status_code == 503


def test_replacement_and_stop_revoke_old_keys(pair_owner):
    uid, _, client = pair_owner
    old = create(client)
    new = create(client)
    assert call(client, old).status_code == 404
    with App.app.app_context():
        assert App.StudyCameraPair.query.filter_by(user_id=uid).count() == 1
    result = client.post(BASE + '/' + new['id'] + '/stop', json={'role': 'receiver'},
                         headers={'X-Camera-Key': new['receiver_key']})
    assert result.get_json() == {'stopped': True}
    assert call(client, new).status_code == 404


def test_school_revocation_erases_connection_and_cross_site_write_is_denied(pair_owner):
    uid, _, client = pair_owner
    pair = create(client)
    assert client.post(BASE, json={'share_video': True},
                       headers={'Origin': 'https://unrelated.example'}).status_code == 403
    with App.app.app_context():
        org = policy.models()['Organization'](name='Pairing school', kind='school', policy={'focus': False})
        App.db.session.add(org)
        App.db.session.flush()
        App.db.session.add(policy.models()['Membership'](user_id=uid, organization_id=org.id, role='student'))
        App.db.session.commit()
    assert call(client, pair).status_code == 403
    with App.app.app_context():
        assert App.db.session.get(App.StudyCameraPair, pair['id']) is None


def test_phone_landing_is_private_and_contains_no_third_party_script(pair_owner):
    _, _, client = pair_owner
    response = client.get('/study-camera')
    assert response.status_code == 200
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    assert response.headers['Cache-Control'] == 'no-store'
    assert 'text/javascript' not in response.get_data(as_text=True)
    assert 'https://' not in response.get_data(as_text=True)
    assert 'noindex,nofollow' in response.get_data(as_text=True)


def test_connected_flag_cannot_extend_an_unused_invitation(pair_owner):
    _, _, client = pair_owner
    pair = create(client)
    assert call(client, pair, connected=True).status_code == 409
    with App.app.app_context():
        row = App.db.session.get(App.StudyCameraPair, pair['id'])
        row.created_at = utcnow() - timedelta(seconds=121)
        # Even a corrupted or legacy extended expiry cannot extend the invite.
        row.expires_at = utcnow() + timedelta(hours=1)
        App.db.session.commit()
    assert claim(client, pair).status_code == 410


def test_policy_revocation_while_waiting_for_pair_lock_is_rechecked(pair_owner, monkeypatch):
    import study_camera_glue
    from flask_sqlalchemy.query import Query
    _, _, client = pair_owner
    pair = create(client)
    original = Query.with_for_update
    def lock(query, *args, **kwargs):
        if query.column_descriptions[0].get('entity') is App.StudyCameraPair:
            monkeypatch.setattr(study_camera_glue, '_focus_allowed', lambda: False)
        return original(query, *args, **kwargs)
    monkeypatch.setattr(Query, 'with_for_update', lock)
    assert call(client, pair).status_code == 403
    with App.app.app_context():
        assert App.db.session.get(App.StudyCameraPair, pair['id']) is None


def test_expiry_clock_is_read_after_row_lock(pair_owner, monkeypatch):
    import time_utils
    from datetime import datetime, timezone
    from flask_sqlalchemy.query import Query
    _, _, client = pair_owner
    clock = [datetime(2026, 10, 15, 8)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].replace(tzinfo=timezone.utc) if tz else clock[0]
    monkeypatch.setattr(time_utils, 'datetime', Clock)
    pair = create(client)
    original = Query.first
    def first(query):
        result = original(query)
        if query.column_descriptions[0].get('entity') is App.StudyCameraPair:
            clock[0] += timedelta(seconds=121)
        return result
    monkeypatch.setattr(Query, 'first', first)
    assert call(client, pair).status_code == 410


def test_revocation_before_decrypted_description_is_returned_discards_it(pair_owner, monkeypatch):
    import study_camera_glue
    _, _, client = pair_owner
    pair = create(client)
    call(client, pair, description={'type': 'offer', 'sdp': OFFER})
    pair.update(claim(client, pair).get_json())
    original = secret_box.decrypt
    def decrypt(value):
        result = original(value)
        monkeypatch.setattr(study_camera_glue, '_focus_allowed', lambda: False)
        return result
    monkeypatch.setattr(secret_box, 'decrypt', decrypt)
    result = call(client, pair, 'sender')
    assert result.status_code == 403
    assert 'sdp' not in result.get_data(as_text=True)


def test_maintenance_removes_abandoned_rows_without_publishing_or_sending(pair_owner, monkeypatch):
    import notifications_glue
    from intelliplan.services.camera_pairs import cleanup_camera_pairs
    _, _, client = pair_owner
    pair = create(client)
    call(client, pair, description={'type': 'offer', 'sdp': OFFER})
    with App.app.app_context():
        row = App.db.session.get(App.StudyCameraPair, pair['id'])
        row.receiver_seen_at = utcnow() - timedelta(seconds=31)
        App.db.session.commit()
        assert cleanup_camera_pairs() == 1
        assert App.db.session.get(App.StudyCameraPair, pair['id']) is None
    assert client.post('/cron/study-cameras').status_code == 401
    monkeypatch.setattr(notifications_glue, '_cron_authorised', lambda: True)
    assert client.post('/cron/study-cameras').get_json() == {'removed': 0}


def test_signaling_polling_has_its_own_account_limit_not_the_default_fifty_per_hour(pair_owner):
    _, _, client = pair_owner
    pair = create(client)
    App.limiter.enabled = True
    try:
        for _ in range(55):
            response = call(client, pair)
            assert response.status_code == 200, response.get_data(as_text=True)
    finally:
        App.limiter.enabled = False
