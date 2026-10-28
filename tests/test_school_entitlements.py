"""Verified domain enrollment, owner-only plans, and signup trial boundaries."""

from datetime import datetime, timedelta
from uuid import uuid4

import App
import growth_glue
from intelliplan.learning import entitlements
from intelliplan.learning import policy
from intelliplan.learning.entitlements import add_verified_school_membership


def _user(email=None, role='student', verified=False):
    row = App.User(email=email or f"school-{uuid4().hex}@example.test", password_hash='x', birth_year=2000)
    row.role = role
    row.email_verified = verified
    row.parent_consent_granted = True
    App.db.session.add(row)
    App.db.session.flush()
    return row


def _org(name='Northview', kind='school', parent_id=None):
    M = policy.models()
    row = M['Organization'](name=name, kind=kind, parent_id=parent_id, policy={})
    App.db.session.add(row)
    App.db.session.flush()
    return row


def _login(client, uid):
    with client.session_transaction() as session:
        session['_user_id'] = str(uid)
        session['_fresh'] = True


def test_only_explicitly_verified_email_gets_domain_membership():
    with App.app.app_context():
        App.db.create_all()
        M = policy.models()
        org = _org()
        test_domain = f"school-{uuid4().hex}.test"
        domain = M['SchoolDomain'](organization_id=org.id, domain=test_domain, verification_hash='x', verified_at=datetime.utcnow(), created_by_id=1)
        App.db.session.add(domain)
        App.db.session.flush()
        unverified = _user(f'student@{test_domain}', verified=False)
        assert add_verified_school_membership(unverified, unverified.email) is False
        assert M['Membership'].query.filter_by(user_id=unverified.id).count() == 0
        verified = _user(f'teacher@{test_domain}', role='teacher', verified=True)
        assert add_verified_school_membership(verified, verified.email) is True
        assert M['Membership'].query.filter_by(user_id=verified.id, organization_id=org.id, role='teacher').count() == 1
        assert add_verified_school_membership(verified, verified.email) is False
        App.db.session.rollback()


def test_domain_and_plan_mutation_are_platform_owner_only(monkeypatch):
    previous = App.limiter.enabled
    App.limiter.enabled = False
    App.app.config['TESTING'] = True
    with App.app.app_context():
        App.db.create_all()
        M = policy.models()
        school = _org()
        admin = _user()
        App.db.session.add(M['Membership'](user_id=admin.id, organization_id=school.id, role='admin'))
        App.db.session.commit()
        admin_id = admin.id
        org_id = school.id
        owner = _user(f"owner-{uuid4().hex}@example.test")
        App.db.session.commit()
        owner_id, owner_email = owner.id, owner.email
    try:
        monkeypatch.setattr(App, 'ADMIN_EMAILS', frozenset({owner_email}))
        client = App.app.test_client()
        _login(client, admin_id)
        denied = client.get('/owner/schools', headers={'Referer': 'http://localhost/dashboard'})
        assert denied.status_code == 404, denied.headers.get('Location')
        assert client.post(f'/api/owner/schools/{org_id}/domains', json={'domain': 'example.edu'}, headers={'Referer': 'http://localhost/dashboard'}).status_code == 404
        assert client.put(f'/api/owner/schools/{org_id}/plan', json={'plan_tier': 'premium'}, headers={'Referer': 'http://localhost/dashboard'}).status_code == 404
        _login(client, owner_id)
        domain_name = f"example-{uuid4().hex}.edu"
        created = client.post(f'/api/owner/schools/{org_id}/domains', json={'domain': f' {domain_name.upper()}. '})
        assert created.status_code == 201, created.get_json()
        payload = created.get_json()
        assert payload['txt_name'] == f'_intelliplan.{domain_name}'
        assert client.put(f'/api/owner/schools/{org_id}/plan', json={'plan_tier': 'premium'}).status_code == 200
        with App.app.app_context():
            assert policy.models()['SchoolPlan'].query.filter_by(organization_id=org_id).one().plan_tier == 'premium'
            assert policy.models()['Audit'].query.filter_by(organization_id=org_id).count() >= 2
    finally:
        App.limiter.enabled = previous


def test_owner_school_list_distinguishes_direct_and_effective_district_plan(monkeypatch):
    previous = App.limiter.enabled
    App.limiter.enabled = False
    App.app.config['TESTING'] = True
    with App.app.app_context():
        App.db.create_all()
        M = policy.models()
        district = _org('North County', 'district')
        school = _org('Northview High', parent_id=district.id)
        owner = _user(f"owner-{uuid4().hex}@example.test")
        district_plan = M['SchoolPlan'].query.filter_by(organization_id=district.id).first()
        if district_plan is None:
            district_plan = M['SchoolPlan'](organization_id=district.id,
                plan_tier='pro', updated_by_id=owner.id)
            App.db.session.add(district_plan)
        else:
            district_plan.plan_tier = 'pro'
            district_plan.updated_by_id = owner.id
        App.db.session.commit()
        owner_id, owner_email, school_id = owner.id, owner.email, school.id
    try:
        monkeypatch.setattr(App, 'ADMIN_EMAILS', frozenset({owner_email}))
        client = App.app.test_client()
        _login(client, owner_id)
        response = client.get('/api/owner/schools')
        assert response.status_code == 200
        listed = next(row for row in response.json['schools'] if row['id'] == school_id)
        assert listed['plan_tier'] == 'free'
        assert listed['plan_configured'] is False
        assert listed['effective_plan_tier'] == 'pro'
        assert listed['effective_plan_source_name'] == 'North County'
        assert listed['effective_plan_inherited'] is True
    finally:
        App.limiter.enabled = previous


def test_domain_verification_enrolls_already_verified_users_once(monkeypatch):
    previous = App.limiter.enabled
    App.limiter.enabled = False
    App.app.config['TESTING'] = True
    domain_name = f"verified-{uuid4().hex}.example.test"
    with App.app.app_context():
        App.db.create_all()
        M = policy.models()
        school = _org('Verified Domain School')
        owner = _user(f"owner-{uuid4().hex}@example.test")
        verified_student = _user(f"student@{domain_name}", verified=True)
        verified_teacher = _user(f"teacher@{domain_name}", role='teacher', verified=True)
        unverified_student = _user(f"unverified@{domain_name}", verified=False)
        domain = M['SchoolDomain'](organization_id=school.id, domain=domain_name,
            verification_hash='proof-hash', created_by_id=owner.id)
        App.db.session.add(domain)
        App.db.session.commit()
        owner_id, owner_email, school_id, domain_id = owner.id, owner.email, school.id, domain.id
        verified_roles = {verified_student.id: 'student', verified_teacher.id: 'teacher'}
        unverified_id = unverified_student.id
    try:
        monkeypatch.setattr(App, 'ADMIN_EMAILS', frozenset({owner_email}))
        monkeypatch.setattr(entitlements, '_dns_txt_contains', lambda domain, token: True)
        client = App.app.test_client()
        _login(client, owner_id)
        response = client.post(f'/api/owner/schools/{school_id}/domains/{domain_id}/verify')
        assert response.status_code == 200, response.get_json()
        assert response.json['enrolled_existing_members'] == 2
        with App.app.app_context():
            M = policy.models()
            memberships = M['Membership'].query.filter_by(organization_id=school_id).all()
            assert {row.user_id: row.role for row in memberships} == verified_roles
            assert unverified_id not in {row.user_id for row in memberships}
            audit_count = M['Audit'].query.filter_by(
                organization_id=school_id, action='verified_domain_member_added').count()
        repeated = client.post(f'/api/owner/schools/{school_id}/domains/{domain_id}/verify')
        assert repeated.status_code == 200
        with App.app.app_context():
            M = policy.models()
            assert M['Membership'].query.filter_by(organization_id=school_id).count() == 2
            assert M['Audit'].query.filter_by(organization_id=school_id,
                action='verified_domain_member_added').count() == audit_count
    finally:
        App.limiter.enabled = previous


def test_trial_requires_config_is_one_time_and_skips_partner_members(monkeypatch):
    now = datetime(2026, 10, 26, 12)
    with App.app.app_context():
        App.db.create_all()
        M = policy.models()
        school = _org()
        App.db.session.add(M['SchoolPlan'](organization_id=school.id, plan_tier='pro', updated_by_id=1))
        partner = _user()
        App.db.session.add(M['Membership'](user_id=partner.id, organization_id=school.id, role='student'))
        individual = _user()
        App.db.session.commit()
        partner_id, individual_id = partner.id, individual.id
    monkeypatch.setenv('BILLING_ENABLED', '1')
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'fixture-only-no-network')
    monkeypatch.setenv('BILLING_TRIAL_DAYS', '14')
    monkeypatch.setenv('STRIPE_PRICE_ID', 'price_pro_test')
    monkeypatch.setattr(growth_glue, '_stripe', lambda interval, tier: (object(), 'price_pro_test'))
    with App.app.app_context():
        partner = App.db.session.get(App.User, partner_id)
        individual = App.db.session.get(App.User, individual_id)
        assert growth_glue.grant_signup_trial(partner, now) is False
        assert partner.trial_started_at is None
        assert growth_glue.grant_signup_trial(individual, now) is True
        App.db.session.commit()
        assert individual.trial_started_at == now
        assert individual.paid_until == now + timedelta(days=14)
        individual.paid_until = None
        App.db.session.commit()
        assert growth_glue.grant_signup_trial(individual, now + timedelta(days=30)) is False
        assert individual.trial_started_at == now
