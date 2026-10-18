"""Native API credential verification and atomic usage metadata."""
from types import SimpleNamespace
from datetime import datetime

from pymongo import ReturnDocument

from intelliplan.persistence.codec import pack_document, unpack_document, update_fields
from intelliplan.persistence.mongo_accounts import Account
from time_utils import utcnow


class KeySlotsFull(ValueError):
    pass


class KeyStateConflict(ValueError):
    pass


class APIKey(SimpleNamespace):
    def __init__(self, document):
        values = {'app_name': '', 'app_url': '', 'use_case': '', 'expected_volume': 'low', 'contact_email': '',
                  'scopes': '', 'key_prefix': '', 'key_hash': '', 'status': 'pending', 'review_note': '',
                  'rate_limit_per_min': 60, 'request_count': 0,
                  'created_at': None, 'approved_at': None, 'revoked_at': None, 'last_used_at': None,
                  **unpack_document(document)}
        values.pop('_id', None)
        values.pop('_ip_encoding', None)
        for name in ('created_at', 'approved_at', 'revoked_at', 'last_used_at', 'accepted_terms_at'):
            if isinstance(values.get(name), str):
                values[name] = datetime.fromisoformat(values[name])
        super().__init__(**values)

    def to_dict(self):
        result = {name: getattr(self, name) for name in ('id', 'app_name', 'app_url', 'use_case',
            'expected_volume', 'key_prefix', 'status', 'review_note')}
        result.update(scopes=self.scope_list(), rate_limit_per_min=self.rate_limit_per_min or 60,
                      request_count=self.request_count or 0)
        for name in ('created_at', 'approved_at', 'revoked_at', 'last_used_at'):
            value = getattr(self, name)
            result[name] = value.isoformat() if value else None
        return result

    def scope_list(self):
        return [scope for scope in (self.scopes or '').split() if scope]


class MongoAPIKeys:
    def __init__(self, store):
        self.store = store

    def ensure_indexes(self):
        self.store.database.api_keys.create_index('id', unique=True)
        self.store.database.api_keys.create_index('key_hash')
        self.store.database.api_keys.create_index('user_id')

    def resolve_digest(self, digest):
        rows = list(self.store.database.api_keys.find({'key_hash': digest, 'status': 'active'}).limit(2))
        if len(rows) != 1:
            return None
        return APIKey(rows[0])

    def touch(self, identity, user_id):
        self.store.database.api_keys.update_one({'id': int(identity), 'user_id': int(user_id), 'status': 'active'}, [{
            '$set': {'last_used_at': {'$literal': utcnow().isoformat()},
                     'request_count': {'$add': [{'$ifNull': ['$request_count', 0]}, 1]}}
        }])


    def get(self, identity, *, user_id=None):
        query = {'id': int(identity)}
        if user_id is not None:
            query['user_id'] = int(user_id)
        row = self.store.database.api_keys.find_one(query)
        return APIKey(row) if row is not None else None

    def list(self, *, user_id=None, status=None, limit=None):
        query = {}
        if user_id is not None:
            query['user_id'] = int(user_id)
        if status and status != 'all':
            query['status'] = status
        rows = self.store.database.api_keys.find(query).sort([('created_at', -1), ('id', -1)])
        if limit is not None:
            rows = rows.limit(int(limit))
        return [APIKey(row) for row in rows]

    @staticmethod
    def _lock_owner(db, user_id, active):
        row = db.users.find_one_and_update({'id': int(user_id)}, {'$inc': {'_api_keys_revision': 1}},
            return_document=ReturnDocument.AFTER, session=active)
        if row is None:
            raise LookupError('Account no longer exists')
        return Account(row)

    def submit(self, user_id, values):
        import api_keys
        values = dict(values)
        if values.keys() - {'app_name', 'app_url', 'use_case', 'expected_volume', 'contact_email', 'scopes'}:
            raise ValueError('Unsupported API application fields')
        scopes = values.get('scopes')
        if not isinstance(scopes, str) or not scopes.split() or any(scope not in api_keys.SCOPES for scope in scopes.split()):
            raise ValueError('Unsupported API scopes')
        def write(db, active):
            user = self._lock_owner(db, user_id, active)
            count = db.api_keys.count_documents({'user_id': int(user_id), 'status': {'$in': ['pending', 'active']}}, session=active)
            if count >= api_keys.MAX_KEYS_PER_USER:
                raise KeySlotsFull('API application limit reached')
            now = utcnow().isoformat()
            row = {**values, 'id': self.store.next_id('api_keys', session=active), 'user_id': int(user_id),
                   'status': 'pending', 'rate_limit_per_min': api_keys.VOLUME_LIMITS['low'],
                   'created_at': now, 'accepted_terms_at': now, 'request_count': 0}
            secret = None
            if api_keys.auto_approvable(user, row['scopes'].split()):
                secret, prefix, digest = api_keys.generate_key()
                row.update(key_prefix=prefix, key_hash=digest, status='active', approved_at=now,
                           review_note='Auto-approved: read-only scopes.')
            db.api_keys.insert_one(pack_document(row), session=active)
            return APIKey(row), secret
        return self.store.transaction(write)

    def revoke(self, user_id, identity):
        def write(db, active):
            self._lock_owner(db, user_id, active)
            row = db.api_keys.find_one({'id': int(identity), 'user_id': int(user_id)}, session=active)
            if row is None:
                return None
            if row.get('status') != 'revoked':
                db.api_keys.update_one({'_id': row['_id']}, update_fields({
                    'status': 'revoked', 'revoked_at': utcnow().isoformat(), 'key_hash': ''}), session=active)
            return APIKey(db.api_keys.find_one({'_id': row['_id']}, session=active))
        return self.store.transaction(write)

    def roll(self, user_id, identity):
        import api_keys
        def write(db, active):
            self._lock_owner(db, user_id, active)
            row = db.api_keys.find_one({'id': int(identity), 'user_id': int(user_id)}, session=active)
            if row is None:
                return None, None
            if row.get('status') != 'active':
                raise KeyStateConflict('Only an active key can be rolled')
            secret, prefix, digest = api_keys.generate_key()
            db.api_keys.update_one({'_id': row['_id']}, update_fields({'key_prefix': prefix, 'key_hash': digest}), session=active)
            return APIKey(db.api_keys.find_one({'_id': row['_id']}, session=active)), secret
        return self.store.transaction(write)

    def decide(self, identity, approved, note):
        import api_keys
        def write(db, active):
            row = db.api_keys.find_one({'id': int(identity)}, session=active)
            if row is None:
                return None, None
            self._lock_owner(db, row['user_id'], active)
            if row.get('status') != 'pending':
                raise KeyStateConflict('This application has already been decided')
            values = {'status': 'denied', 'review_note': str(note)[:500]}
            secret = None
            if approved:
                secret, prefix, digest = api_keys.generate_key()
                values.update(status='active', key_prefix=prefix, key_hash=digest,
                    approved_at=utcnow().isoformat(), revoked_at=None,
                    rate_limit_per_min=api_keys.VOLUME_LIMITS.get(row.get('expected_volume') or 'low', 60))
            db.api_keys.update_one({'_id': row['_id']}, update_fields(values), session=active)
            return APIKey(db.api_keys.find_one({'_id': row['_id']}, session=active)), secret
        return self.store.transaction(write)
