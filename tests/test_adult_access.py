"""Adult sharing and account administration use existing authenticated identities."""
import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin
from flask_sqlalchemy import SQLAlchemy

from intelliplan.admin_access import bp, has_grant, register_model
from tests.test_family_app import family_app as family_app, signed_in


@pytest.fixture
def admin_app():
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='admin-test', SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
    db = SQLAlchemy(app)
    class User(UserMixin, db.Model):
        __tablename__ = 'users'
        id = db.Column(db.Integer, primary_key=True)
        email = db.Column(db.String(255), unique=True)
        name = db.Column(db.String(255))
    app.intelliplan_db, app.intelliplan_user_model = db, User
    app.extensions['admin_owner_checker'] = lambda user: user.is_authenticated and user.id == 1
    with app.app_context():
        app.extensions['account_admin_grant_model'] = register_model(db)
        db.create_all()
        db.session.add_all([User(id=1, email='owner@example.test'), User(id=2, email='member@example.test'),
                            User(id=3, email='outsider@example.test')])
        db.session.commit()
    login = LoginManager(app)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))
    app.register_blueprint(bp)
    @app.get('/test/admin-status')
    def status():
        from flask_login import current_user
        return {'admin': has_grant(current_user)}
    return app


def test_owner_grants_exact_account_and_revocation_applies_to_same_session(admin_app):
    owner, member, outsider = (signed_in(admin_app, uid) for uid in (1, 2, 3))
    endpoint = '/api/admin/accounts'
    assert member.post(endpoint, json={'user_id': 2}).status_code == 403
    assert owner.post(endpoint, json={'email': 'member@example.test'}).status_code == 400
    assert owner.post(endpoint, json={'user_id': True}).status_code == 400
    assert owner.post(endpoint, json={'user_id': 1}).status_code == 400
    assert owner.post(endpoint, json={'user_id': 999}).status_code == 404
    found = owner.get(endpoint + '?email=MEMBER@example.test').get_json()['accounts']
    assert found[0]['id'] == 2 and found[0]['admin'] is False
    assert owner.post(endpoint, json={'user_id': 2}).status_code == 200
    assert member.get('/test/admin-status').get_json()['admin'] is True
    assert outsider.get('/test/admin-status').get_json()['admin'] is False
    # Delegation is not authority to delegate again.
    assert member.post(endpoint, json={'user_id': 3}).status_code == 403
    assert member.get(endpoint).status_code == 403
    assert owner.delete(endpoint, json={'user_id': 2}).status_code == 200
    assert member.get('/test/admin-status').get_json()['admin'] is False
    with admin_app.app_context():
        rows = admin_app.extensions['account_admin_audit_model'].query.order_by(
            admin_app.extensions['account_admin_audit_model'].id).all()
        assert [(row.actor_id, row.target_id, row.action) for row in rows] == [(1, 2, 'grant'), (1, 2, 'revoke')]


def test_teacher_student_selects_scopes_and_tool_policy_is_read_only(family_app, monkeypatch):
    from intelliplan.learning import policy
    with family_app.app_context():
        teacher = family_app.intelliplan_db.session.get(family_app.intelliplan_user_model, 1)
        teacher.role = 'teacher'
        family_app.intelliplan_db.session.commit()
    teacher, student, other = (signed_in(family_app, uid) for uid in (1, 2, 3))
    monkeypatch.setattr(policy, 'decision', lambda user, key: (key != 'math', 'institution_policy' if key == 'math' else 'allowed'))
    path = '/api/roles/student/2/overview'
    link = teacher.post('/api/roles/invite', json={'student_email': 'college@example.edu'}).get_json()['link_id']
    assert teacher.get(path).status_code == 403
    assert student.post(f'/api/roles/links/{link}/accept', json={'scopes': ['tools']}).status_code == 200
    data = teacher.get(path).get_json()
    assert data['summary']['work_status'] == 'not_shared'
    assert data['summary']['upcoming'] is None
    assert next(t for t in data['tools'] if t['key'] == 'math')['allowed'] is False
    assert 'conversation' not in str(data) and 'answers' not in data
    assert other.get(path).status_code == 403
    assert teacher.patch(f'/api/roles/links/{link}/scopes', json={'scopes': ['work']}).status_code == 404
    assert student.patch(f'/api/roles/links/{link}/scopes', json={'scopes': ['work']}).status_code == 200
    shared = teacher.get(path).get_json()
    assert 'tools' not in shared and shared['summary']['upcoming'][0]['title'] == 'Research paper'
    assert student.delete(f'/api/roles/links/{link}').status_code == 200
    assert teacher.get(path).status_code == 403


def test_legacy_approval_does_not_expand_to_new_tool_scope(family_app):
    parent, student = signed_in(family_app, 1), signed_in(family_app, 2)
    link = parent.post('/api/roles/invite', json={'student_email': 'college@example.edu'}).get_json()['link_id']
    assert student.post(f'/api/roles/links/{link}/accept').status_code == 200
    assert 'tools' not in parent.get('/api/roles/student/2/overview').get_json()


@pytest.mark.parametrize('birth_year', [None, 2012, 2008])
def test_adult_invite_and_role_require_recorded_adult_age(family_app, birth_year):
    with family_app.app_context():
        user = family_app.intelliplan_db.session.get(family_app.intelliplan_user_model, 1)
        user.birth_year = birth_year
        family_app.intelliplan_db.session.commit()
    client = signed_in(family_app, 1)
    assert client.post('/api/roles/role', json={'role': 'teacher'}).status_code == 403
    assert client.post('/api/roles/invite', json={'student_email': 'college@example.edu'}).status_code == 403


def _count_selects(app, client, path):
    from sqlalchemy import event

    selects = []
    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith('SELECT'):
            selects.append(statement)
    with app.app_context():
        engine = app.intelliplan_db.engine
    event.listen(engine, 'before_cursor_execute', record)
    try:
        response = client.get(path)
    finally:
        event.remove(engine, 'before_cursor_execute', record)
    assert response.status_code == 200
    return response.get_json(), len(selects)


def test_admin_listing_batches_grants_and_keeps_revoked_accounts_out(admin_app):
    with admin_app.app_context():
        db, User = admin_app.intelliplan_db, admin_app.intelliplan_user_model
        Grant = admin_app.extensions['account_admin_grant_model']
        db.session.add_all([User(id=uid, email=f'admin{uid}@example.test') for uid in range(10, 50)])
        db.session.flush()
        db.session.add_all([Grant(user_id=uid, granted_by_id=1) for uid in range(10, 50)])
        db.session.commit()
    owner = signed_in(admin_app, 1)
    payload, queries = _count_selects(admin_app, owner, '/api/admin/accounts')
    assert len(payload['accounts']) == 40
    assert all(account['admin'] and not account['owner'] for account in payload['accounts'])
    assert queries == 3  # Authentication, joined accounts, fresh batch of grants.
    assert owner.delete('/api/admin/accounts', json={'user_id': 10}).status_code == 200
    payload, queries = _count_selects(admin_app, owner, '/api/admin/accounts')
    assert {account['id'] for account in payload['accounts']} == set(range(11, 50))
    assert queries == 3


@pytest.mark.parametrize('path,user_id,field', [
    ('/api/roles/links', 1, 'links'),
    ('/api/roles/pending', 2, 'pending'),
    ('/api/roles/my-links', 2, 'links'),
])
def test_adult_link_lists_have_constant_queries_and_owner_isolation(family_app, path, user_id, field):
    with family_app.app_context():
        db, User, Link = (family_app.intelliplan_db, family_app.intelliplan_user_model,
                          family_app.intelliplan_student_link_model)
        db.session.add_all([User(id=uid, email=f'linked{uid}@example.test', role='student' if user_id == 1 else 'parent',
                                birth_year=1980) for uid in range(10, 50)])
        db.session.flush()
        db.session.add_all([Link(linker_user_id=1 if user_id == 1 else uid,
                                student_user_id=uid if user_id == 1 else 2,
                                relationship='parent') for uid in range(10, 50)])
        # A different account's relationship must never appear in the batch.
        db.session.add(Link(linker_user_id=3, student_user_id=3, relationship='parent'))
        db.session.commit()
    payload, queries = _count_selects(family_app, signed_in(family_app, user_id), path)
    assert len(payload[field]) == 40
    email_key = 'student_email' if user_id == 1 else 'linker_email'
    assert {row[email_key] for row in payload[field]} == {f'linked{uid}@example.test' for uid in range(10, 50)}
    assert queries == 3  # Authentication, scoped links, related users in one query.


def test_shared_work_filters_imported_open_and_completed_tasks_by_student_policy(family_app, monkeypatch):
    from intelliplan.learning import policy
    checked = []
    def decision(user, source):
        checked.append(user.id)
        return source != 'canvas', 'allowed'
    monkeypatch.setattr(policy, 'decision', decision)
    with family_app.app_context():
        db, Task = family_app.intelliplan_db, family_app.intelliplan_manual_task_model
        db.session.add_all([Task(user_id=2, title=f'{source}-{done}', course='Imported', done=done,
                                import_source=source, estimated_time=30)
                            for source in ('canvas', 'scraper:canvas', 'moodle', 'future-source')
                            for done in (False, True)])
        db.session.commit()
    parent, student = signed_in(family_app, 1), signed_in(family_app, 2)
    link = parent.post('/api/roles/invite', json={'student_email': 'college@example.edu'}).get_json()['link_id']
    assert student.post(f'/api/roles/links/{link}/accept', json={'scopes': ['work']}).status_code == 200
    summary = parent.get('/api/roles/student/2/overview').get_json()['summary']
    assert summary['work_status'] == 'ok'
    assert summary['open'] == 2
    assert summary['completed'] == 3  # One personal, one allowed import, one dismissed LMS task.
    assert {row['title'] for row in summary['upcoming']} == {'Research paper', 'moodle-False'}
    assert checked and set(checked) == {2}
    monkeypatch.setattr(policy, 'decision', lambda user, source: (True, 'allowed'))
    restored = parent.get('/api/roles/student/2/overview').get_json()['summary']
    assert restored['open'] == 5 and restored['completed'] == 6
