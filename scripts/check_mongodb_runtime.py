"""Verify the native driver in a dedicated test database without source writes.

Usage: python scripts/check_mongodb_runtime.py --env-file <private file>
Credentials are read by the driver and never included in reports or arguments.
The test records are retained for inspection; no database is dropped or cleared.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
from threading import Barrier
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values  # noqa: E402
from flask import Flask, session  # noqa: E402

from adaptive_tutor.mongo_store import MongoTutor  # noqa: E402
import desktop_auth  # noqa: E402
from flashcards.mongo_store import MongoFlashcards  # noqa: E402
from intelliplan.persistence.accounts import PasswordConflict  # noqa: E402
from intelliplan.persistence.codec import pack_document, unpack_document, update_fields  # noqa: E402
from intelliplan.persistence.mongo import MongoStore  # noqa: E402
from intelliplan.persistence.mongo_accounts import MongoAccounts  # noqa: E402
from intelliplan.persistence.mongo_desktop import MongoDesktopTickets  # noqa: E402
from intelliplan.persistence.mongo_identity import MongoIdentities  # noqa: E402
from intelliplan.persistence.mongo_extensions import MongoExtensionTokens  # noqa: E402
from intelliplan.persistence.mongo_erasure import MongoErasure  # noqa: E402
from intelliplan.persistence.sessions import MongoSession, MongoSessionInterface  # noqa: E402


class DeliberateRollback(Exception):
    pass


def verify_sessions(store, run_id, user_id, epoch):
    cookie_name = f'runtime_{run_id}'

    def session_app(name):
        app = Flask(name)
        app.config.update(SECRET_KEY='synthetic-runtime-only', SESSION_COOKIE_NAME=cookie_name,
                          SESSION_KEY_PREFIX=f'runtime:{run_id}:')
        app.session_interface = MongoSessionInterface(app, store)

        @app.get('/write')
        def write():
            session['_user_id'] = str(user_id)
            session['session_epoch'] = epoch
            session['runtime_marker'] = run_id
            return {'ok': True}

        @app.get('/read')
        def read():
            return {'marker': session.get('runtime_marker')}

        @app.post('/rotate')
        def rotate():
            app.session_interface.regenerate(session)
            session['rotated'] = True
            return {'ok': True}

        @app.post('/clear')
        def clear():
            session.clear()
            return {'ok': True}

        return app

    first = session_app(f'runtime-first-{run_id}').test_client()
    second = session_app(f'runtime-second-{run_id}').test_client()
    if first.get('/write').status_code != 200:
        raise AssertionError('Native session could not be saved')
    original = first.get_cookie(cookie_name).value
    second.set_cookie(cookie_name, original)
    if second.get('/read').json['marker'] != run_id:
        raise AssertionError('Native session was not shared across app instances')
    if second.post('/rotate').status_code != 200:
        raise AssertionError('Native session rotation failed')
    rotated = second.get_cookie(cookie_name).value
    if rotated == original or first.get('/read').json['marker'] is not None:
        raise AssertionError('The original native session survived rotation')
    if second.post('/clear').status_code != 200:
        raise AssertionError('Native session logout failed')
    first.set_cookie(cookie_name, rotated)
    if first.get('/read').json['marker'] is not None:
        raise AssertionError('The native session survived logout')


def verify_erasure(store, run_id, user, tutor):
    accounts = MongoAccounts(store)
    peer = accounts.create(email=f'peer-{run_id}@example.invalid', password_hash='synthetic-peer-hash')
    store.database.users.update_one({'id': peer.id}, {'$set': {'referred_by_id': user.id}})
    group_id = store.next_id('study_groups')
    store.database.study_groups.insert_one({'id': group_id, 'owner_id': user.id, 'name': 'Synthetic shared group'})
    audit_ids = []
    for padding in ('', 'A' * 20000):
        audit_id = store.next_id('learning_policy_audit')
        audit_ids.append(audit_id)
        changes = ('{"target_user_ids":[' + str(user.id) + ',' + str(peer.id) + '],'
                   '"precise":0.12345678901234567890123456789,"padding":' + json.dumps(padding) + '}')
        store.database.learning_policy_audit.insert_one(pack_document({
            'id': audit_id, 'actor_id': peer.id, 'changes': changes}))
    serializer = MongoSessionInterface(Flask(f'erasure-{run_id}'), store).serializer
    for kind, identity in (('own', user.id), ('peer', peer.id)):
        store.database.sessions.insert_one({'session_id': f'legacy-{kind}-{run_id}',
            'expiry': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            'data': serializer.encode({'_user_id': str(identity)})})

    def fail_decoder(value):
        raise DeliberateRollback()

    try:
        MongoErasure(store, session_decoder=fail_decoder).erase(user.id)
    except DeliberateRollback:
        pass
    else:
        raise AssertionError('Deliberate erasure failure did not propagate')
    if accounts.get(user.id) is None or store.database.fc_cards.count_documents({'user_id': user.id}) != 50:
        raise AssertionError('Failed erasure did not roll back owned data')
    if accounts.get(peer.id).referred_by_id != user.id:
        raise AssertionError('Failed erasure did not roll back shared references')
    erasure = MongoErasure(store, session_decoder=serializer.decode)
    if not erasure.erase(user.id, expected_uploads=erasure.upload_names(user.id)):
        raise AssertionError('Native account erasure failed')
    if accounts.get(user.id) is not None or store.database.fc_cards.count_documents({'user_id': user.id}):
        raise AssertionError('Erasure retained owned account data')
    if store.database.adaptive_student_profile.count_documents({'user_id': user.id}):
        raise AssertionError('Erasure retained the learner profile')
    if any(store.database[name].count_documents({'user_id': user.id})
           for name in ('tutor_memory', 'tutor_conversations')):
        raise AssertionError('Erasure retained native tutor history')
    if accounts.get(peer.id) is None or accounts.get(peer.id).referred_by_id is not None:
        raise AssertionError('Erasure removed or failed to unlink another account')
    if store.database.study_groups.find_one({'id': group_id})['owner_id'] is not None:
        raise AssertionError('Shared group was not retained anonymously')
    for audit_id in audit_ids:
        row = store.database.learning_policy_audit.find_one({'id': audit_id})
        changes = json.loads(unpack_document(row)['changes'], parse_float=Decimal)
        if changes['target_user_ids'] != [peer.id] or changes['precise'] != Decimal('0.12345678901234567890123456789'):
            raise AssertionError('Erasure retained migrated audit references or lost JSON precision')
    if store.database.sessions.find_one({'session_id': f'legacy-own-{run_id}'}) is not None:
        raise AssertionError('Erasure retained an owned legacy binary session')
    if store.database.sessions.find_one({'session_id': f'legacy-peer-{run_id}'}) is None:
        raise AssertionError('Erasure removed another account session')
    for create in (lambda: MongoFlashcards(store).create_deck(user.id, 'Cannot resurrect'),
                   tutor.get_or_create_profile, lambda: MongoIdentities(store).get_or_create(user.id)):
        try:
            create()
        except LookupError:
            pass
        else:
            raise AssertionError('A deleted account recreated child records')
    app = Flask(f'post-erasure-session-{run_id}')
    interface = MongoSessionInterface(app, store)
    stale = MongoSession({'_user_id': str(user.id), 'session_epoch': 1}, sid=f'deleted-{run_id}')
    interface._upsert_session(timedelta(hours=1), stale, f'runtime:deleted-{run_id}')
    if store.database.sessions.count_documents({'user_id': user.id}):
        raise AssertionError('A stale response recreated an erased session')

    def learner_profile(identity):
        class OwnedTutor(MongoTutor):
            def _owner(self):
                return {'user_id': identity}
        return OwnedTutor(store).get_or_create_profile()

    from intelliplan.persistence.mongo_linked_accounts import MongoLinkedAccounts
    from intelliplan.persistence.mongo_app_links import MongoAppLinks

    for label, collection, create_child in (
        ('deck', 'fc_decks', lambda identity: MongoFlashcards(store).create_deck(identity, 'Synthetic racing deck')),
        ('identity', 'user_identities', lambda identity: MongoIdentities(store).get_or_create(identity)),
        ('learner', 'adaptive_student_profile', learner_profile),
        ('linked', 'linked_accounts', lambda identity: MongoLinkedAccounts(store).create_active(
            identity, 'Synthetic racing school', 'studentvue', {'password': 'synthetic'})),
        ('mobile', 'app_link_codes', lambda identity: MongoAppLinks(store).issue(
            identity, code_hash=uuid.uuid4().hex * 2, next_path='/settings')),
    ):
        account = accounts.create(email=f'erasure-race-{label}-{run_id}@example.invalid', password_hash='synthetic-hash')
        gate = Barrier(2)

        def create():
            gate.wait(timeout=10)
            try:
                return create_child(account.id)
            except LookupError:
                return None

        def remove():
            gate.wait(timeout=10)
            return MongoErasure(store, session_decoder=serializer.decode).erase(account.id)

        with ThreadPoolExecutor(max_workers=2) as executor:
            created, removed = executor.submit(create), executor.submit(remove)
            created.result()
            if not removed.result():
                raise AssertionError('Concurrent account erasure did not finish')
        if accounts.get(account.id) is not None or store.database[collection].count_documents({'user_id': account.id}):
            raise AssertionError('Concurrent erasure left an orphan child')

    pending = accounts.create(email=f'pending-race-{run_id}@example.invalid', password_hash='synthetic-hash')
    pending = accounts.establish_age(pending.id, birth_year=datetime.now(timezone.utc).year - 10,
                                     parent_email=f'parent-race-{run_id}@example.invalid')
    pending_token = pending.parent_consent_token
    gate = Barrier(2)

    def approve():
        gate.wait(timeout=10)
        return accounts.approve_parent(pending_token, notice_version='synthetic-race')

    def deny():
        gate.wait(timeout=10)
        return MongoErasure(store, session_decoder=serializer.decode).erase(
            pending.id, pending_token=pending_token)

    with ThreadPoolExecutor(max_workers=2) as executor:
        approval, denial = executor.submit(approve), executor.submit(deny)
        approved, denied = approval.result(), denial.result()
    remaining = accounts.get(pending.id)
    if denied:
        if approved is not None or remaining is not None:
            raise AssertionError('A denied pending account also received approval')
    elif approved is None or remaining is None or not remaining.parent_consent_granted:
        raise AssertionError('Concurrent parental decisions left an inconsistent account')


def verify(store):
    if store.database.name != 'intelliplan_runtime_test':
        raise ValueError('Only the dedicated intelliplan_runtime_test database is allowed')
    if not store.ping():
        raise RuntimeError('MongoDB ping failed')
    run_id = uuid.uuid4().hex
    probes = store.database['_runtime_probes']
    probes.create_index('run_id')
    accounts = MongoAccounts(store)
    accounts.ensure_indexes()
    user = accounts.create(email=f'runtime-{run_id}@example.invalid',
                           name='Synthetic runtime check', password_hash='synthetic-unusable-hash')
    checks = {'driver_ping': True}
    accounts.set_preferences(user.id, {'phone': '+12065550101', 'sms_reminders_opt_in': True,
                                      'push_reminders_opt_in': True})
    saved_preferences = accounts.set_preferences(user.id, {'phone': None})
    if saved_preferences.sms_reminders_opt_in or not saved_preferences.push_reminders_opt_in:
        raise AssertionError('Account preference update lost fields or retained SMS without a phone')
    if accounts.set_preferences(user.id, {}).sms_reminders_opt_in:
        raise AssertionError('Empty preference update changed the SMS invariant')
    checks['atomic_account_preferences'] = True

    store.database.users.update_one({'id': user.id}, update_fields({
        'lms_preferences': '{"precise":0.12345678901234567890123456789,"future_setting":' + json.dumps('x' * 20000) + '}'}))
    with ThreadPoolExecutor(max_workers=2) as executor:
        grade = executor.submit(accounts.set_lms_sources, user.id, {'grade_source': 'canvas'})
        assignments = executor.submit(accounts.set_lms_sources, user.id, {'assignment_sources': ['manual']})
        grade.result()
        assignments.result()
    saved_preferences = json.loads(accounts.get(user.id).lms_preferences, parse_float=Decimal)
    if saved_preferences != {'future_setting': 'x' * 20000, 'precise': Decimal('0.12345678901234567890123456789'),
                             'grade_source': 'canvas', 'assignment_sources': ['manual']}:
        raise AssertionError('Concurrent LMS preference updates lost restored fields')
    accounts.set_preferences(user.id, {'focus_enforcement': 'alarm', 'focus_grace_seconds': 40})
    preferred = accounts.set_preferences(user.id, {'premium_model_pref': 'auto', 'premium_clarify': False})
    if preferred.focus_enforcement != 'alarm' or preferred.focus_grace_seconds != 40 or preferred.premium_clarify:
        raise AssertionError('Focus/premium preference updates lost unrelated settings')
    checks['atomic_lms_source_preferences'] = True
    with ThreadPoolExecutor(max_workers=4) as executor:
        attempts = list(executor.map(lambda _: accounts.consume_tutor_message(user.id, 7), range(20)))
    if sum(result[0] for result in attempts) != 7 or accounts.get(user.id).monthly_tutor_messages != 7:
        raise AssertionError('Concurrent tutor requests exceeded or lost the allowance')
    reset_at = accounts.get(user.id).tutor_reset_date
    if accounts.consume_tutor_message(user.id, 7, now=reset_at) != (True, 6, 7):
        raise AssertionError('Tutor allowance did not reset at the next UTC month')
    checks['atomic_tutor_allowance'] = True
    with ThreadPoolExecutor(max_workers=4) as executor:
        touches = list(executor.map(lambda index: accounts.remember_first_touch(
            user.id, json.dumps({'channel': f'synthetic-{index}'})), range(8)))
    if sum(touches) != 1 or not accounts.get(user.id).first_touch_json:
        raise AssertionError('Concurrent attribution replaced the first touch')
    checks['single_write_first_touch'] = True
    with ThreadPoolExecutor(max_workers=4) as executor:
        children = list(executor.map(lambda _: accounts.establish_age(user.id,
            birth_year=datetime.now(timezone.utc).year - 10, parent_email=f'parent-{run_id}@example.invalid'), range(8)))
    consent_tokens = {child.parent_consent_token for child in children}
    if len(consent_tokens) != 1 or None in consent_tokens:
        raise AssertionError('Concurrent initial age writes replaced the consent token')
    consent_token = next(iter(consent_tokens))
    with ThreadPoolExecutor(max_workers=4) as executor:
        approvals = list(executor.map(lambda _: accounts.approve_parent(
            consent_token, notice_version='synthetic-runtime'), range(8)))
    if sum(result is not None for result in approvals) != 1:
        raise AssertionError('Parental consent was consumed more than once')
    approved = accounts.get(user.id)
    if not approved.parent_consent_granted or approved.parent_consent_token is not None:
        raise AssertionError('Parental consent was not recorded atomically')
    checks['single_use_parental_consent'] = True
    try:
        accounts.set_role(user.id, 'parent')
    except ValueError as exc:
        if str(exc) != 'adult_account_required':
            raise
    else:
        raise AssertionError('Child account changed to a parent role')
    if accounts.set_role(user.id, 'student').role != 'student':
        raise AssertionError('Native role update failed')
    checks['role_age_eligibility'] = True

    def rollback(db, active):
        db['_runtime_probes'].insert_one({'run_id': run_id, 'test': 'rollback'}, session=active)
        raise DeliberateRollback()

    try:
        store.transaction(rollback)
    except DeliberateRollback:
        pass
    if probes.count_documents({'run_id': run_id, 'test': 'rollback'}) != 0:
        raise AssertionError('Transaction did not roll back')
    checks['transaction_rollback'] = True

    with ThreadPoolExecutor(max_workers=4) as executor:
        ids = list(executor.map(lambda _: store.next_id('_runtime_probes'), range(24)))
    if len(set(ids)) != 24:
        raise AssertionError('Concurrent ID allocations overlapped')
    checks['concurrent_ids'] = True

    token_hash = f'synthetic-{run_id}'
    accounts.issue_reset(user.id, token_hash=token_hash, requested_ip='runtime-test')
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: accounts.complete_reset(
            token_hash, password_hash='synthetic-reset-hash'), range(8)))
    if sum(result is not None for result in results) != 1:
        raise AssertionError('A reset token was consumed more than once')
    if accounts.get(user.id).session_epoch != 1:
        raise AssertionError('Credential epoch did not change exactly once')
    checks['single_use_reset'] = True

    tickets = MongoDesktopTickets(store)
    tickets.ensure_indexes()
    verifier = desktop_auth.new_verifier()
    code_hash = desktop_auth.hash_code(desktop_auth.new_code())
    tickets.issue(user.id, code_hash=code_hash, code_challenge=desktop_auth.challenge_for(verifier))
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: tickets.redeem(
            code_hash=code_hash, verifier=verifier), range(8)))
    if sum(result is not None for result in results) != 1:
        raise AssertionError('A desktop ticket was consumed more than once')
    checks['single_use_desktop_handoff'] = True

    identities = MongoIdentities(store)
    identities.ensure_indexes()
    with ThreadPoolExecutor(max_workers=4) as executor:
        profiles = list(executor.map(lambda _: identities.get_or_create(user.id), range(8)))
    if len({profile.id for profile in profiles}) != 1:
        raise AssertionError('Concurrent identity creation duplicated the profile')
    patches = [{'goals': 'synthetic goal'}, {'grade_level': '10th grade'},
               {'weekly_commitments': 'synthetic commitment'}, {'availability': '{"Mon":"16:00"}'}]
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda patch: identities.save(user.id, patch), patches))
    profile = identities.get(user.id)
    if any(getattr(profile, field) != value for patch in patches for field, value in patch.items()):
        raise AssertionError('Concurrent identity updates lost unrelated fields')
    checks['concurrent_identity_updates'] = True

    tokens = MongoExtensionTokens(store)
    tokens.ensure_indexes()
    with ThreadPoolExecutor(max_workers=4) as executor:
        credentials = list(executor.map(lambda _: tokens.issue(
            user.id, reuse=True, expected_epoch=1), range(8)))
    if len(set(credentials)) != 1 or tokens.user_for(credentials[0]).id != user.id:
        raise AssertionError('Concurrent extension handoff duplicated credentials')
    try:
        tokens.issue(user.id, expected_epoch=0)
    except PasswordConflict:
        pass
    else:
        raise AssertionError('A revoked cookie epoch issued an extension credential')
    checks['concurrent_extension_handoff'] = True

    import app_link
    from intelliplan.persistence.mongo_app_links import MongoAppLinks
    mobile_links = MongoAppLinks(store)
    mobile_links.ensure_indexes()
    mobile_code = app_link.new_code()
    mobile_links.issue(user.id, code_hash=app_link.hash_code(mobile_code), next_path='/settings', expected_epoch=1)
    with ThreadPoolExecutor(max_workers=4) as executor:
        mobile_claims = list(executor.map(lambda _: mobile_links.redeem(mobile_code), range(8)))
    if sum(result is not None for result in mobile_claims) != 1:
        raise AssertionError('Concurrent mobile browser claims reused a code')
    claimed_mobile = next(result for result in mobile_claims if result is not None)
    if claimed_mobile.session_epoch != 1 or claimed_mobile.next_path != '/settings':
        raise AssertionError('Mobile handoff lost the issuance proof or destination')
    mobile_codes = [app_link.new_code() for _ in range(8)]
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda code: mobile_links.issue(user.id, code_hash=app_link.hash_code(code),
            next_path='/oauth/canvas', expected_epoch=1), mobile_codes))
    live_mobile = store.database.app_link_codes.count_documents({'user_id': user.id, 'used_at': None})
    if live_mobile != 1:
        raise AssertionError('Concurrent mobile handoff issuance retained multiple live codes')
    checks['single_use_mobile_browser_handoff'] = True
    import api_keys
    from intelliplan.persistence.mongo_api_keys import MongoAPIKeys
    public_keys = MongoAPIKeys(store)
    public_keys.ensure_indexes()
    synthetic_key, key_prefix, key_digest = api_keys.generate_key()
    key_id = store.next_id('api_keys')
    store.database.api_keys.insert_one({'id': key_id, 'user_id': user.id, 'key_hash': key_digest,
        'key_prefix': key_prefix, 'status': 'active', 'scopes': 'read:profile', 'request_count': None})
    resolved = public_keys.resolve_digest(api_keys.hash_key(synthetic_key))
    if resolved is None or resolved.user_id != user.id or resolved.scope_list() != ['read:profile']:
        raise AssertionError('Native API key lookup lost the owner or scope')
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: public_keys.touch(key_id, user.id), range(12)))
    if store.database.api_keys.find_one({'id': key_id})['request_count'] != 12:
        raise AssertionError('Concurrent API usage touches lost increments')
    store.database.api_keys.update_one({'id': key_id}, {'$set': {'status': 'revoked'}})
    if public_keys.resolve_digest(key_digest) is not None:
        raise AssertionError('A revoked native API credential was accepted')
    checks['native_api_key_verification'] = True
    from intelliplan.persistence.mongo_api_keys import KeySlotsFull
    key_owner = accounts.create(email=f'key-owner-{run_id}@example.invalid', password_hash='synthetic-hash')
    # An aged synthetic account may auto-approve read-only scopes. The age is
    # set only in this dedicated test database, never on a source account.
    store.database.users.update_one({'id': key_owner.id}, {'$set': {
        'created_at': (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)).isoformat()}})
    key_values = {'app_name': 'Synthetic runtime key', 'app_url': 'https://example.invalid',
                  'use_case': 'A synthetic runtime check for developer credential persistence.',
                  'expected_volume': 'low', 'contact_email': key_owner.email, 'scopes': 'read:profile'}
    def submit_key(_):
        try:
            return public_keys.submit(key_owner.id, key_values)
        except KeySlotsFull:
            return None
    with ThreadPoolExecutor(max_workers=4) as executor:
        applications = list(executor.map(submit_key, range(8)))
    if sum(value is not None for value in applications) != 5:
        raise AssertionError('Concurrent developer applications exceeded the five-slot limit')
    key_row, key_secret = next(value for value in applications if value is not None)
    if public_keys.resolve_digest(api_keys.hash_key(key_secret)) is None:
        raise AssertionError('A read-only native key did not auto-approve')
    rolled_row, rolled_secret = public_keys.roll(key_owner.id, key_row.id)
    if public_keys.resolve_digest(api_keys.hash_key(key_secret)) is not None or public_keys.resolve_digest(api_keys.hash_key(rolled_secret)) is None:
        raise AssertionError('Native developer-key rotation did not replace the secret')
    public_keys.revoke(key_owner.id, rolled_row.id)
    if public_keys.resolve_digest(api_keys.hash_key(rolled_secret)) is not None:
        raise AssertionError('Native developer-key revocation did not invalidate the secret')
    pending, _ = public_keys.submit(key_owner.id, {**key_values, 'scopes': 'write:tasks'})
    with ThreadPoolExecutor(max_workers=4) as executor:
        from intelliplan.persistence.mongo_api_keys import KeyStateConflict
        def approve_key(_):
            try:
                return public_keys.decide(pending.id, True, 'Synthetic approved review')
            except KeyStateConflict:
                return None
        approvals = list(executor.map(approve_key, range(8)))
    if sum(value is not None for value in approvals) != 1:
        raise AssertionError('Concurrent admin decisions minted multiple secrets')
    checks['atomic_developer_key_lifecycle'] = True

    from intelliplan.persistence.mongo_linked_accounts import MongoLinkedAccounts
    linked = MongoLinkedAccounts(store)
    linked.ensure_indexes()
    with ThreadPoolExecutor(max_workers=4) as executor:
        profiles = list(executor.map(lambda index: linked.create_active(
            user.id, f'Synthetic school {index}', 'studentvue',
            {'password': f'synthetic-{run_id}-{index}'}), range(8)))
    if len(linked.list(user.id, active=True)) != 1:
        raise AssertionError('Concurrent LMS connections left multiple active profiles')
    with ThreadPoolExecutor(max_workers=4) as executor:
        switched = list(executor.map(lambda row: linked.switch(user.id, row.profile_id), profiles))
    if not all(switched) or len(linked.list(user.id, active=True)) != 1:
        raise AssertionError('Concurrent LMS profile switches lost the active invariant')
    try:
        unauthorized = linked.switch(user.id + 1000000, profiles[0].profile_id)
    except LookupError:
        unauthorized = False
    if unauthorized:
        raise AssertionError('LMS profile switch crossed an account boundary')
    checks['concurrent_lms_profiles'] = True

    from intelliplan.persistence.mongo_canvas import MongoCanvas
    canvas = MongoCanvas(store)
    canvas.ensure_indexes()
    def connect_canvas(token):
        canvas.connect(user.id, canvas_base='https://synthetic.instructure.com',
            access_token=token, refresh_token='synthetic-refresh', expires_at=None,
            canvas_user_id='synthetic', canvas_user_name='Runtime', profile_name='Runtime',
            credentials={'canvas_token': token, 'canvas_oauth': True,
                         'canvas_url': 'https://synthetic.instructure.com'})
    connect_canvas('synthetic-original')
    previous = canvas.get(user.id)
    with ThreadPoolExecutor(max_workers=4) as executor:
        refreshed = list(executor.map(lambda index: canvas.refresh(user.id, previous,
            access_token=f'synthetic-fresh-{index}', refresh_token=f'synthetic-rotated-{index}',
            expires_at=None), range(8)))
    if sum(refreshed) != 1:
        raise AssertionError('Concurrent Canvas refresh accepted a stale connection proof')
    current = canvas.get(user.id)
    if linked.active(user.id).get_credentials()['canvas_token'] != current.access_token:
        raise AssertionError('Canvas OAuth and profile tokens diverged')
    # Fail after updating the OAuth row: driver rollback must restore that row.
    profile = linked.active(user.id)
    raw_profile = store.database.linked_accounts.find_one({'id': profile.id})
    store.database.linked_accounts.update_one({'id': profile.id}, {'$set': {'credentials': 'invalid-json'}})
    try:
        canvas.refresh(user.id, current, access_token='must-rollback', refresh_token=None, expires_at=None)
    except ValueError:
        pass
    else:
        raise AssertionError('Canvas rollback probe did not fail')
    if canvas.get(user.id).access_token != current.access_token:
        raise AssertionError('Canvas token update escaped transaction rollback')
    store.database.linked_accounts.replace_one({'id': profile.id}, raw_profile)
    connect_canvas('synthetic-reconnected')
    if canvas.disconnect(user.id, current):
        raise AssertionError('A stale disconnect erased a new Canvas connection')
    if not canvas.disconnect(user.id, canvas.get(user.id)) or canvas.get(user.id) is not None:
        raise AssertionError('Canvas disconnect did not remove the OAuth connection')
    if any('canvas_token' in row.get_credentials() for row in linked.list(user.id, login_type='canvas')):
        raise AssertionError('Canvas disconnect retained a linked-profile credential')
    checks['atomic_canvas_credentials'] = True

    import chatbot_api as tutor_api
    from intelliplan.persistence.mongo_tutor_history import MongoTutorHistory
    history = MongoTutorHistory(store, lambda: (user.id, None), tutor_api._normalize_tutor_messages,
        tutor_api._merge_tutor_messages, tutor_api._default_tutor_profile)
    history.ensure_indexes()
    with ThreadPoolExecutor(max_workers=4) as executor:
        memories = list(executor.map(lambda _: history.load_memory(), range(8)))
    if len({row['id'] for row in memories}) != 1:
        raise AssertionError('Concurrent tutor memory creation duplicated an owner')
    memory_id = memories[0]['id']
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: history.record_profile_turn(memory_id,
            '[Subject: Math] Help me with algebra', 'Synthetic example', tutor_api._update_tutor_profile), range(12)))
    if json.loads(history.load_memory()['profile_json'])['turns'] != 12:
        raise AssertionError('Concurrent tutor profile updates lost turns')
    conversation = history.create_conversation('Synthetic concurrent conversation')
    common = [{'role': 'user', 'content': 'Synthetic start'}, {'role': 'assistant', 'content': 'Ready'}]
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda index: history.save_conversation(conversation,
            common + [{'role': 'user', 'content': f'Synthetic branch {index}'}]), range(8)))
    transcript = json.loads(history.get_conversation(conversation)['messages_json'])
    if len(transcript) != 10 or {row['content'] for row in transcript[2:]} != {f'Synthetic branch {index}' for index in range(8)}:
        raise AssertionError('Concurrent tutor conversation saves lost or duplicated history')
    checks['concurrent_tutor_history'] = True
    guest_history = MongoTutorHistory(store, lambda: (None, f'synthetic-guest-{run_id}'),
        tutor_api._normalize_tutor_messages, tutor_api._merge_tutor_messages, tutor_api._default_tutor_profile)
    with ThreadPoolExecutor(max_workers=4) as executor:
        guest_memories = list(executor.map(lambda _: guest_history.load_memory(), range(8)))
    if len({row['id'] for row in guest_memories}) != 1 or guest_history.get_conversation(conversation) is not None:
        raise AssertionError('Tutor guest memory duplicated or crossed an account boundary')
    checks['isolated_guest_tutor_memory'] = True

    from intelliplan.persistence.mongo_ai_ledger import MongoAILedger
    ledger = MongoAILedger(store)
    ledger.ensure_indexes()
    credit_ref = f'synthetic-credit:{run_id}'
    with ThreadPoolExecutor(max_workers=4) as executor:
        credits = list(executor.map(lambda _: ledger.credit(user.id, 1000000,
            period='2026-10', reason='test', stripe_ref=credit_ref, now=datetime(2026, 10, 18)), range(8)))
    if sum(credits) != 1 or ledger.granted(user.id, '2026-10') != 1000000:
        raise AssertionError('Concurrent AI credits were duplicated')
    values = {'period': '2026-10', 'created_at': datetime(2026, 10, 18).isoformat(),
        'provider': 'synthetic', 'model': 'synthetic', 'source': 'platform', 'purpose': 'test',
        'tier': None, 'input_tokens': 1, 'output_tokens': 1, 'cache_read_tokens': 0,
        'cache_write_tokens': 0, 'cost_micro': 2**53 + 1}
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: ledger.record(user.id, values), range(8)))
    if ledger.spent(user.id, '2026-10', 'platform') != 8 * (2**53 + 1):
        raise AssertionError('AI spending totals lost integer precision or concurrent writes')
    checks['atomic_ai_ledger'] = True

    from intelliplan.persistence.mongo_email import MongoEmailState
    mail = MongoEmailState(store)
    mail.ensure_indexes()
    synthetic_address = f'synthetic-{run_id}@example.invalid'
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: mail.suppress(synthetic_address.upper()), range(8)))
    if not all(results) or store.database.email_suppressions.count_documents({'email': synthetic_address}) != 1:
        raise AssertionError('Concurrent email suppressions duplicated or failed')
    with ThreadPoolExecutor(max_workers=4) as executor:
        claims = list(executor.map(lambda _: mail.claim_budget_notice(user.id, '2026-10'), range(8)))
    if sum(row is not None for row in claims) != 1 or mail.claim_budget_notice(user.id, '2026-09') is not None:
        raise AssertionError('Concurrent or stale monthly budget notices were claimed repeatedly')
    checks['atomic_email_suppression_and_notice'] = True

    with ThreadPoolExecutor(max_workers=4) as executor:
        sends = list(executor.map(lambda _: mail.claim_send(user.id, f'synthetic-lifecycle:{run_id}'), range(8)))
    winners = [claim for claim in sends if claim is not None]
    if len(winners) != 1:
        raise AssertionError('Concurrent lifecycle sends were claimed repeatedly')
    original_claim = winners[0]
    if not mail.record_send(original_claim, 'failed'):
        raise AssertionError('Lifecycle failure could not be recorded')
    retry_claim = mail.claim_send(user.id, f'synthetic-lifecycle:{run_id}')
    if retry_claim is None or mail.record_send(original_claim, 'sent', 'stale'):
        raise AssertionError('Stale lifecycle completion overwrote a newer claim')
    if not mail.record_send(retry_claim, 'sent', 'synthetic-provider-id'):
        raise AssertionError('Lifecycle retry completion failed')
    if mail.claim_send(user.id, f'synthetic-lifecycle:{run_id}') is not None:
        raise AssertionError('Lifecycle send was reclaimed after acceptance')
    checks['atomic_lifecycle_email_claims'] = True

    notice_address = accounts.get(user.id).email
    store.database.users.update_one({'id': user.id}, {'$set': {
        'marketing_emails_opt_in': True, 'email_reminders_opt_in': True,
        'marketing_opt_in_at': '2026-10-01T00:00:00'}})
    original_transaction = store.transaction
    def force_unsubscribe_rollback(operation):
        def fail_after_write(db, active):
            operation(db, active)
            raise DeliberateRollback()
        return original_transaction(fail_after_write)
    try:
        store.transaction = force_unsubscribe_rollback
        try:
            mail.unsubscribe(notice_address)
            raise AssertionError('Unsubscribe rollback check did not abort')
        except DeliberateRollback:
            pass
    finally:
        store.transaction = original_transaction
    if mail.is_suppressed(notice_address) or not accounts.get(user.id).marketing_emails_opt_in:
        raise AssertionError('Aborted unsubscribe partially changed suppression or account settings')
    with ThreadPoolExecutor(max_workers=4) as executor:
        if not all(executor.map(lambda _: mail.unsubscribe(notice_address.upper()), range(8))):
            raise AssertionError('Concurrent unsubscribe failed')
    unsubscribed = accounts.get(user.id)
    if not mail.is_suppressed(notice_address) or unsubscribed.marketing_emails_opt_in or not unsubscribed.email_reminders_opt_in:
        raise AssertionError('Unsubscribe did not atomically preserve notification preferences')
    if unsubscribed.marketing_opt_in_at.isoformat() != '2026-10-01T00:00:00':
        raise AssertionError('Unsubscribe changed dated consent evidence')
    checks['native_unsubscribe_and_rollback'] = True

    cohort_now = datetime(2026, 10, 18, 12)
    cohort = accounts.create(email=f'synthetic-cohort-{run_id}@example.invalid', password_hash='synthetic-cohort')
    store.database.users.update_one({'id': cohort.id}, {'$set': {
        'created_at': (cohort_now - timedelta(days=3)).replace(tzinfo=timezone.utc).isoformat()}})
    cohort_rows = mail.onboarding_candidates(cohort_now - timedelta(days=11), cohort_now - timedelta(days=2), 10000)
    if not any(candidate.id == cohort.id for candidate in cohort_rows):
        raise AssertionError('Native onboarding omitted a restored ISO timestamp')
    if mail.onboarding_exists('SavedSchedule', cohort.id) or mail.completed_session(cohort.id):
        raise AssertionError('Native onboarding progress crossed an account boundary')
    if (user.id, f'synthetic-lifecycle:{run_id}') not in mail.onboarding_history(
            [user.id], [f'synthetic-lifecycle:{run_id}']):
        raise AssertionError('Native onboarding send history omitted an accepted message')
    checks['native_onboarding_progress'] = True

    campaign_key = f'synthetic-campaign:{run_id}'
    initial_cohort = mail.campaign_candidates(oldest=cohort_now - timedelta(days=4),
        newest=cohort_now - timedelta(days=2), email_key=campaign_key, limit=10000)
    if not any(candidate.id == cohort.id for candidate in initial_cohort):
        raise AssertionError('Native campaign selection omitted its eligible cohort')
    campaign_claim = mail.claim_send(cohort.id, campaign_key)
    mail.record_send(campaign_claim, 'sent', 'synthetic-campaign-provider-id')
    repeated_cohort = mail.campaign_candidates(oldest=cohort_now - timedelta(days=4),
        newest=cohort_now - timedelta(days=2), email_key=campaign_key, limit=10000)
    if any(candidate.id == cohort.id for candidate in repeated_cohort):
        raise AssertionError('Native campaign selection included an accepted send')
    if {candidate.id for candidate in mail.campaign_candidates(admins={cohort.email})} != {cohort.id}:
        raise AssertionError('Native campaign test-recipient selection broadened its scope')
    checks['native_campaign_selection'] = True

    from intelliplan.persistence.mongo_learning_policy import MongoLearningPolicy
    from intelliplan.learning import policy as learning_policy
    school_policy = MongoLearningPolicy(store)
    school_policy.ensure_indexes()
    parent_id = store.next_id('learning_organizations')
    child_id = store.next_id('learning_organizations')
    store.database.learning_organizations.insert_many([
        pack_document({'id': parent_id, 'parent_id': None, 'policy': json.dumps({'tutor': False, 'providers': ['claude']}) + ' ' * 10000}),
        {'id': child_id, 'parent_id': parent_id, 'policy': json.dumps({'providers': ['groq']})},
    ])
    store.database.learning_memberships.insert_one({'id': store.next_id('learning_memberships'),
        'user_id': user.id, 'organization_id': child_id, 'role': 'student'})
    policy_app = Flask('synthetic-runtime-policy')
    policy_app.extensions['mongo_store'] = store
    with policy_app.test_request_context():
        if learning_policy.decision(user, 'tutor') != (False, 'institution_policy'):
            raise AssertionError('Native tutor policy did not apply institution restrictions')
        if learning_policy.permitted_providers(user):
            raise AssertionError('Native provider restrictions were not intersected')
        store.database.learning_organizations.update_one({'id': parent_id}, update_fields({'policy': {'tutor': True}}))
        learning_policy.invalidate_cache()
        if learning_policy.decision(user, 'tutor') != (True, 'allowed'):
            raise AssertionError('Native policy invalidation did not reload restrictions')
        store.database.learning_organizations.update_one({'id': child_id}, {'$set': {'parent_id': -1}})
        learning_policy.invalidate_cache()
        if learning_policy.decision(user, 'tutor') != (False, 'policy_unavailable'):
            raise AssertionError('Missing institution parent did not fail closed')
    checks['native_institution_policy'] = True

    import os
    import secret_box
    from cryptography.fernet import Fernet
    from intelliplan.persistence.mongo_ai_accounts import MongoAIAccounts
    original_encryption = os.environ.get('DATA_ENCRYPTION_KEY')
    try:
        os.environ['DATA_ENCRYPTION_KEY'] = Fernet.generate_key().decode()
        secret_box.reset_cache()
        providers = MongoAIAccounts(store)
        providers.ensure_indexes()
        # Runtime owner is an adult synthetic account at this point.
        store.database.users.update_one({'id': user.id}, {'$set': {'birth_year': 1990}})
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda index: providers.link(user.id, 'openai',
                'sk-synthetic-' + run_id + str(index)), range(8)))
        if store.database.linked_ai_accounts.count_documents({'user_id': user.id, 'provider': 'openai'}) != 1:
            raise AssertionError('Concurrent provider account linking duplicated a credential')
        old_key = providers.list(user.id)[0]
        replacement = providers.link(user.id, 'openai', 'sk-synthetic-replacement-' + run_id)
        if providers.mark(old_key, ok=False) or not providers.mark(replacement, ok=True):
            raise AssertionError('Stale provider status changed a replacement credential')
        raw_key = store.database.linked_ai_accounts.find_one({'id': replacement.id})
        if not secret_box.is_encrypted(raw_key['api_key']):
            raise AssertionError('Provider credential was stored without encryption')
        providers.unlink(user.id, 'openai')
        if providers.mark(replacement, ok=True) or providers.list(user.id):
            raise AssertionError('Provider status recreated an unlinked credential')
        checks['atomic_provider_key_lifecycle'] = True
    finally:
        if original_encryption is None:
            os.environ.pop('DATA_ENCRYPTION_KEY', None)
        else:
            os.environ['DATA_ENCRYPTION_KEY'] = original_encryption
        secret_box.reset_cache()

    cards = MongoFlashcards(store)
    cards.ensure_tables()
    deck_id = cards.create_deck(user.id, f'Synthetic runtime deck {run_id}')
    added = cards.add_cards(user.id, deck_id, [
        {'front': f'Synthetic question {index}', 'back': f'Synthetic answer {index}'} for index in range(50)])
    if added != 50 or len(cards.due_queue(user.id, deck_id)) != 20:
        raise AssertionError('Native flashcard import or daily allowance failed')
    card_id = store.database.fc_cards.find_one({'deck_id': deck_id, 'user_id': user.id})['id']
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: cards.grade_card(user.id, card_id, 3), range(8)))
    saved_card = store.database.fc_cards.find_one({'id': card_id})
    if saved_card['reps'] != 8 or store.database.fc_reviews.count_documents({'card_id': card_id}) != 8:
        raise AssertionError('Concurrent flashcard grading lost history')
    if cards.grade_card(user.id + 1000000, card_id, 3) is not None:
        raise AssertionError('Flashcard grading crossed an account boundary')
    checks['concurrent_flashcard_reviews'] = True

    class OwnedTutor(MongoTutor):
        def _owner(self):
            return {'user_id': user.id}

    tutor = OwnedTutor(store)
    tutor.ensure_indexes()
    profile = tutor.get_or_create_profile()
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: tutor.update_mastery(profile['id'], 'Runtime', run_id, True), range(12)))
    mastery = tutor.list_mastery(profile['id'])
    if len(mastery) != 1 or mastery[0]['total_attempts'] != 12 or mastery[0]['correct_attempts'] != 12:
        raise AssertionError('Concurrent learner updates lost evidence')
    checks['concurrent_mastery_updates'] = True
    verify_sessions(store, run_id, user.id, 1)
    checks['native_session_lifecycle'] = True
    verify_erasure(store, run_id, user, tutor)
    if store.database.tutor_memory.find_one({'id': guest_memories[0]['id']}) is None:
        raise AssertionError('Account erasure removed guest tutor memory')
    checks['native_account_erasure_and_rollback'] = True
    store.database['_runtime_checks'].insert_one({'run_id': run_id, 'checks': checks,
                                             'application_ready': False})
    return {'run_id': run_id, 'checks': checks, 'application_ready': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', required=True, type=Path)
    args = parser.parse_args()
    values = dotenv_values(args.env_file)
    uri = values.get('MONGODB_URI')
    database = values.get('MONGODB_DATABASE')
    if not uri or not database:
        print(json.dumps({'status': 'configuration_required',
                          'required_keys': ['MONGODB_URI', 'MONGODB_DATABASE']}))
        return 2
    store = None
    try:
        store = MongoStore(uri, database)
        result = verify(store)
    except Exception as exc:
        # Driver exceptions can include server/connection details. Return a
        # classification only so test logs cannot disclose connection secrets.
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__,
                          'error_code': getattr(exc, 'code', None)}))
        return 1
    finally:
        if store is not None:
            store.close()
    print(json.dumps({'status': 'passed', **result}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
