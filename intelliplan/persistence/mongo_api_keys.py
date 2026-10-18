"""Native API credential verification and atomic usage metadata."""
from types import SimpleNamespace

from intelliplan.persistence.codec import unpack_document
from time_utils import utcnow


class APIKey(SimpleNamespace):
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
        values = {'scopes': '', 'rate_limit_per_min': 60, 'request_count': 0, **unpack_document(rows[0])}
        values.pop('_id', None)
        values.pop('_ip_encoding', None)
        return APIKey(**values)

    def touch(self, identity, user_id):
        self.store.database.api_keys.update_one({'id': int(identity), 'user_id': int(user_id), 'status': 'active'}, [{
            '$set': {'last_used_at': {'$literal': utcnow().isoformat()},
                     'request_count': {'$add': [{'$ifNull': ['$request_count', 0]}, 1]}}
        }])
