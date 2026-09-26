"""Foundations is a bounded assessed path, scoped to the signed-in account."""

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin
from flask_sqlalchemy import SQLAlchemy

from primer.api import primer_bp
from primer.catalog import ITEM_BY_ID, normalize_answer
from primer import store


class User(UserMixin):
    def __init__(self, user_id):
        self.id = user_id


@pytest.fixture
def app():
    application = Flask(__name__)
    application.config.update(SECRET_KEY='primer-test-secret', SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', TESTING=True)
    SQLAlchemy(application)
    login = LoginManager(application)
    login.user_loader(lambda user_id: User(user_id))
    application.register_blueprint(primer_bp)
    yield application


def signed_in(app, user_id='1'):
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = user_id
        session['_fresh'] = True
    return client


def create(client, nickname='Sky'):
    response = client.post('/api/primer/learners', json={'nickname': nickname, 'world': 'space'})
    assert response.status_code == 201
    return response.get_json()['learner']['id']


def activity(client, learner_id):
    response = client.get(f'/api/primer/learners/{learner_id}/activity')
    assert response.status_code == 200
    body = response.get_json()
    assert 'answer' not in body['item']
    return body


def submit(client, learner_id, challenge, answer_text):
    return client.post(f'/api/primer/learners/{learner_id}/answer', json={
        'token': challenge['token'], 'answer': answer_text,
    })


def test_auth_ownership_and_deletion(app):
    anonymous = app.test_client()
    assert anonymous.get('/api/primer/learners').status_code == 401
    assert anonymous.post('/api/primer/learners', json={'nickname': 'X', 'world': 'forest'}).status_code == 401
    owner, other = signed_in(app, '1'), signed_in(app, '2')
    learner_id = create(owner)
    assert other.get(f'/api/primer/learners/{learner_id}/progress').status_code == 404
    assert other.get(f'/api/primer/learners/{learner_id}/activity').status_code == 404
    assert other.delete(f'/api/primer/learners/{learner_id}').status_code == 404
    assert owner.delete(f'/api/primer/learners/{learner_id}').status_code == 200
    assert owner.get(f'/api/primer/learners/{learner_id}/progress').status_code == 404


def test_server_grades_and_replay_is_rejected(app):
    client = signed_in(app)
    learner_id = create(client)
    challenge = activity(client, learner_id)
    item = ITEM_BY_ID[challenge['item']['id']]
    wrong = next(option for option in item.options if normalize_answer(option) != normalize_answer(item.answer))
    result = submit(client, learner_id, challenge, wrong)
    assert result.status_code == 200
    assert result.get_json()['correct'] is False
    assert result.get_json()['evidence']['estimate'] == pytest.approx(1 / 3, abs=.001)
    assert submit(client, learner_id, challenge, item.answer).status_code == 409
    progress = client.get(f'/api/primer/learners/{learner_id}/progress').get_json()
    assert progress['total_attempts'] == 1
    assert progress['skills'][0]['label'] == 'Practicing'
    assert 'answer' not in store.ATTEMPT.c.keys()
    assert client.get(f'/api/primer/learners/{learner_id}/progress').headers['Cache-Control'] == 'private, no-store'
    next_activity = activity(client, learner_id)
    assert next_activity['skill']['domain'] == 'Writing'
    assert submit(client, learner_id, next_activity, ITEM_BY_ID[next_activity['item']['id']].answer).status_code == 200
    assert activity(client, learner_id)['skill']['domain'] == 'Reading'


def test_challenges_are_scoped_and_tamper_protected(app):
    client = signed_in(app)
    first, second = create(client, 'One'), create(client, 'Two')
    challenge = activity(client, first)
    answer = ITEM_BY_ID[challenge['item']['id']].answer
    assert submit(client, second, challenge, answer).status_code == 400
    challenge['token'] += 'tampered'
    assert submit(client, first, challenge, answer).status_code == 400
    assert client.get(f'/api/primer/learners/{first}/progress').get_json()['total_attempts'] == 0


def test_all_domains_unlock_and_progress_with_evidence(app):
    client = signed_in(app)
    learner_id = create(client)
    seen = set()
    for _ in range(24):
        challenge = activity(client, learner_id)
        seen.add(challenge['skill']['domain'])
        answer = ITEM_BY_ID[challenge['item']['id']].answer
        result = submit(client, learner_id, challenge, answer)
        assert result.status_code == 200
        assert result.get_json()['correct'] is True
    assert seen == {'Reading', 'Writing', 'Arithmetic'}
    progress = client.get(f'/api/primer/learners/{learner_id}/progress').get_json()
    assert progress['total_attempts'] == 24
    assert all(row['unlocked'] for row in progress['skills'])
    assert any(row['label'] in {'Growing', 'Strong'} for row in progress['skills'])


def test_input_validation_does_not_store_child_answers(app):
    client = signed_in(app)
    assert client.post('/api/primer/learners', json={'nickname': '  ', 'world': 'space'}).status_code == 400
    assert client.post('/api/primer/learners', json={'nickname': 'Sky', 'world': 'unknown'}).status_code == 400
    learner_id = create(client)
    challenge = activity(client, learner_id)
    assert submit(client, learner_id, challenge, 'x' * 201).status_code == 400
    assert submit(client, learner_id, challenge, ' ').status_code == 400
    assert client.get(f'/api/primer/learners/{learner_id}/progress').get_json()['total_attempts'] == 0


def test_success_spaces_review_and_error_brings_it_back(app):
    with app.app_context():
        learner = store.create_learner(1, 'Sky', 'forest')
        learner_id = learner['id']
        first = store.record_attempt(learner_id, 'read_sounds', 'sound_m', 'nonce-1', True)
        second = store.record_attempt(learner_id, 'read_sounds', 'sound_s', 'nonce-2', True)
        third = store.record_attempt(learner_id, 'read_sounds', 'sound_m', 'nonce-3', True)
        assert (first['due_at'] - first['updated_at']).days == 1
        assert (second['due_at'] - second['updated_at']).days == 3
        assert (third['due_at'] - third['updated_at']).days == 7
        assert store.progress(learner_id)['skills'][0]['label'] == 'Growing'
        store.record_attempt(learner_id, 'read_sounds', 'sound_s', 'nonce-4', True)
        assert store.progress(learner_id)['skills'][0]['label'] == 'Strong'
        wrong = store.record_attempt(learner_id, 'read_sounds', 'sound_m', 'nonce-5', False)
        assert wrong['due_at'] == wrong['updated_at']
        assert wrong['streak'] == 0
