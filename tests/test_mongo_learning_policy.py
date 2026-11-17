"""Native school restrictions stay effective across cached requests and beta flags."""
from flask import Flask
import json
import mongomock
import pytest

import premium_glue
from intelliplan.learning import policy
from intelliplan.persistence.mongo import MongoStore
from intelliplan.persistence.mongo_accounts import MongoAccounts
from intelliplan.persistence.mongo_learning_policy import MongoLearningPolicy
from intelliplan.persistence.codec import update_fields


@pytest.fixture
def environment():
    app = Flask(__name__)
    client = mongomock.MongoClient()
    store = MongoStore('mongodb://test', 'policy', client_factory=lambda *a, **k: client)
    store.transaction = lambda work: work(store.database, None)
    store.database.users.insert_many([{'id': 1, 'plan_tier': 'premium'}, {'id': 2}])
    store.database.learning_organizations.insert_many([
        {'id': 10, 'parent_id': None, 'policy': {'providers': ['claude', 'groq'], 'max_generation_tokens': 8000}},
        {'id': 11, 'parent_id': 10, 'policy': {'providers': ['groq'], 'notifications': False}},
        {'id': 12, 'parent_id': None, 'policy': {'tutor': False}},
    ])
    store.database.learning_memberships.insert_one({'id': 1, 'user_id': 1, 'organization_id': 11, 'role': 'student'})
    repo = MongoLearningPolicy(store)
    repo.ensure_indexes()
    app.extensions['mongo_store'] = store
    return app, store, repo, MongoAccounts(store).get(1)


def test_native_policy_intersects_parent_and_child_without_sql(environment):
    app, store, repo, user = environment
    with app.test_request_context():
        effective = policy.effective(user)
        assert effective['providers'] == {'groq'}
        assert effective['max_generation_tokens'] == 8000
        assert policy.decision(user, 'notifications') == (False, 'institution_policy')
        assert policy.decision(user, 'tutor') == (True, 'allowed')
        assert policy.permitted_providers(user) == {'groq'}
    assert 'sqlalchemy' not in app.extensions


@pytest.mark.parametrize('compressed', [False, True])
def test_restored_json_text_policies_preserve_restrictions_and_beta_eligibility(environment, compressed):
    app, store, repo, user = environment
    for raw in list(store.database.learning_organizations.find({})):
        text = json.dumps(raw['policy']) + (' ' * 10000 if compressed else '')
        store.database.learning_organizations.update_one({'id': raw['id']}, update_fields({'policy': text}))
    if compressed:
        assert store.database.learning_organizations.find_one({'id': 10}).get('_ip_encoding')
    store.database.feature_flags.insert_one({'id': 1, 'key': 'school_practice_preview', 'enabled': True})
    store.database.learning_beta_enrollments.insert_one({'id': 1, 'user_id': 1, 'feature': 'practice_preview',
        'organization_id': 11, 'approved': True, 'consent_version': policy.BETA_NOTICE_VERSION,
        'accepted_at': '2026-10-18T00:00:00'})
    with app.app_context():
        assert policy.decision(user, 'tutor') == (True, 'allowed')
        assert policy.decision(user, 'practice_preview') == (True, 'allowed')
        assert policy.decision(user, 'notifications') == (False, 'institution_policy')
        assert policy.permitted_providers(user) == {'groq'}


def test_native_beta_requires_flag_approval_membership_and_versioned_consent(environment):
    app, store, repo, user = environment
    with app.app_context():
        assert policy.decision(user, 'practice_preview')[1] == 'feature_disabled'
        store.database.feature_flags.insert_one({'id': 1, 'key': 'school_practice_preview', 'enabled': True})
        assert policy.decision(user, 'practice_preview')[1] == 'beta_approval_required'
        enrollment = {'id': 1, 'user_id': 1, 'feature': 'practice_preview', 'organization_id': 11,
                      'approved': True, 'consent_version': 'old', 'accepted_at': '2026-10-18T00:00:00'}
        store.database.learning_beta_enrollments.insert_one(enrollment)
        assert policy.decision(user, 'practice_preview')[1] == 'beta_consent_required'
        store.database.learning_beta_enrollments.update_one({'id': 1}, {'$set': {'consent_version': policy.BETA_NOTICE_VERSION}})
        assert policy.decision(user, 'practice_preview') == (True, 'allowed')
        store.database.learning_memberships.delete_one({'id': 1})
        assert policy.decision(user, 'practice_preview')[1] == 'beta_approval_required'


@pytest.mark.parametrize('field,value', [('enabled', 'false'), ('enabled', {'value': True}),
    ('approved', 'false'), ('approved', 1), ('accepted_at', True), ('accepted_at', {'date': 'today'})])
def test_malformed_native_flag_or_beta_evidence_cannot_authorize(environment, field, value):
    app, store, repo, user = environment
    store.database.feature_flags.insert_one({'id': 1, 'key': 'school_practice_preview',
        'enabled': value if field == 'enabled' else True})
    store.database.learning_beta_enrollments.insert_one({'id': 1, 'user_id': 1, 'feature': 'practice_preview',
        'organization_id': 11, 'approved': value if field == 'approved' else True,
        'consent_version': policy.BETA_NOTICE_VERSION,
        'accepted_at': value if field == 'accepted_at' else '2026-10-18T00:00:00'})
    with app.app_context():
        assert policy.decision(user, 'practice_preview') == (False, 'policy_unavailable')



@pytest.mark.parametrize('failure', ['missing_parent', 'missing_child', 'cycle', 'malformed', 'deleted_user'])
def test_native_unavailable_policy_fails_closed(environment, failure):
    app, store, repo, user = environment
    if failure == 'missing_parent':
        store.database.learning_organizations.delete_one({'id': 10})
    elif failure == 'missing_child':
        store.database.learning_organizations.delete_one({'id': 11})
    elif failure == 'cycle':
        store.database.learning_organizations.update_one({'id': 10}, {'$set': {'parent_id': 11}})
    elif failure == 'malformed':
        store.database.learning_organizations.update_one({'id': 10}, {'$set': {'policy': {'tutor': 'yes'}}})
    else:
        store.database.users.delete_one({'id': 1})
    with app.app_context():
        assert policy.decision(user, 'tutor') == (False, 'policy_unavailable')


def test_native_admin_scope_requires_explicit_authority(environment):
    app, store, repo, user = environment
    with app.app_context():
        assert not policy.is_learning_admin(user)
        assert not policy.administers(user, 11)
        store.database.learning_memberships.update_one({'id': 1}, {'$set': {'role': 'admin'}})
        assert policy.is_learning_admin(user)
        assert policy.administers(user, 11)
        assert not policy.administers(user, 12)
        store.database.learning_memberships.delete_one({'id': 1})
        app.extensions['admin_checker'] = lambda candidate: candidate.id == 1
        assert policy.administers(user, 12)
        assert policy.is_learning_admin(user)
        assert policy.decision(user, 'video_generation')[1] == 'feature_disabled'
        store.database.feature_flags.insert_one({'id': 2, 'key': 'school_video_generation', 'enabled': True})
        assert policy.decision(user, 'video_generation')[1] == 'provider_not_integrated'


def test_tutor_permission_reloads_policy_and_account_within_request(environment):
    app, store, repo, user = environment
    with app.test_request_context():
        owner, first = premium_glue._tutor_permission(1)
        assert owner.id == 1 and first
        store.database.learning_organizations.update_one({'id': 10}, {'$set': {'policy': {'tutor': False}}})
        assert not premium_glue._tutor_permission(1)[1]
        store.database.users.delete_one({'id': 1})
        assert premium_glue._tutor_permission(1)[0] is None
