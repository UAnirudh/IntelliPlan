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

from adaptive_tutor.mongo_store import MongoTutor  # noqa: E402
import desktop_auth  # noqa: E402
from intelliplan.persistence.mongo import MongoStore  # noqa: E402
from intelliplan.persistence.mongo_accounts import MongoAccounts  # noqa: E402
from intelliplan.persistence.mongo_desktop import MongoDesktopTickets  # noqa: E402
from intelliplan.persistence.mongo_identity import MongoIdentities  # noqa: E402


class DeliberateRollback(Exception):
    pass


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
