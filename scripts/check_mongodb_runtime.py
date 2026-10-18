"""Verify the native driver in a dedicated test database without source writes.

Usage: python scripts/check_mongodb_runtime.py --env-file <private file>
Credentials are read by the driver and never included in reports or arguments.
The test records are retained for inspection; no database is dropped or cleared.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values  # noqa: E402
from flask import Flask, session  # noqa: E402

from adaptive_tutor.mongo_store import MongoTutor  # noqa: E402
import desktop_auth  # noqa: E402
from flashcards.mongo_store import MongoFlashcards  # noqa: E402
from intelliplan.persistence.accounts import PasswordConflict  # noqa: E402
from intelliplan.persistence.mongo import MongoStore  # noqa: E402
from intelliplan.persistence.mongo_accounts import MongoAccounts  # noqa: E402
from intelliplan.persistence.mongo_desktop import MongoDesktopTickets  # noqa: E402
from intelliplan.persistence.mongo_identity import MongoIdentities  # noqa: E402
from intelliplan.persistence.mongo_extensions import MongoExtensionTokens  # noqa: E402
from intelliplan.persistence.sessions import MongoSessionInterface  # noqa: E402


class DeliberateRollback(Exception):
    pass


def verify_sessions(store, run_id):
    cookie_name = f'runtime_{run_id}'

    def session_app(name):
        app = Flask(name)
        app.config.update(SECRET_KEY='synthetic-runtime-only', SESSION_COOKIE_NAME=cookie_name,
                          SESSION_KEY_PREFIX=f'runtime:{run_id}:')
        app.session_interface = MongoSessionInterface(app, store.database)

        @app.get('/write')
        def write():
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
    verify_sessions(store, run_id)
    checks['native_session_lifecycle'] = True
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
