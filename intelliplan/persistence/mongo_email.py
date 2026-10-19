"""Address suppressions persist independently of deleted/recreated accounts."""
import re

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from intelliplan.persistence.codec import pack_document, update_fields
from intelliplan.persistence.mongo_accounts import Account
from time_utils import utcnow


class MongoEmailState:
    def __init__(self, store):
        self.store = store

    def ensure_indexes(self):
        self.store.database.email_suppressions.create_index('id', unique=True)
        self.store.database.email_suppressions.create_index('email', unique=True)

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
