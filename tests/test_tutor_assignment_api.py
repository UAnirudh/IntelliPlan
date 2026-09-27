"""Exercise the schoolwork consent boundary and the actual tutor prompt path."""

from types import SimpleNamespace

import pytest

import App
import assignment_materials
import chatbot_api
from App import LinkedAccount, User, bcrypt, db


@pytest.fixture
def client():
    App.app.config['TESTING'] = True
    App.limiter.enabled = False
    with App.app.test_client() as c:
        with App.app.app_context():
            db.create_all()
            _wipe()
        yield c
        with App.app.app_context():
            _wipe()
    App.limiter.enabled = True


def _wipe():
    ids = [u.id for u in User.query.filter(User.email.like('tam+%')).all()]
    if ids:
        LinkedAccount.query.filter(LinkedAccount.user_id.in_(ids)).delete(synchronize_session=False)
    User.query.filter(User.email.like('tam+%')).delete(synchronize_session=False)
    db.session.commit()


def _sign_in(client, *, consent):
    with App.app.app_context():
        user = User(email='tam+student@example.com',
                    password_hash=bcrypt.generate_password_hash('study-password').decode())
        user.ai_personalization_opt_in = consent
        db.session.add(user)
        db.session.commit()
        account = LinkedAccount(user_id=user.id, login_type='canvas', is_active=True)
        account.set_credentials({'canvas_url': 'https://school.instructure.com',
                                 'canvas_token': 'student-token'})
        db.session.add(account)
        db.session.commit()
        uid = user.id
    with client.session_transaction() as session:
        session['_user_id'] = str(uid)
        session['_fresh'] = True


def test_assignment_list_requires_signed_in_consent_and_own_canvas(client, monkeypatch):
    calls = []
    monkeypatch.setattr(assignment_materials, 'list_assignments',
                        lambda url, token: calls.append((url, token)) or [{'id': '8', 'course_id': '4'}])
    assert client.get('/api/tutor/assignments').status_code == 401
    _sign_in(client, consent=False)
    assert client.get('/api/tutor/assignments').status_code == 403
    assert calls == []
    with App.app.app_context():
        User.query.filter_by(email='tam+student@example.com').first().ai_personalization_opt_in = True
        db.session.commit()
    result = client.get('/api/tutor/assignments')
    assert result.status_code == 200
    assert result.json['assignments'][0]['id'] == '8'
    assert calls == [('https://school.instructure.com', 'student-token')]


def test_selected_assignment_reaches_tutor_without_entering_saved_messages(client, monkeypatch):
    _sign_in(client, consent=True)
    context = {'title': 'Fractions', 'description': 'Compare two fractions.',
               'materials': [{'name': 'worksheet.txt', 'text': 'SECRET SCHOOL TEXT'}],
               'skipped_count': 0}
    monkeypatch.setattr(assignment_materials, 'load_assignment',
                        lambda url, token, course, assignment: context)
    monkeypatch.setattr(chatbot_api, '_check_and_increment_tutor_limit', lambda: (True, 49, 50))
    monkeypatch.setattr(chatbot_api.ai_firewall, 'guard',
                        lambda *a, **k: SimpleNamespace(max_output_tokens=1800, plan='free'))
    monkeypatch.setattr(chatbot_api.ai_firewall, 'record_tokens', lambda *a, **k: None)
    monkeypatch.setattr(chatbot_api, '_safety_check_user_message', lambda *a: None)
    monkeypatch.setattr(chatbot_api, '_safety_check_assistant_reply', lambda *a: None)
    monkeypatch.setattr(chatbot_api, '_load_tutor_memory',
                        lambda: {'id': 1, 'profile_json': '{}'})
    monkeypatch.setattr(chatbot_api, '_build_personalization_prompt', lambda **k: None)
    monkeypatch.setattr(chatbot_api, '_prepare_adaptive_turn', lambda *a: None)
    monkeypatch.setattr(chatbot_api, '_load_user_identity', lambda: {})
    monkeypatch.setattr(chatbot_api, '_save_tutor_profile', lambda *a: None)
    monkeypatch.setattr(chatbot_api, '_record_adaptive_turn', lambda *a: None)
    monkeypatch.setattr(chatbot_api, '_ensure_conversation',
                        lambda *a: {'id': 31, 'title': 'Fractions'})
    saved = []
    monkeypatch.setattr(chatbot_api, '_save_conversation', lambda ident, messages, *a: saved.extend(messages))
    seen = []
    monkeypatch.setattr(chatbot_api, '_llm_chat',
                        lambda **kwargs: seen.extend(kwargs['messages']) or 'What do you know about common denominators?')

    result = client.post('/api/tutor', json={
        'messages': [{'role': 'user', 'content': 'Help me get started.'}],
        'assignment_ref': {'course_id': '4', 'assignment_id': '8'},
    })
    assert result.status_code == 200, result.json
    assert result.json['assignment_context']['files_read'] == ['worksheet.txt']
    assert any('SECRET SCHOOL TEXT' in message['content'] for message in seen)
    assert all('SECRET SCHOOL TEXT' not in message['content'] for message in saved)


def test_photo_tutor_receives_selected_assignment_context(client, monkeypatch):
    _sign_in(client, consent=True)
    context = {'title': 'Diagram', 'description': 'Label the cycle.',
               'materials': [{'name': 'reading.txt', 'text': 'Evaporation comes first.'}],
               'skipped_count': 0}
    monkeypatch.setattr(assignment_materials, 'load_assignment', lambda *a: context)
    monkeypatch.setattr(chatbot_api.ai_firewall, 'guard',
                        lambda *a, **k: SimpleNamespace(max_output_tokens=1800, plan='free'))
    monkeypatch.setattr(chatbot_api, 'ai_available', lambda: True)
    seen = []
    monkeypatch.setattr(chatbot_api, 'ai_vision',
                        lambda **kwargs: seen.append(kwargs['system_prompt']) or 'What does evaporation mean?')
    monkeypatch.setattr(chatbot_api, '_get_conversation', lambda *a: None)
    monkeypatch.setattr(chatbot_api, '_ensure_conversation',
                        lambda *a: {'id': 31, 'title': 'Diagram'})
    monkeypatch.setattr(chatbot_api, '_save_conversation', lambda *a: None)

    result = client.post('/api/tutor/vision', json={
        'question': 'Help me read this diagram.', 'image_b64': 'aGVsbG8=',
        'assignment_ref': {'course_id': '4', 'assignment_id': '8'},
    })
    assert result.status_code == 200, result.json
    assert result.json['assignment_context']['files_read'] == ['reading.txt']
    assert 'Evaporation comes first.' in seen[0]
