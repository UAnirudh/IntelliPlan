"""Teacher registration/sign-in reuse the normal credential and consent boundary."""
from uuid import uuid4

import App
from tests.test_family_entry import client


def test_teacher_signup_signin_and_student_consent_flow(client, monkeypatch):
    monkeypatch.setattr(App, 'check_recaptcha', lambda action: None)
    monkeypatch.setattr(App, 'send_welcome_email_on_signup', lambda uid: None)
    monkeypatch.setattr(App, '_grant_referral_bonus', lambda user: None)
    entry = client.get('/teacher')
    assert entry.status_code == 302 and entry.location == '/login/account?next=/teacher'
    assert b'Sign in to Teacher' in client.get(entry.location).data
    page = client.get('/register?next=/teacher')
    assert b'Create your Teacher account' in page.data
    assert b'name="next" value="/teacher"' in page.data
    email = f'teacher-{uuid4().hex}@example.test'
    response = client.post('/register', data={'email': email, 'password': 'teacher-test-pass',
        'confirm_password': 'teacher-test-pass', 'birth_year': 1980, 'next': '/teacher'})
    assert response.status_code == 302 and response.location == '/teacher'
    assert b'Your students' in client.get('/teacher').data
    with App.app.app_context():
        teacher = App.User.query.filter_by(email=email).first()
        assert teacher.role == 'teacher'
        assert App.is_admin(teacher) is False
        student = App.User(email=f'student-{uuid4().hex}@example.test', role='student', birth_year=2010,
            parent_consent_granted=True, password_hash='')
        App.db.session.add(student); App.db.session.commit()
        student_email, student_id = student.email, student.id
    link = client.post('/api/roles/invite', json={'student_email': student_email}).get_json()['link_id']
    assert client.get(f'/api/roles/student/{student_id}/overview').status_code == 403
    client.get('/logout')
    with client.session_transaction() as session:
        session['_user_id'] = str(student_id); session['_fresh'] = True; session['session_epoch'] = 0
    assert client.post(f'/api/roles/links/{link}/accept', json={'scopes':['tools']}).status_code == 200
    client.get('/logout')
    signed_in = client.post('/login/account', data={'email': email, 'password':'teacher-test-pass', 'next':'/teacher'})
    assert signed_in.status_code == 302 and signed_in.location == '/teacher'
    overview = client.get(f'/api/roles/student/{student_id}/overview')
    assert overview.status_code == 200
    assert overview.get_json()['summary']['work_status'] == 'not_shared'
    assert overview.get_json()['tools']


def test_teacher_form_rejects_minors_and_unsafe_destinations(client, monkeypatch):
    monkeypatch.setattr(App, 'check_recaptcha', lambda action: None)
    response = client.post('/register', data={'email':f'minor-{uuid4().hex}@example.test',
        'password':'teacher-test-pass','birth_year':2011,'next':'/teacher'})
    assert b'Parent and teacher accounts are for adults.' in response.data
    assert b'Sign in to Teacher' not in client.get('/login/account?next=https://evil.test/teacher').data
