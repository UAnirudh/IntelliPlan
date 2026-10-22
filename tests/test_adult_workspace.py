"""Adults get linked-student views, rather than their own student workspace."""
from html.parser import HTMLParser
from uuid import uuid4

import pytest

import App


class Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.hrefs = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.hrefs.append(dict(attrs).get('href'))


@pytest.fixture
def accounts(monkeypatch):
    monkeypatch.setitem(App.app.config, 'TESTING', True)
    monkeypatch.setattr(App.limiter, 'enabled', False)
    with App.app.app_context():
        App.db.create_all()
        users = {}
        for role in ('parent', 'teacher', 'student'):
            user = App.User(email=f'{role}-{uuid4().hex}@example.test', name=role.title(),
                            role=role, birth_year=1980, parent_consent_granted=True,
                            password_hash=App.bcrypt.generate_password_hash('adult-workspace-test').decode())
            App.db.session.add(user)
            App.db.session.flush()
            users[role] = {'id': user.id, 'email': user.email}
        App.db.session.commit()
    return users


def signed_in(account, host=None):
    client = App.app.test_client()
    with client.session_transaction(base_url=host) as session:
        session.update(_user_id=str(account['id']), _fresh=True, session_epoch=0)
    return client


@pytest.mark.parametrize('role', ['parent', 'teacher'])
def test_adult_navigation_and_direct_student_pages(accounts, role):
    client = signed_in(accounts[role])
    home = '/' + role
    for path in ('/', '/command-center', '/dashboard', '/scheduler', '/study-tools',
                 '/tutor', '/classes', '/library', '/study-files', '/live',
                 '/linked-accounts', '/foundations/parent', '/onboarding'):
        response = client.get(path)
        assert response.status_code == 302, (role, path)
        assert response.location == home, (role, path, response.location)
    for path in (home, '/adult/account', '/legal', '/forgot-password', '/reset-password'):
        response = client.get(path)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'adult-workspace' in html
        forbidden = {'/dashboard', '/command-center', '/classes', '/scheduler',
                     '/tutor', '/study-tools', '/connect', '/linked-accounts', '/'}
        assert not forbidden.intersection(Links(html).hrefs)
        assert 'app-sidebar' not in html
    assert client.get('/settings').location == '/adult/account'
    other = '/teacher' if role == 'parent' else '/parent'
    assert client.get(other).location == home


@pytest.mark.parametrize('role', ['parent', 'teacher'])
def test_adult_student_apis_are_unavailable_but_sharing_remains(accounts, role):
    client = signed_in(accounts[role])
    for method, path in (('GET', '/api/classes/manual'), ('POST', '/api/classes/manual'),
                         ('GET', '/api/primer/learners'), ('POST', '/api/tasks'),
                         ('GET', '/api/cloud-documents'), ('POST', '/settings')):
        response = client.open(path, method=method, json={})
        assert response.status_code == 403, path
        assert response.get_json()['error'] == 'student_workspace_only'
    assert client.get('/api/roles/links').status_code == 200
    response = client.get(f"/api/roles/student/{accounts['student']['id']}/overview")
    assert response.status_code == 403
    assert client.get('/forgot-password').status_code == 200


@pytest.mark.parametrize('role', ['parent', 'teacher'])
def test_plain_login_uses_stored_role(accounts, role):
    response = App.app.test_client().post('/login/account', data={
        'email': accounts[role]['email'], 'password': 'adult-workspace-test'})
    assert response.status_code == 302
    assert response.location == '/' + role


def test_family_hostname_account_and_configured_hostname(accounts, monkeypatch):
    monkeypatch.setitem(App.app.config, 'PARENT_APP_HOST', 'family.example.test')
    host = 'https://family.example.test'
    client = signed_in(accounts['parent'], host)
    assert client.get('/', base_url=host).status_code == 200
    assert client.get('/parent', base_url=host).location == '/'
    assert client.get('/settings', base_url=host).location == '/adult/account'
    assert client.get('/adult/account', base_url=host).status_code == 200
    assert client.get('/legal', base_url=host).status_code == 200
    teacher = signed_in(accounts['teacher'], host)
    assert teacher.get('/', base_url=host).location == '/teacher'
    assert teacher.get('/teacher', base_url=host).status_code == 200


def test_students_keep_their_own_workspace(accounts):
    student = signed_in(accounts['student'])
    assert student.get('/linked-accounts').status_code == 200
    assert student.get('/api/classes/manual').status_code == 200
    assert student.get('/adult/account').status_code == 403


@pytest.mark.parametrize('role', ['parent', 'teacher'])
def test_password_reset_invalidates_adult_session_before_routing(accounts, role):
    client = signed_in(accounts[role])
    with App.app.app_context():
        user = App.db.session.get(App.User, accounts[role]['id'])
        user.session_epoch = 1
        App.db.session.commit()
    response = client.get('/' + role)
    assert response.status_code == 302
    assert '/login' in response.location
    with client.session_transaction() as session:
        assert '_user_id' not in session


@pytest.mark.parametrize('role', ['parent', 'teacher'])
def test_adult_token_cannot_read_student_api(accounts, role):
    from auth_api import make_token
    with App.app.app_context():
        token = make_token(App.db.session.get(App.User, accounts[role]['id']))
        extension_token = 'adult-test-' + uuid4().hex
        App.db.session.add(App.ExtensionToken(user_id=accounts[role]['id'], token=extension_token))
        App.db.session.commit()
    client = App.app.test_client()
    for path in ('/api/v1/assignments', '/api/v1/courses', '/api/v1/grades'):
        response = client.get(path, headers={'Authorization': f'Bearer {token}'})
        assert response.status_code == 403
        assert response.get_json()['error'] == 'student_workspace_only'
    response = client.get('/extension/tasks', headers={'X-Extension-Token': extension_token})
    assert response.status_code == 403
    assert response.get_json()['error'] == 'student_workspace_only'
    response = client.post('/extension/task/add', headers={'X-Extension-Token': extension_token}, json={})
    assert response.status_code == 403
    response = client.get('/api/focus-shield/settings', headers={'X-Extension-Token': extension_token})
    assert response.status_code == 403
    assert response.get_json()['error'] == 'student_workspace_only'
