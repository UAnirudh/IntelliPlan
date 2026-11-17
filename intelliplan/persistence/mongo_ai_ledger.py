"""Native AI spend and credit ledger; all money remains integer micro-dollars."""
from datetime import datetime
from types import SimpleNamespace

from pymongo.errors import DuplicateKeyError

from intelliplan.persistence.codec import pack_document, unpack_document


class MongoAILedger:
    def __init__(self, store):
        self.store = store

    def ensure_indexes(self):
        for name in ('ai_spend_events', 'ai_credit_grants'):
            collection = self.store.database[name]
            collection.create_index('id', unique=True)
            collection.create_index([('user_id', 1), ('period', 1)])
        self.store.database.ai_credit_grants.create_index(
            'stripe_ref', unique=True, partialFilterExpression={'stripe_ref': {'$type': 'string'}})

    @staticmethod
    def _owner(db, user_id, active):
        # Coordinate with account erasure and serialize writes for this owner.
        result = db.users.update_one({'id': int(user_id)}, {'$inc': {'_ai_ledger_revision': 1}}, session=active)
        if not result.matched_count:
            raise LookupError('Account no longer exists')

    def spent(self, user_id, period, source):
        rows = self.store.database.ai_spend_events.find(
            {'user_id': int(user_id), 'period': period, 'source': source}, {'cost_micro': 1})
        return sum(int(row.get('cost_micro') or 0) for row in rows)

    def granted(self, user_id, period):
        rows = self.store.database.ai_credit_grants.find(
            {'user_id': int(user_id), 'period': period}, {'amount_micro': 1})
        return sum(int(row.get('amount_micro') or 0) for row in rows)

    def record(self, user_id, values):
        fields = {'period', 'created_at', 'provider', 'model', 'source', 'purpose', 'tier',
                  'input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'cost_micro'}
        if values.keys() != fields:
            raise ValueError('Unsupported spend fields')
        def write(db, active):
            self._owner(db, user_id, active)
            row = {**values, 'user_id': int(user_id),
                   'id': self.store.next_id('ai_spend_events', session=active)}
            db.ai_spend_events.insert_one(pack_document(row), session=active)
        self.store.transaction(write)

    def has_credit(self, user_id, reference):
        return self.store.database.ai_credit_grants.find_one(
            {'user_id': int(user_id), 'stripe_ref': reference}, {'_id': 1}) is not None

    def credit(self, user_id, amount_micro, *, period, reason, stripe_ref, now):
        if type(amount_micro) is not int or not amount_micro:
            return False
        if stripe_ref is not None and (not isinstance(stripe_ref, str) or not stripe_ref or len(stripe_ref) > 128):
            raise ValueError('Invalid credit reference')
        def write(db, active):
            self._owner(db, user_id, active)
            if stripe_ref and db.ai_credit_grants.find_one({'stripe_ref': stripe_ref}, session=active):
                return False
            row = {'id': self.store.next_id('ai_credit_grants', session=active), 'user_id': int(user_id),
                   'period': period, 'amount_micro': amount_micro, 'reason': reason[:16],
                   'stripe_ref': stripe_ref, 'created_at': now.isoformat()}
            db.ai_credit_grants.insert_one(pack_document(row), session=active)
            return True
        try:
            return self.store.transaction(write)
        except DuplicateKeyError:
            # Only a persisted identical reference counts as duplicate success.
            if stripe_ref and self.store.database.ai_credit_grants.find_one({'stripe_ref': stripe_ref}):
                return False
            raise

    def usage(self, user_id, period):
        rows = self.store.database.ai_spend_events.find(
            {'user_id': int(user_id), 'period': period, 'source': {'$ne': 'router'}})
        result = []
        for row in rows:
            values = unpack_document(row)
            when = values.get('created_at')
            if isinstance(when, str):
                values['created_at'] = datetime.fromisoformat(when)
            result.append(SimpleNamespace(**values))
        return result
