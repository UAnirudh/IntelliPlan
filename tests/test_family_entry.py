"""Family remains usable without the optional parent DNS record."""
from uuid import uuid4

import pytest

import App


@pytest.fixture
def client():
    App.app.config['TESTING'] = True
    App.limiter.enabled = False
    with App.app.app_context():
        App.db.create_all()
    with App.app.test_client() as client:
        yield client
    App.limiter.enabled = True


def test_main_domain_family_entry_preserves_login_destination(client):
    entry = client.get('/parent')
    assert entry.status_code == 302
    assert entry.location == '/login/account?next=/parent'
    page = client.get(entry.location)
    assert b'Sign in to Family' in page.data
    assert b'name="next" value="/parent"' in page.data
    email = f'family-{uuid4().hex}@example.test'
    with App.app.app_context():
        user = App.User(email=email, role='parent', birth_year=1980,
                        password_hash=App.bcrypt.generate_password_hash('family-test-pass').decode())
        App.db.session.add(user)
        App.db.session.commit()
    response = client.post('/login/account', data={
        'email': email, 'password': 'family-test-pass', 'next': '/parent'})
    assert response.status_code == 302 and response.location == '/parent'
    portal = client.get('/parent')
    assert b'familyWorkspace' in portal.data
    assert b'href="/parent" aria-label="IntelliPlan Family home"' in portal.data


def test_family_registration_flow_and_unsafe_destinations(client):
    register = client.get('/register?next=/parent')
    assert b'Create your Family account' in register.data
    assert b'name="next" value="/parent"' in register.data
    normal = client.get('/login/account?next=https://evil.example/parent')
    assert b'Sign in to Family' not in normal.data
    parent_host = client.get('/login/account', base_url='https://parent.intelliplan.tech')
    assert b'Sign in to Family' in parent_host.data
    assert b'name="next" value="/parent"' not in parent_host.data
    assert client.get('/parent', base_url='https://parent.intelliplan.tech').location == '/'


def test_parent_host_login_keeps_cookie_host_only(client):
    email = f'family-host-{uuid4().hex}@example.test'
    with App.app.app_context():
        user = App.User(email=email, role='parent', birth_year=1980,
                        password_hash=App.bcrypt.generate_password_hash('family-test-pass').decode())
        App.db.session.add(user); App.db.session.commit()
    response = client.post('/login/account', base_url='https://parent.intelliplan.tech',
                           headers={'Origin': 'https://parent.intelliplan.tech'},
                           data={'email': email, 'password': 'family-test-pass'})
    assert response.status_code == 302 and response.location == '/'
    assert all('Domain=' not in cookie for cookie in response.headers.getlist('Set-Cookie'))
    assert b'familyWorkspace' in client.get('/', base_url='https://parent.intelliplan.tech').data
    assert client.post('/api/roles/role', base_url='https://parent.intelliplan.tech',
                       headers={'Origin': 'https://evil.example'}, json={'role': 'parent'}).status_code == 403
