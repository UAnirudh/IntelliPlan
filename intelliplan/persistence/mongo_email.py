"""Address suppressions persist independently of deleted/recreated accounts."""
import re
import secrets
from types import SimpleNamespace

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from intelliplan.persistence.codec import pack_document, unpack_document, update_fields
from intelliplan.persistence.mongo_accounts import Account
from time_utils import utcnow


class MongoEmailState:
    def __init__(self, store):
        self.store = store

    def ensure_indexes(self):
        self.store.database.email_suppressions.create_index('id', unique=True)
        self.store.database.email_suppressions.create_index('email', unique=True)
        self.store.database.email_sends.create_index('id', unique=True)
        self.store.database.email_sends.create_index([('user_id', 1), ('email_key', 1)], unique=True)

    def is_suppressed(self, address):
        return self.store.database.email_suppressions.find_one(
            {'email': address.strip().lower()}, {'_id': 1}) is not None

    def suppress(self, address, reason='unsubscribe'):
        address = address.strip().lower()
        if not address:
            return False
        def write(db, active):
            if db.email_suppressions.find_one({'email': address}, session=active):
                return True
            db.email_suppressions.insert_one(pack_document({
                'id': self.store.next_id('email_suppressions', session=active),
                'email': address, 'reason': reason[:32], 'created_at': utcnow().isoformat(),
            }), session=active)
            return True
        try:
            return self.store.transaction(write)
        except DuplicateKeyError:
            return self.is_suppressed(address)

    def claim_budget_notice(self, user_id, period):
        if not isinstance(period, str) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period):
            raise ValueError('Invalid budget period')
        def write(db, active):
            raw = db.users.find_one_and_update(
                {'id': int(user_id), '$or': [
                    {'ai_low_budget_notified': None}, {'ai_low_budget_notified': {'$lt': period}}]},
                update_fields({'ai_low_budget_notified': period}),
                return_document=ReturnDocument.AFTER, session=active)
            return Account(raw) if raw is not None else None
        return self.store.transaction(write)

    @staticmethod
    def _owner(db, user_id, active):
        result = db.users.update_one({'id': int(user_id)}, {'$inc': {'_email_revision': 1}}, session=active)
        if not result.matched_count:
            raise LookupError('Account no longer exists')

    def claim_send(self, user_id, email_key):
        if not isinstance(email_key, str) or not email_key or len(email_key) > 64:
            raise ValueError('Invalid email identity')
        def write(db, active):
            self._owner(db, user_id, active)
            raw = db.email_sends.find_one({'user_id': int(user_id), 'email_key': email_key}, session=active)
            if raw is not None and raw.get('status') != 'failed':
                return None
            values = {'status': 'pending', '_claim_token': secrets.token_hex(16)}
            if raw is None:
                values.update(id=self.store.next_id('email_sends', session=active), user_id=int(user_id),
                              email_key=email_key, sent_at=utcnow().isoformat(), provider_message_id=None)
                db.email_sends.insert_one(pack_document(values), session=active)
                return SimpleNamespace(**values)
            db.email_sends.update_one({'_id': raw['_id']}, update_fields(values), session=active)
            return SimpleNamespace(**{**unpack_document(raw), **values})
        return self.store.transaction(write)

    def record_send(self, expected, status, message_id=None):
        if status not in {'sent', 'failed'}:
            raise ValueError('Invalid email outcome')
        def write(db, active):
            self._owner(db, expected.user_id, active)
            values = {'status': status}
            if isinstance(message_id, str):
                values['provider_message_id'] = message_id[:128]
            result = db.email_sends.update_one({'id': expected.id, 'user_id': expected.user_id,
                'email_key': expected.email_key, 'status': 'pending', '_claim_token': expected._claim_token},
                update_fields(values), session=active)
            return result.matched_count == 1
        return self.store.transaction(write)
