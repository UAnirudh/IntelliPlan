"""Owner-scoped native tutor conversation and profile-memory storage."""
from datetime import datetime
import json

from pymongo.errors import DuplicateKeyError

from intelliplan.persistence.codec import pack_document, unpack_document, update_fields
from intelliplan.persistence.json_values import dumps_exact, loads_exact
from time_utils import utcnow


class MongoTutorHistory:
    def __init__(self, store, owner, normalize, merge, default_profile):
        self.store, self.owner = store, owner
        self.normalize, self.merge, self.default_profile = normalize, merge, default_profile

    def ensure_indexes(self):
        for name in ('tutor_memory', 'tutor_conversations'):
            collection = self.store.database[name]
            collection.create_index('id', unique=True)
            collection.create_index([('user_id', 1), ('updated_at', -1)])
            collection.create_index([('guest_session_id', 1), ('updated_at', -1)])
        self.store.database.tutor_memory.create_index('user_id', unique=True,
            partialFilterExpression={'user_id': {'$type': 'int'}})
        self.store.database.tutor_memory.create_index('guest_session_id', unique=True,
            partialFilterExpression={'guest_session_id': {'$type': 'string'}, 'user_id': None})

    def _scope(self):
        user_id, guest_id = self.owner()
        if user_id is not None:
            return {'user_id': int(user_id)}
        if not isinstance(guest_id, str) or not guest_id:
            raise ValueError('A tutor owner is required')
        return {'user_id': None, 'guest_session_id': guest_id}

    @staticmethod
    def _lock_owner(db, scope, active):
        if scope['user_id'] is not None:
            if not db.users.update_one({'id': scope['user_id']}, {'$inc': {'_tutor_history_revision': 1}},
                                       session=active).matched_count:
                raise LookupError('Account no longer exists')

    @staticmethod
    def _view(raw):
        if raw is None:
            return None
        row = unpack_document(raw)
        row.pop('_id', None)
        row.pop('_ip_encoding', None)
        for name in ('created_at', 'updated_at'):
            if isinstance(row.get(name), str):
                row[name] = datetime.fromisoformat(row[name])
        return row

    def load_memory(self):
        scope = self._scope()
        def write(db, active):
            self._lock_owner(db, scope, active)
            row = db.tutor_memory.find_one(scope, session=active)
            if row is None:
                now = utcnow().isoformat()
                row = {**scope, 'guest_session_id': scope.get('guest_session_id'),
                       'id': self.store.next_id('tutor_memory', session=active),
                       'messages_json': '[]', 'profile_json': dumps_exact(self.default_profile()),
                       'created_at': now, 'updated_at': now}
                db.tutor_memory.insert_one(pack_document(row), session=active)
            return self._view(row)
        try:
            return self.store.transaction(write)
        except DuplicateKeyError:
            # Guest creation can race across processes. Only accept an actual
            # owner-matching winner; other index failures still propagate.
            row = self.store.database.tutor_memory.find_one(scope)
            if row is None:
                raise
            return self._view(row)

    def _mutate(self, collection, identity, change):
        scope = self._scope()
        def write(db, active):
            self._lock_owner(db, scope, active)
            query = {**scope, 'id': int(identity)}
            row = db[collection].find_one(query, session=active)
            if row is None:
                raise LookupError('Tutor record not found')
            values = change(self._view(row))
            values['updated_at'] = utcnow().isoformat()
            db[collection].update_one({'_id': row['_id']}, update_fields(values), session=active)
            return self._view(db[collection].find_one({'_id': row['_id']}, session=active))
        return self.store.transaction(write)

    def save_memory(self, identity, messages, profile):
        return self._mutate('tutor_memory', identity, lambda row: {
            'messages_json': json.dumps(self.merge(json.loads(row['messages_json']), messages)),
            'profile_json': dumps_exact(profile)})

    def save_profile(self, identity, profile):
        return self._mutate('tutor_memory', identity, lambda row: {'profile_json': dumps_exact(profile)})

    def record_profile_turn(self, identity, text, reply, update_profile):
        return self._mutate('tutor_memory', identity, lambda row: {
            'profile_json': dumps_exact(update_profile(loads_exact(row['profile_json']), text, reply))})

    def list_conversations(self):
        rows = self.store.database.tutor_conversations.find(self._scope(), {
            'id': 1, 'title': 1, 'updated_at': 1}).sort([('updated_at', -1), ('id', -1)])
        return [{'id': row['id'], 'title': row['title'],
                 'updated_at': row['updated_at'].isoformat() if row.get('updated_at') else None}
                for row in map(self._view, rows)]

    def get_conversation(self, identity):
        return self._view(self.store.database.tutor_conversations.find_one({**self._scope(), 'id': int(identity)}))

    def create_conversation(self, title='New chat'):
        scope = self._scope()
        def write(db, active):
            self._lock_owner(db, scope, active)
            now = utcnow().isoformat()
            identity = self.store.next_id('tutor_conversations', session=active)
            db.tutor_conversations.insert_one(pack_document({**scope, 'guest_session_id': scope.get('guest_session_id'),
                'id': identity, 'title': str(title)[:160], 'messages_json': '[]',
                'created_at': now, 'updated_at': now}), session=active)
            return identity
        return self.store.transaction(write)

    def save_conversation(self, identity, messages, title=None):
        def change(row):
            values = {'messages_json': json.dumps(self.merge(json.loads(row['messages_json']), messages))}
            if title:
                values['title'] = str(title)[:160]
            return values
        return self._mutate('tutor_conversations', identity, change)

    def rename_conversation(self, identity, title):
        return self._mutate('tutor_conversations', identity, lambda row: {'title': str(title)[:160]})

    def delete_conversation(self, identity):
        scope = self._scope()
        def write(db, active):
            self._lock_owner(db, scope, active)
            return bool(db.tutor_conversations.delete_one({**scope, 'id': int(identity)}, session=active).deleted_count)
        return self.store.transaction(write)
