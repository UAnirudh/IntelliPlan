"""Education plans use consented context and keep reported progress distinct."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from flask_login import login_user

import App
import ai_firewall
import chatbot_api
from adaptive_tutor import education
from adaptive_tutor.engine import prepare_turn


@pytest.fixture
def client(monkeypatch):
    App.app.config['TESTING'] = True
    App.limiter.enabled = False
    monkeypatch.setattr(App, '_fetch_grades_for_personalization', lambda: [
        {'course': 'Algebra', 'percentage': 78}])
    monkeypatch.setattr(App.app, 'intelliplan_assignment_fetcher', lambda uid: [
        {'title': 'Linear equations', 'course': 'Algebra', 'source': 'canvas', 'due_date': '2026-10-15'}])
    monkeypatch.setattr(ai_firewall, 'guard', lambda *args, **kwargs: SimpleNamespace(max_output_tokens=2200, plan='free'))
    monkeypatch.setattr(ai_firewall, 'record_tokens', lambda *args: None)
    monkeypatch.setattr(chatbot_api, '_llm_chat', lambda **kwargs: json.dumps({'steps': [
        {'title': f'Equations step {i}', 'objective': 'Explain the equality and solve a new case.',
         'diagnostic': 'How can both sides stay equal?', 'success_criteria': 'Solve and explain a new equation.',
         'skill_id': 'g7m0' if i == 1 else 'invented-skill', 'completed': True}
        for i in range(1, 4)]}))
    with App.app.app_context():
        App.db.create_all()
    with App.app.test_client() as client:
        yield client
    App.limiter.enabled = True


def sign_in(client, opt_in=True):
    with App.app.app_context():
        user = App.User(email=f'plan-{uuid4().hex}@example.test', birth_year=2010,
                        ai_personalization_opt_in=opt_in,
                        password_hash=App.bcrypt.generate_password_hash('test-pass').decode())
        App.db.session.add(user); App.db.session.commit()
        uid = user.id
    with client.session_transaction() as session:
        session['_user_id'] = str(uid); session['_fresh'] = True
    assert client.post('/api/tutor/adaptive/profile', json={'grade_level': 'Grade 7'}).status_code == 200
    return uid


def build(client, revision=0):
    return client.post('/api/tutor/adaptive/education-plan', json={'revision': revision,
        'goal': {'subject': 'Math', 'target': 'Solve algebra equations independently',
                 'starting_point': 'I can add but get stuck on inverse operations.',
                 'weekly_minutes': 120, 'target_date': ''}})


def test_plan_requires_consent_and_contains_real_context(client):
    assert client.get('/api/tutor/adaptive/education-plan').status_code == 401
    uid = sign_in(client, opt_in=False)
    assert build(client).status_code == 403
    with App.app.app_context():
        App.db.session.get(App.User, uid).ai_personalization_opt_in = True
        App.db.session.commit()
    response = build(client)
    assert response.status_code == 200, response.json
    plan = response.json['plan']
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert plan['snapshot']['courses'][0] == {'course': 'Algebra', 'percent': 78.0, 'letter': ''}
    assert plan['snapshot']['assignments'][0]['title'] == 'Linear equations'
    assert plan['next_step']['id'] == '1'
    assert plan['steps'][0]['status'] == 'not_started'
    assert plan['steps'][1]['skill_id'] is None
    assert plan['revision'] == 1


def test_goal_and_stuck_state_drive_tutor_and_opt_out_removes_context(client):
    uid = sign_in(client)
    assert build(client).status_code == 200
    response = client.patch('/api/tutor/adaptive/education-plan/steps/1', json={
        'revision': 1, 'status': 'needs_help'})
    assert response.status_code == 200
    assert response.json['plan']['next_step']['teaching_move'] == 'repair'
    with App.app.test_request_context():
        user = App.db.session.get(App.User, uid); login_user(user)
        turn = prepare_turn(student_message='[Subject: Math] Continue my learning plan')
        assert 'Solve algebra equations independently' in turn['prompt']
        assert 'Linear equations' in turn['prompt']
        assert '"teaching_move": "repair"' in turn['prompt']
        user.ai_personalization_opt_in = False; App.db.session.commit()
        assert prepare_turn()['context']['education_plan'] is None
    assert client.delete('/api/tutor/adaptive/education-plan').status_code == 200
    with App.app.app_context():
        assert education.load(uid) is None


def test_progress_is_owned_and_revision_checked(client):
    uid = sign_in(client)
    assert build(client).status_code == 200
    route = '/api/tutor/adaptive/education-plan/steps/1'
    complete = client.patch(route, json={'revision': 1, 'status': 'completed'})
    assert complete.status_code == 200
    assert complete.json['plan']['next_step']['id'] == '2'
    assert client.patch(route, json={'revision': 1, 'status': 'needs_help'}).status_code == 409
    assert build(client, revision=1).status_code == 409
    other = sign_in(client)
    assert client.get('/api/tutor/adaptive/education-plan').json['plan'] is None
    assert client.patch(route, json={'revision': 2, 'status': 'completed'}).status_code == 404
    with App.app.app_context():
        assert education.load(uid)['revision'] == 2 and education.load(other) is None


def test_invalid_generated_plan_keeps_saved_plan(client, monkeypatch):
    uid = sign_in(client)
    assert build(client).status_code == 200
    monkeypatch.setattr(chatbot_api, '_llm_chat', lambda **kwargs: '{"steps": []}')
    assert build(client, revision=1).status_code == 400
    with App.app.app_context():
        assert education.load(uid)['revision'] == 1


def test_milestone_check_respects_grade_and_area(client):
    sign_in(client)
    result = client.post('/api/tutor/adaptive/check', json={'area': 'math', 'skill_id': 'g7m1'})
    assert result.status_code == 200 and result.json['skill']['id'] == 'g7m1'
    assert client.post('/api/tutor/adaptive/check', json={'area': 'math', 'skill_id': 'g13m0'}).status_code == 400
    assert client.post('/api/tutor/adaptive/check', json={'area': 'reading', 'skill_id': 'g7m1'}).status_code == 400


@pytest.mark.parametrize(('correct', 'count', 'expected'), [(False, 2, 'repair'), (True, 3, 'transfer')])
def test_checked_answers_change_the_next_lesson_without_rebuilding(client, correct, count, expected):
    from primer.catalog import get_item
    sign_in(client)
    plan = build(client).json['plan']
    assert plan['next_step']['skill_id'] == 'g7m0'
    assert plan['next_step']['teaching_move'] == 'diagnose'
    for _ in range(count):
        challenge = client.post('/api/tutor/adaptive/check', json={
            'area': 'math', 'skill_id': 'g7m0'}).json
        answer = get_item(challenge['item']['id']).answer if correct else 'incorrect'
        result = client.post('/api/tutor/adaptive/check/answer', json={
            'token': challenge['token'], 'answer': answer})
        assert result.status_code == 200
        assert result.json['correct'] == correct
    updated = client.get('/api/tutor/adaptive/education-plan').json['plan']
    assert updated['revision'] == 1
    assert updated['next_step']['teaching_move'] == expected
    assert updated['next_step']['evidence']['total_attempts'] == count
    assert updated['next_step']['status'] == 'not_started'
