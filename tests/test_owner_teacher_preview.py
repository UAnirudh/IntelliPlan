"""The requested owner gets fictional teacher tools without changing age or role."""
import App
from tests.test_family_entry import client


def test_owner_preview_is_exact_verified_and_founder_session_only(client, monkeypatch):
    monkeypatch.setattr(App, 'FOUNDER_EMAIL', 'uanirudh0811@gmail.com')
    with App.app.app_context():
        user = App.User(email='uanirudh0811@gmail.com', password_hash='unused',
                        birth_year=2020, email_verified=True, role='student')
        App.db.session.add(user)
        App.db.session.commit()
        uid = user.id
        App._get_or_create_identity(uid).completed = True
        App.db.session.commit()
    with client.session_transaction() as state:
        state['_user_id'] = str(uid)
        state['_fresh'] = True
    assert client.get('/teacher/demo').status_code == 302
    with client.session_transaction() as state:
        state['ip_founder_mode'] = True
    response = client.get('/teacher/demo')
    assert response.status_code == 200
    assert b'Jordan Rivera' in response.data
    assert b'My study week' in client.get('/teacher/demo?view=student').data
    assert b'Family progress preview' in client.get('/teacher/demo?view=family').data
    assert response.headers['Cache-Control'] == 'no-store'
    # Preview access never converts the account to an adult teacher.
    assert client.post('/api/roles/role', json={'role': 'teacher'}).status_code == 403
    with App.app.app_context():
        user = App.db.session.get(App.User, uid)
        assert user.birth_year == 2020
        assert user.role == 'student'
        user.email_verified = False
        App.db.session.commit()
    assert client.get('/teacher/demo').status_code == 302
