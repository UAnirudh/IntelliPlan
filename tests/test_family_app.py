"""Family sharing is consented, account-scoped, and backed by real records."""

from datetime import date, timedelta
import json

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin
from flask_sqlalchemy import SQLAlchemy

from intelliplan.api.roles import bp as roles_bp


@pytest.fixture
def family_app():
    app = Flask(__name__)
    app.config.update(SECRET_KEY='family-test', SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', TESTING=True)
    db = SQLAlchemy(app)

    class User(UserMixin, db.Model):
        __tablename__ = 'users'
        id = db.Column(db.Integer, primary_key=True)
        email = db.Column(db.String(255))
        name = db.Column(db.String(255))
        role = db.Column(db.String(16))
        birth_year = db.Column(db.Integer)

    class Link(db.Model):
        __tablename__ = 'student_links'
        id = db.Column(db.Integer, primary_key=True)
        linker_user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
        student_user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
        relationship = db.Column(db.String(16))
        invite_token = db.Column(db.String(64))
        accepted_at = db.Column(db.DateTime)
        share_scopes_json = db.Column(db.String(128))
        created_at = db.Column(db.DateTime)

    class Nudge(db.Model):
        __tablename__ = 'family_nudges'
        id = db.Column(db.Integer, primary_key=True)
        link_id = db.Column(db.Integer, db.ForeignKey('student_links.id'))
        template_id = db.Column(db.String(32))
        created_at = db.Column(db.DateTime)
        acknowledged_at = db.Column(db.DateTime)
        withdrawn_at = db.Column(db.DateTime)

    class ManualTask(db.Model):
        __tablename__ = 'manual_tasks'
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer)
        title = db.Column(db.String(255))
        course = db.Column(db.String(255))
        due_date = db.Column(db.String(32))
        estimated_time = db.Column(db.Integer)
        done = db.Column(db.Boolean)
        import_source = db.Column(db.String(64))

    class Dismissed(db.Model):
        __tablename__ = 'dismissed_assignments'
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer)
        title = db.Column(db.String(255))

    class StudyPoints(db.Model):
        __tablename__ = 'study_points'
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer)
        session_history = db.Column(db.Text)

    login = LoginManager(app)
    login.user_loader(lambda user_id: db.session.get(User, int(user_id)))
    app.intelliplan_db = db
    app.intelliplan_user_model = User
    app.intelliplan_student_link_model = Link
    app.intelliplan_family_nudge_model = Nudge
    app.intelliplan_manual_task_model = ManualTask
    app.intelliplan_dismissed_assignment_model = Dismissed
    app.intelliplan_study_points_model = StudyPoints
    app.intelliplan_assignment_fetcher = lambda user_id: [
        {'title': 'Old lab report', 'course': 'Science', 'source': 'canvas',
         'due_date': date.today().isoformat()},
    ] if user_id == 2 else []
    app.intelliplan_norm_title = lambda title: ' '.join((title or '').casefold().split())
    app.register_blueprint(roles_bp)
    with app.app_context():
        db.create_all()
        db.session.add_all([
            User(id=1, email='parent@example.com', name='Parent', role='parent', birth_year=1980),
            User(id=2, email='college@example.edu', name='College', role='student', birth_year=2006),
            User(id=3, email='other@example.com', name='Other', role='parent', birth_year=1980),
            ManualTask(user_id=2, title='Research paper', course='Writing',
                       due_date=date.today().isoformat(), estimated_time=90, done=False),
            ManualTask(user_id=2, title='Read article', course='Writing',
                       due_date=date.today().isoformat(), estimated_time=30, done=True),
            StudyPoints(user_id=2, session_history='[{"date":"' + date.today().isoformat() + '","questions":3}]'),
            Dismissed(user_id=2, title='Old lab report'),
        ])
        db.session.commit()
        from primer import store as primer_store
        learner = primer_store.create_learner(2, 'College', 'space', grade=13)
        primer_store.record_attempt(learner['id'], 'g13r0', 'v2g13r0-0000',
                                    'family-test-attempt', True)
    yield app


def signed_in(app, user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(user_id)
        session['_fresh'] = True
    return client


def test_college_parent_sharing_requires_explicit_student_acceptance(family_app):
    parent, student, other = (signed_in(family_app, uid) for uid in (1, 2, 3))
    assert parent.post('/api/roles/invite', json={'student_email': ['college@example.edu']}).status_code == 400
    assert parent.post('/api/roles/role', json={'role': ['parent']}).status_code == 400
    path = '/api/roles/student/2/overview'
    assert parent.get(path).status_code == 403
    assert other.get(path).status_code == 403
    invited = parent.post('/api/roles/invite', json={'student_email': 'college@example.edu'})
    assert invited.status_code == 200
    link_id = invited.get_json()['link_id']
    assert parent.get(path).status_code == 403
    assert student.get('/api/roles/my-links').get_json()['links'][0]['accepted'] is False
    assert other.post(f'/api/roles/links/{link_id}/accept').status_code == 404
    assert student.post(f'/api/roles/links/{link_id}/accept').status_code == 200
    summary = parent.get(path).get_json()['summary']
    assert summary['status'] == 'ok'
    assert summary['open'] == 1 and summary['completed'] == 1
    assert summary['total_assignments'] == 2  # A dismissed lab is not completed work.
    assert summary['study_days_7d'] == summary['study_sessions_7d'] == 1
    assert summary['foundations'][0]['grade'] == 13
    assert summary['foundations'][0]['answers_7d'] == 1
    assert summary['upcoming'][0]['title'] == 'Research paper'
    assert other.get(path).status_code == 403
    assert student.delete(f'/api/roles/links/{link_id}').status_code == 200
    assert parent.get(path).status_code == 403


def test_encouragement_is_bounded_and_vanishes_after_revoke(family_app):
    parent, student = signed_in(family_app, 1), signed_in(family_app, 2)
    link_id = parent.post('/api/roles/invite', json={'student_email': 'college@example.edu'}).get_json()['link_id']
    assert student.post(f'/api/roles/links/{link_id}/accept').status_code == 200
    endpoint = '/api/roles/student/2/encouragement'
    assert parent.post(endpoint, json={'template_id': ['steady']}).status_code == 400
    first = parent.post(endpoint, json={'template_id': 'steady'})
    assert first.status_code == 201
    note_id = first.get_json()['note']['id']
    assert parent.post(endpoint, json={'template_id': 'plan'}).status_code == 429
    assert len(student.get('/api/roles/my-encouragement').get_json()['notes']) == 1
    assert student.post(f'/api/roles/my-encouragement/{note_id}/acknowledge').status_code == 200
    assert student.get('/api/roles/my-encouragement').get_json()['notes'] == []
    assert parent.get(endpoint).get_json()['latest']['acknowledged_at']
    assert student.delete(f'/api/roles/links/{link_id}').status_code == 200
    assert parent.get(endpoint).status_code == 403


@pytest.mark.parametrize('relationship', ['parent', 'teacher'])
def test_shared_week_counts_only_recent_records_and_respects_consent(family_app, relationship):
    with family_app.app_context():
        family_app.intelliplan_db.session.get(family_app.intelliplan_user_model, 1).role = relationship
        points = family_app.intelliplan_study_points_model.query.filter_by(user_id=2).one()
        today = date.today()
        points.session_history = json.dumps([
            {'date': today.isoformat(), 'answer': 'private answer'},
            {'date': today.isoformat()},
            {'date': (today - timedelta(days=6)).isoformat()},
            {'date': (today - timedelta(days=7)).isoformat()},
            {'date': (today + timedelta(days=1)).isoformat()},
            {'date': 'bad-date'}, None,
        ])
        family_app.intelliplan_db.session.commit()
    adult, student = signed_in(family_app, 1), signed_in(family_app, 2)
    link = adult.post('/api/roles/invite', json={'student_email': 'college@example.edu'}).get_json()['link_id']
    assert student.post(f'/api/roles/links/{link}/accept', json={'scopes': ['study', 'work']}).status_code == 200
    summary = adult.get('/api/roles/student/2/overview').get_json()['summary']
    week = summary['study_week']
    assert [day['date'] for day in week] == [(today - timedelta(days=n)).isoformat() for n in range(6, -1, -1)]
    assert [day['sessions'] for day in week] == [1, 0, 0, 0, 0, 0, 2]
    assert summary['study_sessions_7d'] == 3 and summary['study_days_7d'] == 2
    assert 'private answer' not in json.dumps(summary)
    assert student.patch(f'/api/roles/links/{link}/scopes', json={'scopes': ['work']}).status_code == 200
    private = adult.get('/api/roles/student/2/overview').get_json()['summary']
    assert private['study_week'] is None and private['study_status'] == 'not_shared'
    assert student.delete(f'/api/roles/links/{link}').status_code == 200
    assert adult.get('/api/roles/student/2/overview').status_code == 403


@pytest.mark.parametrize('history', ['{invalid', '{}', 'null', '"bad"'])
def test_invalid_study_record_is_unavailable_without_hiding_work(family_app, history):
    with family_app.app_context():
        family_app.intelliplan_study_points_model.query.filter_by(user_id=2).one().session_history = history
        family_app.intelliplan_db.session.commit()
    parent, student = signed_in(family_app, 1), signed_in(family_app, 2)
    link = parent.post('/api/roles/invite', json={'student_email': 'college@example.edu'}).get_json()['link_id']
    assert student.post(f'/api/roles/links/{link}/accept').status_code == 200
    summary = parent.get('/api/roles/student/2/overview').get_json()['summary']
    assert summary['study_status'] == 'unavailable' and summary['study_week'] is None
    assert summary['open'] == summary['completed'] == 1


def test_student_can_limit_and_change_parent_sharing(family_app):
    parent, student, other = (signed_in(family_app, uid) for uid in (1, 2, 3))
    link_id = parent.post('/api/roles/invite', json={
        'student_email': 'college@example.edu'}).get_json()['link_id']
    approval = f'/api/roles/links/{link_id}/accept'
    scope_path = f'/api/roles/links/{link_id}/scopes'
    assert student.post(approval, json={'scopes': []}).status_code == 400
    assert student.post(approval, json={'scopes': ['private']}).status_code == 400
    assert parent.get('/api/roles/student/2/overview').status_code == 403
    assert student.post(approval, json={'scopes': ['study']}).status_code == 200
    summary = parent.get('/api/roles/student/2/overview').get_json()['summary']
    assert summary['scopes'] == ['study']
    assert summary['study_days_7d'] == 1
    assert summary['open'] is None and summary['upcoming'] is None
    assert summary['foundations'] is None
    assert summary['work_status'] == 'not_shared'
    assert other.patch(scope_path, json={'scopes': ['work']}).status_code == 404
    assert parent.patch(scope_path, json={'scopes': ['work']}).status_code == 404
    assert student.patch(scope_path, json={'scopes': []}).status_code == 400
    assert student.patch(scope_path, json={'scopes': ['work', 'foundations']}).status_code == 200
    summary = parent.get('/api/roles/student/2/overview').get_json()['summary']
    assert summary['open'] == 1
    assert summary['foundations'][0]['answers_7d'] == 1
    assert summary['study_days_7d'] is None
    assert summary['study_status'] == 'not_shared'
    assert summary['study_week'] is None
    assert student.get('/api/roles/my-links').get_json()['links'][0]['scopes'] == ['foundations', 'work']


def test_work_failure_does_not_hide_recorded_study(family_app, monkeypatch):
    import intelliplan.api.roles as roles

    parent, student = signed_in(family_app, 1), signed_in(family_app, 2)
    link_id = parent.post('/api/roles/invite', json={
        'student_email': 'college@example.edu'}).get_json()['link_id']
    assert student.post(f'/api/roles/links/{link_id}/accept').status_code == 200

    def fail_work(*_args):
        raise RuntimeError('upstream work unavailable')

    monkeypatch.setattr(roles.AssignmentRepository, 'for_user', fail_work)
    summary = parent.get('/api/roles/student/2/overview').get_json()['summary']
    assert summary['status'] == 'ok'
    assert summary['work_status'] == 'unavailable'
    assert summary['open'] is None and summary['upcoming'] is None
    assert summary['study_days_7d'] == 1
    assert summary['foundations'][0]['answers_7d'] == 1


def test_parent_hostname_has_own_entry_and_blocks_student_routes():
    import App

    App.app.config['TESTING'] = True
    with App.app.test_client() as client:
        root = client.get('/', base_url='https://parent.intelliplan.tech')
        assert root.status_code == 302
        assert root.headers['Location'].endswith('/login/account')
        assert client.get('/command-center', base_url='https://parent.intelliplan.tech').status_code == 404
