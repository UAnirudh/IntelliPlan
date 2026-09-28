"""A tutor practice result is server-scored, account-owned, and replay safe."""

from datetime import datetime
from uuid import uuid4

import pytest

import App
from adaptive_tutor import checks, store
from adaptive_tutor.engine import build_dashboard, prepare_turn
from primer.catalog import get_item
from App import User, db, bcrypt


@pytest.fixture
def client():
    App.app.config['TESTING'] = True
    App.limiter.enabled = False
    with App.app.test_client() as test_client:
        with App.app.app_context():
            db.create_all()
        yield test_client
    App.limiter.enabled = True


def _sign_in(client, *, age=16, consent=False):
    email = f'tcheck+{uuid4().hex}@example.com'
    with App.app.app_context():
        user = User(email=email, password_hash=bcrypt.generate_password_hash('test-password').decode())
        user.birth_year = datetime.utcnow().year - age if age is not None else None
        user.parent_consent_granted = consent
        db.session.add(user)
        db.session.commit()
        uid = user.id
    with client.session_transaction() as session:
        session['_user_id'] = str(uid)
        session['_fresh'] = True
    return uid


def _grade(client, grade='Grade 7'):
    response = client.post('/api/tutor/adaptive/profile', json={'grade_level': grade})
    assert response.status_code == 200


@pytest.mark.parametrize(('raw', 'expected'), [
    ('Kindergarten', 0), ('k', 0), ('7th grade', 7), ('Grade 11', 11),
    ('12', 12), ('College freshman', 13), ('freshman', None), ('Grade 13', None),
])
def test_grade_parser_uses_an_explicit_k_to_college_level(raw, expected):
    assert checks.parse_grade(raw) == expected


def test_check_requires_signed_in_age_and_parent_consent(client):
    request = {'area': 'math'}
    assert client.post('/api/tutor/adaptive/check', json=request).status_code == 401
    uid = _sign_in(client, age=None)
    assert client.post('/api/tutor/adaptive/check', json=request).status_code == 403
    with App.app.app_context():
        user = db.session.get(User, uid)
        user.birth_year = datetime.utcnow().year - 10
        db.session.commit()
    assert client.post('/api/tutor/adaptive/check', json=request).status_code == 403
    with App.app.app_context():
        user = db.session.get(User, uid)
        user.parent_consent_granted = True
        db.session.commit()
    _grade(client, 'Grade 3')
    assert client.post('/api/tutor/adaptive/check', json=request).status_code == 200


def test_scored_check_updates_tutor_from_independent_answer_only(client):
    uid = _sign_in(client)
    _grade(client, 'Grade 7')
    challenge = client.post('/api/tutor/adaptive/check', json={'area': 'math'})
    assert challenge.status_code == 200, challenge.json
    assert challenge.json['skill']['grade'] == 7
    assert 'answer' not in challenge.json['item']
    item = get_item(challenge.json['item']['id'])
    reply = client.post('/api/tutor/adaptive/check/answer', json={
        'token': challenge.json['token'], 'answer': item.answer})
    assert reply.status_code == 200, reply.json
    assert reply.json['correct'] is True and reply.json['assisted'] is False
    assert client.post('/api/tutor/adaptive/check/answer', json={
        'token': challenge.json['token'], 'answer': item.answer}).status_code == 409
    with App.app.test_request_context():
        # Bind the signed-in owner while checking the dashboard and tutor path.
        from flask_login import login_user
        login_user(db.session.get(User, uid))
        dashboard = build_dashboard()
        turn = prepare_turn(student_message='[Subject: Math]\nExplain the next step')
    row = next(row for row in dashboard['mastery'] if row['topic'] == challenge.json['skill']['title'])
    assert row['source'] == 'scored_check'
    assert row['independent_correct'] == 1 and row['total_attempts'] == 1
    assert '1 independent correct of 1 checked attempts' in turn['prompt']
    with App.app.app_context():
        stored = db.session.execute(checks.ATTEMPT.select().where(checks.ATTEMPT.c.owner_id == uid)).mappings().all()
    assert len(stored) == 1 and 'answer' not in stored[0]
    assert store.list_mastery(store.get_or_create_profile()['id']) == []


def test_hint_marks_answer_assisted_and_wrong_answer_prompts_repair(client):
    _sign_in(client)
    _grade(client, 'Grade 9')
    challenge = client.post('/api/tutor/adaptive/check', json={'area': 'math'}).json
    hint = client.post('/api/tutor/adaptive/check/hint', json={'token': challenge['token']})
    assert hint.status_code == 200 and hint.json['hint']
    item = get_item(challenge['item']['id'])
    answer = client.post('/api/tutor/adaptive/check/answer', json={
        'token': challenge['token'], 'answer': item.answer})
    assert answer.status_code == 200 and answer.json['assisted'] is True
    assert answer.json['practice']['independent_correct'] == 0
    assert client.post('/api/tutor/adaptive/check/hint', json={
        'token': challenge['token']}).status_code == 409
    second = client.post('/api/tutor/adaptive/check', json={'area': 'math'}).json
    bad = client.post('/api/tutor/adaptive/check/answer', json={
        'token': second['token'], 'answer': 'definitely incorrect'})
    assert bad.status_code == 200 and bad.json['correct'] is False
    next_check = client.post('/api/tutor/adaptive/check', json={'area': 'math'}).json
    assert next_check['mode'] == 'repair'
    assert next_check['skill']['id'] == second['skill']['id']
    assert next_check['item']['id'] != second['item']['id']


def test_check_token_is_bound_to_owner_and_grade(client):
    _sign_in(client)
    _grade(client, 'Grade 8')
    challenge = client.post('/api/tutor/adaptive/check', json={'area': 'reading'}).json
    _grade(client, 'Grade 9')
    assert client.post('/api/tutor/adaptive/check/answer', json={
        'token': challenge['token'], 'answer': 'anything'}).status_code == 409
    _sign_in(client)
    _grade(client, 'Grade 8')
    assert client.post('/api/tutor/adaptive/check/hint', json={
        'token': challenge['token']}).status_code == 409


def test_two_misses_offer_a_prior_grade_bridge(client):
    _sign_in(client)
    _grade(client, 'Grade 8')
    first = client.post('/api/tutor/adaptive/check', json={'area': 'math'}).json
    assert client.post('/api/tutor/adaptive/check/answer', json={
        'token': first['token'], 'answer': 'not the answer'}).status_code == 200
    repair = client.post('/api/tutor/adaptive/check', json={'area': 'math'}).json
    assert repair['mode'] == 'repair'
    assert client.post('/api/tutor/adaptive/check/answer', json={
        'token': repair['token'], 'answer': 'not the answer'}).status_code == 200
    bridge = client.post('/api/tutor/adaptive/check', json={'area': 'math'}).json
    assert bridge['mode'] == 'bridge'
    assert bridge['skill']['grade'] == 7


def test_college_check_uses_college_foundation_bank(client):
    _sign_in(client, age=19)
    _grade(client, 'College')
    response = client.post('/api/tutor/adaptive/check', json={'area': 'writing'})
    assert response.status_code == 200
    assert response.json['skill']['grade'] == 13
    assert response.json['item']['id'].startswith('v2g13w')


def test_two_preissued_tokens_cannot_count_the_same_item_twice(client):
    _sign_in(client)
    _grade(client, 'Grade 6')
    first = client.post('/api/tutor/adaptive/check', json={'area': 'reading'}).json
    second = client.post('/api/tutor/adaptive/check', json={'area': 'reading'}).json
    assert first['item']['id'] == second['item']['id']
    assert first['token'] != second['token']
    # A hint applies to this check step even if the page requested a second token.
    assert client.post('/api/tutor/adaptive/check/hint', json={
        'token': first['token']}).status_code == 200
    item = get_item(second['item']['id'])
    credited = client.post('/api/tutor/adaptive/check/answer', json={
        'token': second['token'], 'answer': item.answer})
    assert credited.status_code == 200 and credited.json['assisted'] is True
    duplicate = client.post('/api/tutor/adaptive/check/answer', json={
        'token': first['token'], 'answer': item.answer})
    assert duplicate.status_code == 409
