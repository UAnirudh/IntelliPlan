"""Native erasure policy, ownership isolation and retained shared content."""
import json
from decimal import Decimal

import mongomock
import pytest

from intelliplan.persistence.mongo import MongoStore
from intelliplan.persistence.mongo_erasure import (
    ANONYMIZED_REFERENCES, DIRECT_OTHER_OWNERS, DIRECT_USER_COLLECTIONS, MongoErasure,
)


@pytest.fixture
def backend():
    client = mongomock.MongoClient()
    store = MongoStore('mongodb://test', 'erasure', client_factory=lambda *a, **k: client)
    store.transaction = lambda work: work(store.database, None)
    store.database.users.insert_many([
        {'id': 1, 'parent_consent_token': 'pending-test', 'parent_consent_granted': False},
        {'id': 2, 'referred_by_id': 1},
    ])
    return store


def test_erasure_removes_all_owned_domains_but_preserves_other_users_and_guests(backend):
    db = backend.database
    for name in DIRECT_USER_COLLECTIONS:
        db[name].insert_many([{'id': 101, 'user_id': 1}, {'id': 102, 'user_id': 2},
                              {'id': 103, 'user_id': None, 'guest_session_id': 'guest'}])
    for name, fields in DIRECT_OTHER_OWNERS.items():
        db[name].insert_many([{'id': 201, fields[0]: 1}, {'id': 202, fields[0]: 2}])
    for name in ('adaptive_session_summary', 'adaptive_memory_import', 'adaptive_learner_memory',
                 'adaptive_mistake_pattern', 'adaptive_subject_mastery'):
        db[name].insert_many([{'id': 301, 'profile_id': 101}, {'id': 302, 'profile_id': 102}])
    for name in ('primer_hint_used', 'primer_attempt', 'primer_skill_state', 'primer_journey',
                 'primer_nudge', 'primer_offline_checkin', 'primer_parent_setting', 'primer_learning_profile'):
        db[name].insert_many([{'id': 301, 'learner_id': 201}, {'id': 302, 'learner_id': 202}])
    for name, field, parent, other in (
        ('learning_generation_jobs', 'course_id', 101, 102), ('learning_lessons', 'course_id', 101, 102),
        ('active_focus_samples', 'session_id', 101, 102), ('family_nudges', 'link_id', 201, 202),
    ):
        db[name].insert_many([{'id': 301, field: parent}, {'id': 302, field: other}])
    db.feature_request_votes.insert_one({'id': 104, 'user_id': 2, 'request_id': 101})
    db.study_groups.insert_one({'id': 501, 'owner_id': 1, 'name': 'Shared workspace'})
    db.study_group_tasks.insert_many([
        {'id': 401, 'created_by_user_id': 1},
        {'id': 402, 'created_by_user_id': 2, 'claimed_by_user_id': 1, 'title': 'Retain'},
    ])
    db.school_outreach_leads.insert_one({'id': 1, 'user_id': 1,
        'student_email': 'synthetic@example.invalid', 'it_contact_email': 'school@example.invalid'})
    db.manual_entry_reasons.insert_one({'id': 1, 'user_id': 1, 'reason': 'School has no API'})
    db.learning_policy_audit.insert_one({'id': 1, 'actor_id': 1,
        'changes': {'target_user_ids': [1, 2], 'nested': {'user_id': '1'}, 'retained': True}})
    db.sessions.insert_many([
        {'session_id': 'indexed-own', 'user_id': 1, 'data': b'not needed'},
        {'session_id': 'indexed-other', 'user_id': 2, 'data': b'not needed'},
        {'session_id': 'legacy-own', 'data': json.dumps({'_user_id': '1'}).encode()},
        {'session_id': 'legacy-other', 'data': json.dumps({'_user_id': '2'}).encode()},
    ])
    assert MongoErasure(backend, session_decoder=json.loads).erase(1)
    assert db.users.find_one({'id': 1}) is None
    assert db.users.find_one({'id': 2})['referred_by_id'] is None
    for name in DIRECT_USER_COLLECTIONS:
        assert db[name].count_documents({'user_id': 1}) == 0, name
        assert db[name].find_one({'id': 102}) is not None, name
        assert db[name].find_one({'id': 103}) is not None, name
    for name in DIRECT_OTHER_OWNERS:
        assert db[name].find_one({'id': 201}) is None, name
        assert db[name].find_one({'id': 202}) is not None, name
    assert db.adaptive_subject_mastery.find_one({'id': 301}) is None
    assert db.adaptive_subject_mastery.find_one({'id': 302}) is not None
    assert db.primer_attempt.find_one({'id': 301}) is None
    assert db.primer_attempt.find_one({'id': 302}) is not None
    assert db.feature_request_votes.find_one({'id': 104}) is None
    assert db.study_groups.find_one({'id': 501})['owner_id'] is None
    assert db.study_group_tasks.find_one({'id': 401}) is None
    assert db.study_group_tasks.find_one({'id': 402})['claimed_by_user_id'] is None
    assert db.school_outreach_leads.find_one({'id': 1})['student_email'] == ''
    assert db.school_outreach_leads.find_one({'id': 1})['it_contact_email'] == 'school@example.invalid'
    assert db.manual_entry_reasons.find_one({'id': 1})['user_id'] is None
    audit = db.learning_policy_audit.find_one({'id': 1})
    assert audit['actor_id'] is None
    assert audit['changes']['target_user_ids'] == [2]
    assert audit['changes']['nested'] == {'deleted_account': True}
    assert db.sessions.find_one({'session_id': 'indexed-own'}) is None
    assert db.sessions.find_one({'session_id': 'legacy-own'}) is None
    assert db.sessions.find_one({'session_id': 'indexed-other'}) is not None
    assert db.sessions.find_one({'session_id': 'legacy-other'}) is not None


def test_erasure_rechecks_staged_file_names_and_pending_approval(backend):
    erasure = MongoErasure(backend)
    expected = erasure.upload_names(1)
    backend.database.course_notes.insert_one({'id': 1, 'user_id': 1, 'stored_filename': 'new.txt'})
    with pytest.raises(RuntimeError, match='uploads changed'):
        erasure.erase(1, expected_uploads=expected)
    assert backend.database.users.find_one({'id': 1}) is not None
    assert not erasure.erase(1, pending_token='wrong-token')
    backend.database.users.update_one({'id': 1}, {'$set': {'parent_consent_granted': True}})
    assert not erasure.erase(1, pending_token='pending-test')
    assert backend.database.course_notes.find_one({'id': 1}) is not None


def test_erasure_refuses_a_globally_stored_file_shared_with_another_account(backend):
    backend.database.lessons.insert_many([
        {'id': 1, 'user_id': 1, 'stored_filename': 'shared.pdf'},
        {'id': 2, 'user_id': 2, 'stored_filename': 'shared.pdf'},
    ])
    with pytest.raises(RuntimeError, match='another account'):
        MongoErasure(backend).upload_names(1)
    assert backend.database.lessons.count_documents({}) == 2


@pytest.mark.parametrize('compressed', [False, True])
def test_migrated_json_text_audit_scrubbing_retains_peer_ids_and_exact_numbers(backend, compressed):
    from intelliplan.persistence.codec import pack_document, unpack_document
    changes = ('{"target_user_ids":[1,2],"nested":{"user_id":"1"},'
               '"precise":0.12345678901234567890123456789,'
               '"huge":1234567890123456789012345678901234567890,'
               '"exponent":1.234567890123456789e100,"padding":"' + ('A' * 20000 if compressed else '') + '"}')
    row = {'id': 1, 'actor_id': 2, 'changes': changes}
    backend.database.learning_policy_audit.insert_one(pack_document(row) if compressed else row)
    assert MongoErasure(backend).erase(1)
    saved = unpack_document(backend.database.learning_policy_audit.find_one({'id': 1}))['changes']
    assert isinstance(saved, str)
    parsed = json.loads(saved, parse_float=Decimal)
    assert parsed['target_user_ids'] == [2]
    assert parsed['nested'] == {'deleted_account': True}
    assert parsed['precise'] == Decimal('0.12345678901234567890123456789')
    assert parsed['huge'] == 1234567890123456789012345678901234567890
    assert parsed['exponent'] == Decimal('1.234567890123456789e100')
    assert parsed['padding'] == ('A' * 20000 if compressed else '')
    assert backend.database.users.find_one({'id': 2}) is not None


def test_deleted_accounts_cannot_recreate_native_children(backend, monkeypatch):
    from adaptive_tutor.mongo_store import MongoTutor
    from flashcards.mongo_store import MongoFlashcards
    from intelliplan.persistence.mongo_accounts import MongoAccounts
    from intelliplan.persistence.mongo_identity import MongoIdentities
    from intelliplan.persistence.mongo_extensions import MongoExtensionTokens
    from intelliplan.persistence.accounts import PasswordConflict

    assert MongoErasure(backend).erase(1)
    with pytest.raises(LookupError):
        MongoFlashcards(backend).create_deck(1, 'Cannot resurrect')
    tutor = MongoTutor(backend)
    monkeypatch.setattr(tutor, '_owner', lambda: {'user_id': 1})
    with pytest.raises(LookupError):
        tutor.get_or_create_profile()
    with pytest.raises(LookupError):
        MongoIdentities(backend).get_or_create(1)
    with pytest.raises(PasswordConflict):
        MongoExtensionTokens(backend).issue(1)
    MongoAccounts(backend).record_event(user_id=1, event='late_event')
    assert backend.database.security_events.count_documents({'user_id': 1}) == 0
    assert backend.database.fc_decks.count_documents({'user_id': 1}) == 0
    assert backend.database.adaptive_student_profile.count_documents({'user_id': 1}) == 0


def test_native_erasure_covers_every_declared_user_reference():
    import App
    covered = {name: {'user_id'} for name in DIRECT_USER_COLLECTIONS}
    covered.update({name: set(fields) for name, fields in DIRECT_OTHER_OWNERS.items()})
    for name, fields in ANONYMIZED_REFERENCES.items():
        covered.setdefault(name, set()).update(fields)
    covered.setdefault('study_group_tasks', set()).add('created_by_user_id')
    missing = []
    for table in App.db.metadata.tables.values():
        for column in table.columns:
            for key in column.foreign_keys:
                if key.target_fullname == 'users.id' and column.name not in covered.get(table.name, set()):
                    missing.append(f'{table.name}.{column.name}')
    assert not missing, missing


@pytest.mark.parametrize('failure', [None, 'rollback', 'unknown_commit'])
def test_native_file_staging_preserves_files_on_failure_and_quarantines_uncertain_outcomes(
        backend, tmp_path, monkeypatch, failure):
    import App
    from flask_login import login_user
    from intelliplan.persistence.mongo_accounts import MongoAccounts

    notes = tmp_path / 'notes'
    lessons = tmp_path / 'lessons'
    (notes / 'user_1').mkdir(parents=True)
    (notes / 'user_2').mkdir()
    lessons.mkdir()
    owned = notes / 'user_1' / 'owned.txt'
    other = notes / 'user_2' / 'other.txt'
    owned.write_text('owned synthetic content')
    other.write_text('other synthetic content')
    backend.database.course_notes.insert_one({'id': 1, 'user_id': 1, 'stored_filename': 'owned.txt'})
    monkeypatch.setitem(App.app.config, 'NOTES_UPLOAD_FOLDER', str(notes))
    monkeypatch.setattr(App, 'LESSON_UPLOAD_FOLDER', str(lessons))
    if failure:
        class Failure(Exception):
            def has_error_label(self, label):
                return failure == 'unknown_commit' and label == 'UnknownTransactionCommitResult'

        def fail_erase(self, *args, **kwargs):
            raise Failure('synthetic failure')
        monkeypatch.setattr(MongoErasure, 'erase', fail_erase)
    with App.app.test_request_context('/account/delete', method='POST'):
        login_user(MongoAccounts(backend).get(1))
        response = App.app.make_response(App._native_account_delete_impl(1, backend))
    assert other.read_text() == 'other synthetic content'
    if failure == 'rollback':
        assert response.status_code == 500
        assert owned.read_text() == 'owned synthetic content'
        assert not list(owned.parent.glob('.delete-*'))
        assert backend.database.users.find_one({'id': 1}) is not None
    elif failure == 'unknown_commit':
        assert response.status_code == 500
        assert 'needs verification' in response.json['message']
        assert not owned.exists()
        assert len(list(owned.parent.glob('.delete-*'))) == 1
    else:
        assert response.status_code == 200
        assert backend.database.users.find_one({'id': 1}) is None
        assert not owned.exists()
        assert not list(owned.parent.glob('.delete-*'))
