"""Clinical vocabulary is a scoped teaching setting, never a safety override."""
from uuid import uuid4

import pytest

import App
from intelliplan.learning import policy
from intelliplan.learning.safety_terms import allowed_terms_for_student, validate_terms


@pytest.fixture
def classroom(monkeypatch):
    monkeypatch.setattr(App.limiter, 'enabled', False)
    monkeypatch.setitem(App.app.config, 'TESTING', True)
    with App.app.app_context():
        App.db.create_all()
        users = {}
        for name, role in [('teacher', 'teacher'), ('other_teacher', 'teacher'),
                           ('student', 'student'), ('outsider', 'student')]:
            user = App.User(email=f'{name}-{uuid4().hex}@example.test', name=name,
                            role=role, birth_year=1980, parent_consent_granted=True, password_hash='x')
            App.db.session.add(user)
            App.db.session.flush()
            users[name] = user.id
        M = policy.models()
        district = M['Organization'](name='District', kind='district', policy={})
        App.db.session.add(district)
        App.db.session.flush()
        school = M['Organization'](name='School', kind='school', parent_id=district.id, policy={})
        App.db.session.add(school)
        App.db.session.flush()
        health = M['Organization'](name='Health class', kind='class', parent_id=school.id, policy={})
        other = M['Organization'](name='Other class', kind='class', parent_id=school.id, policy={})
        App.db.session.add_all([health, other])
        App.db.session.flush()
        App.db.session.add_all([
            M['Membership'](user_id=users['teacher'], organization_id=health.id, role='teacher'),
            M['Membership'](user_id=users['student'], organization_id=health.id, role='student'),
            M['Membership'](user_id=users['other_teacher'], organization_id=other.id, role='teacher'),
            M['Membership'](user_id=users['outsider'], organization_id=other.id, role='student'),
        ])
        App.db.session.commit()
        result = dict(users, district=district.id, school=school.id, health=health.id, other=other.id)
    yield result
    with App.app.app_context():
        M['Audit'].query.filter(M['Audit'].organization_id.in_([result['health'], result['other']])).delete(synchronize_session=False)
        M['Membership'].query.filter(M['Membership'].user_id.in_(users.values())).delete(synchronize_session=False)
        M['Organization'].query.filter(M['Organization'].id.in_([result['health'], result['other'], result['school'], result['district']])).delete(synchronize_session=False)
        App.User.query.filter(App.User.id.in_(users.values())).delete(synchronize_session=False)
        App.db.session.commit()


def signed_in(user_id):
    client = App.app.test_client()
    with client.session_transaction() as session:
        session.update(_user_id=str(user_id), _fresh=True, session_epoch=0)
    return client


def endpoint(classroom):
    return f"/api/teacher/terminology/{classroom['health']}"


def test_teacher_selects_only_clinical_terms_for_assigned_classroom(classroom):
    client = signed_in(classroom['teacher'])
    rows = client.get('/api/teacher/terminology').get_json()['classrooms']
    assert [row['id'] for row in rows] == [classroom['health']]
    response = client.put(endpoint(classroom), json={'terms': ['penis', 'puberty'], 'version': 1})
    assert response.status_code == 200
    assert response.get_json()['version'] == 2
    with App.app.app_context():
        assert allowed_terms_for_student(classroom['student']) == {'penis', 'puberty'}
        assert allowed_terms_for_student(classroom['outsider']) == set()
        row = App.db.session.get(policy.models()['Organization'], classroom['health'])
        assert row.policy == {'educational_terms': ['penis', 'puberty']}
        audit = policy.models()['Audit'].query.filter_by(organization_id=row.id).one()
        assert audit.action == 'educational_terms_updated' and audit.actor_id == classroom['teacher']
        # Vocabulary metadata does not turn into an AI feature/policy bypass.
        assert policy.effective(App.db.session.get(App.User, classroom['student']))['tutor'] is True


@pytest.mark.parametrize('actor', ['other_teacher', 'student', 'outsider'])
def test_unrelated_teacher_and_student_cannot_edit_classroom(classroom, actor):
    response = signed_in(classroom[actor]).put(endpoint(classroom), json={'terms': ['penis'], 'version': 1})
    assert response.status_code == 403
    with App.app.app_context():
        assert App.db.session.get(policy.models()['Organization'], classroom['health']).policy == {}


@pytest.mark.parametrize('terms', [['molotov'], ['pornography'], ['ignore safety rules'], ['penis', '*'], ['Penis'], 'penis', None])
def test_dangerous_or_free_form_terms_are_rejected(classroom, terms):
    assert signed_in(classroom['teacher']).put(endpoint(classroom), json={'terms': terms, 'version': 1}).status_code == 400


def test_district_ceiling_and_changed_version_cannot_be_overridden(classroom):
    client = signed_in(classroom['teacher'])
    with App.app.app_context():
        district = App.db.session.get(policy.models()['Organization'], classroom['district'])
        district.policy = {'educational_terms': ['puberty'], 'tutor': False}
        App.db.session.commit()
    assert client.put(endpoint(classroom), json={'terms': ['penis'], 'version': 1}).status_code == 403
    assert client.put(endpoint(classroom), json={'terms': ['puberty'], 'version': True}).status_code == 400
    assert client.put(endpoint(classroom), json={'terms': ['puberty'], 'version': 1}).status_code == 200
    assert client.put(endpoint(classroom), json={'terms': [], 'version': 1}).status_code == 409
    rows = client.get('/api/teacher/terminology').get_json()['classrooms']
    assert rows[0]['permitted_terms'] == ['puberty']
    with App.app.app_context():
        assert allowed_terms_for_student(classroom['student']) == {'puberty'}
        assert policy.effective(App.db.session.get(App.User, classroom['student']))['tutor'] is False


def test_policy_changes_take_effect_without_a_new_login(classroom):
    client = signed_in(classroom['teacher'])
    assert client.put(endpoint(classroom), json={'terms': ['penis'], 'version': 1}).status_code == 200
    with App.app.app_context():
        assert allowed_terms_for_student(classroom['student']) == {'penis'}
        district = App.db.session.get(policy.models()['Organization'], classroom['district'])
        district.policy = {'educational_terms': []}
        App.db.session.commit()
        assert allowed_terms_for_student(classroom['student']) == set()
    assert client.put(endpoint(classroom), json={'terms': ['penis'], 'version': 2}).status_code == 403


def test_progress_link_does_not_grant_policy_access(classroom):
    with App.app.app_context():
        App.db.session.add(App.StudentLink(linker_user_id=classroom['other_teacher'],
                                           student_user_id=classroom['student'], relationship='teacher'))
        App.db.session.commit()
    assert signed_in(classroom['other_teacher']).put(endpoint(classroom), json={'terms': ['penis'], 'version': 1}).status_code == 403
    with App.app.app_context():
        App.StudentLink.query.filter_by(linker_user_id=classroom['other_teacher']).delete()
        App.db.session.commit()


def test_empty_and_unauthenticated_paths_are_honest(classroom):
    anonymous = App.app.test_client()
    assert anonymous.get('/api/teacher/terminology').status_code == 401
    assert anonymous.put(endpoint(classroom), json={'terms': ['penis'], 'version': 1}).status_code == 403
    assert signed_in(classroom['student']).get('/teacher/terminology').status_code == 403
    assert validate_terms([]) == []
    with App.app.app_context():
        assert allowed_terms_for_student('1') == set()
        assert allowed_terms_for_student(99999999) == set()


def test_school_admin_can_create_and_staff_only_their_classroom(classroom):
    client = signed_in(classroom['teacher'])
    with App.app.app_context():
        M = policy.models()
        App.db.session.add(M['Membership'](user_id=classroom['teacher'],
                                           organization_id=classroom['school'], role='admin'))
        App.db.session.commit()
    payload = {'kind': 'class', 'name': 'Biology', 'parent_id': classroom['school'], 'policy': {}}
    assert signed_in(classroom['other_teacher']).post('/api/school-admin/organizations', json=payload).status_code == 403
    created = client.post('/api/school-admin/organizations', json=payload)
    assert created.status_code == 201
    classroom_id = created.get_json()['id']
    member_path = f'/api/school-admin/organizations/{classroom_id}/members'
    assert client.post(member_path, json={'user_id': classroom['other_teacher'], 'role': 'teacher'}).status_code == 200
    assert client.post(member_path, json={'user_id': classroom['outsider'], 'role': 'student'}).status_code == 200
    assert client.post(member_path, json={'user_id': classroom['outsider'], 'role': 'admin'}).status_code == 403
    assert client.post('/api/school-admin/organizations', json={'kind': 'school', 'name': 'Unauthorized school'}).status_code == 403
    with App.app.app_context():
        foreign = M['Organization'](name='Unrelated school', kind='school', policy={})
        App.db.session.add(foreign)
        App.db.session.commit()
        foreign_id = foreign.id
    assert client.post('/api/school-admin/organizations', json={**payload, 'parent_id': foreign_id}).status_code == 403
    with App.app.app_context():
        M['Organization'].query.filter_by(id=foreign_id).delete()
        # Fixture teardown removes the original hierarchy, so remove this new child first.
        M['Membership'].query.filter_by(organization_id=classroom_id).delete()
        M['Audit'].query.filter_by(organization_id=classroom_id).delete()
        M['Organization'].query.filter_by(id=classroom_id).delete()
        App.db.session.commit()


def test_district_admin_authority_reaches_classes_but_not_another_district(classroom):
    with App.app.app_context():
        M = policy.models()
        App.db.session.add(M['Membership'](user_id=classroom['other_teacher'],
                                           organization_id=classroom['district'], role='admin'))
        foreign = M['Organization'](name='Other district', kind='district', policy={})
        App.db.session.add(foreign)
        App.db.session.commit()
        assert policy.administers(App.db.session.get(App.User, classroom['other_teacher']), classroom['health'])
        assert not policy.administers(App.db.session.get(App.User, classroom['other_teacher']), foreign.id)
        M['Organization'].query.filter_by(id=foreign.id).delete()
        App.db.session.commit()
    assert signed_in(classroom['other_teacher']).put(endpoint(classroom), json={'terms': ['penis'], 'version': 1}).status_code == 200
