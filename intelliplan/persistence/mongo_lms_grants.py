"""Native LMS grants with source-compatible fields and conditional refresh."""
from datetime import datetime

import secret_box

from intelliplan.persistence.codec import pack_document, update_fields
from intelliplan.persistence.lms_grants import LMSGrant, SCHEMAS, reconnect_values, same_grant, validate_fields
from time_utils import utcnow


class MongoLMSGrants:
    def __init__(self, store, provider):
        self.store, self.provider = store, provider
        self.collection_name, self.secrets, _ = SCHEMAS[provider]

    def ensure_indexes(self):
        collection = self.store.database[self.collection_name]
        collection.create_index('id', unique=True)
        collection.create_index('user_id')

    def get(self, user_id):
        raw = self.store.database[self.collection_name].find_one({'user_id': int(user_id)}, sort=[('id', -1)])
        return LMSGrant(self.provider, raw) if raw else None

    @staticmethod
    def _lock(db, user_id, active):
        result = db.users.update_one({'id': int(user_id)}, {'$inc': {'_lms_grant_revision': 1}}, session=active)
        if not result.matched_count:
            raise LookupError('Account no longer exists')

    def _write(self, user_id, values, expected=None):
        def write(db, active):
            self._lock(db, user_id, active)
            collection = db[self.collection_name]
            query = {'user_id': int(user_id)}
            if expected is not None:
                query['id'] = expected.id
            raw = collection.find_one(query, sort=[('id', -1)], session=active)
            if expected is not None and not same_grant(self.provider,
                    LMSGrant(self.provider, raw) if raw else None, expected):
                return False
            changes = reconnect_values(self.provider, LMSGrant(self.provider, raw) if raw else None, values) if expected is None else values
            stored = {key: secret_box.encrypt(value) if key in self.secrets else
                value.isoformat() if isinstance(value, datetime) else value for key, value in changes.items()}
            if raw is None:
                stored.update(id=self.store.next_id(self.collection_name, session=active), user_id=int(user_id))
                collection.insert_one(pack_document(stored), session=active)
                return LMSGrant(self.provider, stored)
            collection.update_one({'_id': raw['_id']}, update_fields(stored), session=active)
            return LMSGrant(self.provider, collection.find_one({'_id': raw['_id']}, session=active))
        return self.store.transaction(write)

    def connect(self, user_id, **values):
        return self._write(user_id, {**validate_fields(self.provider, values), 'connected_at': utcnow()})

    def refresh(self, user_id, expected, **values):
        if expected is None:
            raise ValueError('LMS refresh requires an existing grant')
        return self._write(user_id, validate_fields(self.provider, values, refresh=True), expected)

    def disconnect(self, user_id):
        def write(db, active):
            self._lock(db, user_id, active)
            db[self.collection_name].delete_many({'user_id': int(user_id)}, session=active)
        self.store.transaction(write)
