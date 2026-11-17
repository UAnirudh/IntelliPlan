"""Native MongoDB desktop handoff ticket issuance and atomic redemption."""
from datetime import timedelta

from pymongo import ReturnDocument

import desktop_auth
from intelliplan.persistence.desktop import DesktopRedemption
from intelliplan.persistence.mongo_accounts import Account
from time_utils import utcnow


class MongoDesktopTickets:
    def __init__(self, store):
        self.store = store

    def ensure_indexes(self):
        self.store.database.desktop_auth_codes.create_index('id', unique=True)
        self.store.database.desktop_auth_codes.create_index('code_hash', unique=True)
        self.store.database.desktop_auth_codes.create_index('user_id')

    def issue(self, user_id, *, code_hash, code_challenge, expected_epoch=None):
        if not desktop_auth.is_valid_challenge(code_challenge):
            raise ValueError('A valid desktop challenge is required')

        def write(db, active):
            owner = db.users.find_one_and_update({'id': int(user_id)}, {'$inc': {'_auth_revision': 1}},
                return_document=ReturnDocument.AFTER, session=active)
            if owner is None:
                raise LookupError('Account no longer exists')
            epoch = int(owner.get('session_epoch') or 0)
            if expected_epoch is not None and epoch != int(expected_epoch):
                raise ValueError('Account credentials changed. Sign in again.')
            db.desktop_auth_codes.insert_one({
                'id': self.store.next_id('desktop_auth_codes', session=active),
                'user_id': int(user_id), 'code_hash': code_hash, 'code_challenge': code_challenge,
                'created_at': utcnow().isoformat(), 'used_at': None, 'session_epoch': epoch}, session=active)

        self.store.transaction(write)

    def redeem(self, *, code_hash, verifier):
        if not desktop_auth.is_valid_verifier(verifier):
            return None

        def write(db, active):
            now = utcnow()
            row = db.desktop_auth_codes.find_one_and_update({
                'code_hash': code_hash, 'used_at': None,
                'created_at': {'$gt': (now - timedelta(seconds=desktop_auth.CODE_TTL_SECONDS)).isoformat()},
            }, {'$set': {'used_at': now.isoformat()}}, return_document=ReturnDocument.BEFORE,
                session=active)
            if row is None:
                return None
            if not desktop_auth.verifier_matches(verifier, row['code_challenge']):
                return None  # Commit the burn even when the verifier is wrong.
            owner = db.users.find_one_and_update({'id': row['user_id']},
                {'$inc': {'_auth_revision': 1}}, return_document=ReturnDocument.AFTER, session=active)
            if owner is None or int(owner.get('session_epoch') or 0) != int(row.get('session_epoch') or 0):
                return None
            return DesktopRedemption(Account(owner), int(row.get('session_epoch') or 0))

        return self.store.transaction(write)
