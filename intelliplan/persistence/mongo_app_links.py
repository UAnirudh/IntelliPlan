"""Native mobile browser-ticket issuance, replacement and atomic redemption."""
from datetime import timedelta

from pymongo import ReturnDocument

import app_link
from intelliplan.persistence.app_links import AppLinkRedemption, allowed_path
from intelliplan.persistence.mongo_accounts import Account
from time_utils import utcnow


class MongoAppLinks:
    def __init__(self, store):
        self.store = store

    def ensure_indexes(self):
        self.store.database.app_link_codes.create_index('id', unique=True)
        self.store.database.app_link_codes.create_index('code_hash', unique=True)
        self.store.database.app_link_codes.create_index('user_id')

    def issue(self, user_id, *, code_hash, next_path, expected_epoch=None):
        if not allowed_path(next_path):
            raise ValueError('Unsupported browser destination')
        def write(db, active):
            owner = db.users.find_one_and_update({'id': int(user_id)}, {'$inc': {'_auth_revision': 1}},
                return_document=ReturnDocument.AFTER, session=active)
            if owner is None:
                raise LookupError('Account no longer exists')
            epoch = int(owner.get('session_epoch') or 0)
            if expected_epoch is not None and int(expected_epoch) != epoch:
                raise ValueError('Account credentials changed. Sign in again.')
            now = utcnow().isoformat()
            db.app_link_codes.update_many({'user_id': int(user_id), 'used_at': None},
                                         {'$set': {'used_at': now}}, session=active)
            db.app_link_codes.insert_one({'id': self.store.next_id('app_link_codes', session=active),
                'user_id': int(user_id), 'code_hash': code_hash, 'next_path': next_path,
                'session_epoch': epoch, 'created_at': now, 'used_at': None}, session=active)
        self.store.transaction(write)

    def redeem(self, code):
        if not app_link.is_valid_code(code):
            return None
        def write(db, active):
            now = utcnow()
            row = db.app_link_codes.find_one_and_update({'code_hash': app_link.hash_code(code), 'used_at': None,
                'created_at': {'$gt': (now - timedelta(seconds=app_link.CODE_TTL_SECONDS)).isoformat()}},
                {'$set': {'used_at': now.isoformat()}}, return_document=ReturnDocument.BEFORE, session=active)
            if row is None or not allowed_path(row['next_path']) or not app_link.codes_match(code, row['code_hash']):
                return None
            owner = db.users.find_one_and_update({'id': row['user_id']}, {'$inc': {'_auth_revision': 1}},
                return_document=ReturnDocument.AFTER, session=active)
            if owner is None or int(owner.get('session_epoch') or 0) != int(row.get('session_epoch') or 0):
                return None
            return AppLinkRedemption(Account(owner), int(row.get('session_epoch') or 0), row['next_path'])
        return self.store.transaction(write)
