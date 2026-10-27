"""Exercise the schoolwork consent boundary and the actual tutor prompt path."""

from types import SimpleNamespace
from datetime import datetime

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


def _sign_in(client, *, consent, birth_year='older_student'):
    with App.app.app_context():
        user = User(email='tam+student@example.com',
                    password_hash=bcrypt.generate_password_hash('study-password').decode())
        user.ai_personalization_opt_in = consent
        user.birth_year = datetime.utcnow().year - 16 if birth_year == 'older_student' else birth_year
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


def test_schoolwork_waits_for_age_and_under_13_parent_consent(client, monkeypatch):
    calls = []
    monkeypatch.setattr(assignment_materials, 'list_assignments',
                        lambda *args: calls.append(args) or [])
    _sign_in(client, consent=True, birth_year=None)
    response = client.get('/api/tutor/assignments')
    assert response.status_code == 403
    assert response.json['age_check_url'].startswith('/account/age')
    assert calls == []
    with App.app.app_context():
        user = User.query.filter_by(email='tam+student@example.com').first()
        user.birth_year = datetime.utcnow().year - 10
        db.session.commit()
    assert client.get('/api/tutor/assignments').status_code == 403
    assert calls == []
    with App.app.app_context():
        user = User.query.filter_by(email='tam+student@example.com').first()
        user.parent_consent_granted = True
        db.session.commit()
    assert client.get('/api/tutor/assignments').status_code == 200
    assert len(calls) == 1


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
    system = '\n'.join(message['content'] for message in seen if message['role'] == 'system')
    assert 'Ask one manageable question per turn' in system
    assert 'Give the final answer in one line' not in system
    assert all('SECRET SCHOOL TEXT' not in message['content'] for message in saved)


def test_explicit_test_prep_retrieves_only_the_students_notes_and_returns_citations(client, monkeypatch):
    _sign_in(client, consent=False)
    with App.app.app_context():
        owner_id = User.query.filter_by(email='tam+student@example.com').one().id

    sync_calls, search_calls, seen_messages = [], [], []
    monkeypatch.setattr(chatbot_api, '_check_and_increment_tutor_limit', lambda: (True, 49, 50))
    monkeypatch.setattr(chatbot_api.ai_firewall, 'guard',
                        lambda *a, **k: SimpleNamespace(max_output_tokens=1800, plan='free'))
    monkeypatch.setattr(chatbot_api.ai_firewall, 'record_tokens', lambda *a, **k: None)
    monkeypatch.setattr(chatbot_api, '_safety_check_user_message', lambda *a: None)
    monkeypatch.setattr(chatbot_api, '_safety_check_assistant_reply', lambda *a: None)
    monkeypatch.setattr(chatbot_api, '_load_tutor_memory', lambda: {'id': 1, 'profile_json': '{}'})
    monkeypatch.setattr(chatbot_api, '_build_personalization_prompt', lambda **k: None)
    monkeypatch.setattr(chatbot_api, '_prepare_adaptive_turn', lambda *a: None)
    monkeypatch.setattr(chatbot_api, '_load_user_identity', lambda: {})
    monkeypatch.setattr(chatbot_api, '_save_tutor_profile', lambda *a: None)
    monkeypatch.setattr(chatbot_api, '_record_adaptive_turn', lambda *a: None)
    monkeypatch.setattr(chatbot_api, '_ensure_conversation', lambda *a: {'id': 31, 'title': 'New chat'})
    monkeypatch.setattr(chatbot_api, '_save_conversation', lambda *a: None)
    monkeypatch.setattr(chatbot_api.ai_firewall, 'record_tokens', lambda *a: None)
    monkeypatch.setattr(chatbot_api.ai_firewall, 'guard',
                        lambda *a, **k: SimpleNamespace(max_output_tokens=1800, plan='free'))

    from intelliplan.learning import policy
    monkeypatch.setattr(policy, 'decision', lambda user, key: (True, 'allowed'))
    monkeypatch.setattr(policy, 'permitted_providers', lambda user: {'groq'})
    monkeypatch.setattr(App._retrieval, 'sync_user', lambda uid: sync_calls.append(uid))
    monkeypatch.setattr(App._retrieval, 'search', lambda uid, query, k=5: search_calls.append((uid, query, k)) or [{
        'note_id': 72, 'title': 'Cell structure review', 'course': 'Biology',
        'date': '2026-10-20', 'text': 'Mitochondria release usable energy through cellular respiration.',
    }])
    monkeypatch.setattr(chatbot_api, '_llm_chat', lambda **kwargs: (
        seen_messages.extend(kwargs['messages']) or
        'Here are two cards from your notes. [N1]'))

    result = client.post('/api/tutor', json={'messages': [{
        'role': 'user', 'content': 'Make flashcards for my biology test from my notes.'
    }]})

    assert result.status_code == 200, result.get_json()
    assert sync_calls == [owner_id]
    assert search_calls[0][0] == owner_id  # retrieval is scoped to the signed-in account
    assert search_calls[0][2] == 5
    context = '\n'.join(m['content'] for m in seen_messages if m['role'] == 'system')
    assert 'Mitochondria release usable energy' in context
    assert 'Cite supporting material inline as [N1]' in context
    assert result.json['study_sources'] == [{
        'note_id': 72, 'title': 'Cell structure review', 'course': 'Biology', 'date': '2026-10-20'
    }]


def test_exam_note_retrieval_stops_when_school_blocks_uploads(client, monkeypatch):
    _sign_in(client, consent=True)
    calls = []
    from intelliplan.learning import policy
    monkeypatch.setattr(policy, 'decision', lambda user, key: (key != 'uploads', 'blocked'))
    monkeypatch.setattr(policy, 'permitted_providers', lambda user: {'groq'})
    monkeypatch.setattr(App._retrieval, 'sync_user', lambda uid: calls.append(('sync', uid)))
    monkeypatch.setattr(App._retrieval, 'search', lambda *args, **kwargs: calls.append(('search', args)))

    with App.app.app_context():
        user = User.query.filter_by(email='tam+student@example.com').one()
        context, sources = chatbot_api._retrieve_exam_notes(user, 'make cards for my chemistry test')

    assert context == '' and sources == []
    assert calls == []


def test_note_retrieval_intent_requires_an_explicit_study_request():
    assert chatbot_api._STUDY_MATERIAL_INTENT.search('Make note cards to prepare for my physics final.')
    assert chatbot_api._STUDY_MATERIAL_INTENT.search('Quiz me on the notes for my biology test.')
    assert not chatbot_api._STUDY_MATERIAL_INTENT.search('How do I use this Python test fixture?')


def test_tutor_provider_readiness_uses_resolved_plan_and_model_tier(monkeypatch):
    calls = []
    monkeypatch.setattr(chatbot_api, '_use_ollama', lambda: False)
    monkeypatch.setattr(chatbot_api, 'ai_available',
                        lambda **kwargs: calls.append(kwargs) or True)
    monkeypatch.setattr(chatbot_api, 'ai_chat', lambda *args, **kwargs: 'synthetic reply')

    reply = chatbot_api._llm_chat(
        model='openai/gpt-oss-120b', messages=[{'role': 'user', 'content': 'hello'}],
        plan='paid')

    assert reply == 'synthetic reply'
    assert calls == [{'plan': 'paid', 'tier': 'standard'}]


@pytest.mark.parametrize('mode', ['single', 'multi'])
def test_photo_tutor_receives_selected_assignment_context(client, monkeypatch, mode):
    monkeypatch.setattr(chatbot_api, '_moderation_response', lambda *a, **k: None)
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
        'mode': mode,
        'assignment_ref': {'course_id': '4', 'assignment_id': '8'},
    })
    assert result.status_code == 200, result.json
    assert result.json['assignment_context']['files_read'] == ['reading.txt']
    assert 'Evaporation comes first.' in seen[0]
    assert 'Ask one manageable question per turn' in seen[0]
    assert '**Answer:**' not in seen[0]
    assert 'Work every distinct problem' not in seen[0]


def test_study_map_needs_consent_and_verifies_own_canvas_source(client, monkeypatch):
    calls = []
    monkeypatch.setattr(assignment_materials, 'load_assignment',
                        lambda *args: calls.append(args) or {
                            'title': 'Fractions', 'description': 'Compare two fractions.',
                            'materials': [{'name': 'sheet.txt', 'text': 'Show your reasoning.'}],
                            'skipped_count': 0})
    ref = {'assignment_ref': {'course_id': '4', 'assignment_id': '8'}}
    assert client.post('/api/tutor/assignment-map', json=ref).status_code == 401
    _sign_in(client, consent=False)
    assert client.post('/api/tutor/assignment-map', json=ref).status_code == 403
    assert calls == []
    with App.app.app_context():
        User.query.filter_by(email='tam+student@example.com').first().ai_personalization_opt_in = True
        db.session.commit()
    monkeypatch.setattr(chatbot_api.ai_firewall, 'guard',
                        lambda *a, **k: SimpleNamespace(max_output_tokens=800, plan='free'))
    monkeypatch.setattr(chatbot_api.ai_firewall, 'record_tokens', lambda *a, **k: None)
    monkeypatch.setattr(chatbot_api, '_llm_chat', lambda **kwargs: (
        '{"steps":[{"focus":"Compare the fractions","source_id":"directions",'
        '"evidence":"Compare two fractions."}],'
        '"first_question":"How would you compare them?"}'))
    result = client.post('/api/tutor/assignment-map', json=ref)
    assert result.status_code == 200, result.json
    assert result.json['status'] == 'grounded'
    assert result.json['steps'][0]['source'] == 'Assignment directions'
    assert calls == [('https://school.instructure.com', 'student-token', '4', '8')]


def test_study_map_with_no_readable_sources_does_not_call_ai(client, monkeypatch):
    _sign_in(client, consent=True)
    monkeypatch.setattr(assignment_materials, 'load_assignment', lambda *args: {
        'title': 'Lab', 'description': '', 'materials': [], 'skipped_count': 2})
    monkeypatch.setattr(chatbot_api, '_llm_chat',
                        lambda **kwargs: pytest.fail('AI should not be called for empty source text'))
    result = client.post('/api/tutor/assignment-map', json={
        'assignment_ref': {'course_id': '4', 'assignment_id': '8'}})
    assert result.status_code == 200
    assert result.json['status'] == 'limited'
    assert result.json['files_skipped'] == 2
