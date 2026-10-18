"""Native LMS profiles over the original encrypted credential values."""
from datetime import datetime
import json
from types import SimpleNamespace
import uuid

import secret_box
from intelliplan.persistence.codec import pack_document, unpack_document, update_fields
from time_utils import utcnow


class LinkedAccount(SimpleNamespace):
    def __init__(self, document):
        values = unpack_document(document)
        values.pop('_id', None)
        values.pop('_ip_encoding', None)
        if isinstance(values.get('created_at'), str):
            values['created_at'] = datetime.fromisoformat(values['created_at'])
        super().__init__(**values)

    def get_credentials(self):
        return json.loads(secret_box.decrypt(self.credentials))


class MongoLinkedAccounts:
    def __init__(self, store):
        self.store = store
        self.collection = store.database.linked_accounts

    def ensure_indexes(self):
        self.collection.create_index('id', unique=True)
        self.collection.create_index('profile_id', unique=True)
        self.collection.create_index([('user_id', 1), ('is_active', 1), ('id', 1)])
        self.collection.create_index([('user_id', 1), ('login_type', 1)])

    def list(self, user_id, *, login_type=None, active=None):
        query = {'user_id': int(user_id)}
        if login_type is not None:
            query['login_type'] = login_type
        if active is not None:
            query['is_active'] = bool(active)
        return [LinkedAccount(row) for row in self.collection.find(query).sort('id', 1)]

    def active(self, user_id):
        row = self.collection.find_one({'user_id': int(user_id), 'is_active': True}, sort=[('id', 1)])
        return LinkedAccount(row) if row is not None else None

    def by_type(self, user_id, login_type):
        row = self.collection.find_one({'user_id': int(user_id), 'login_type': login_type},
                                       sort=[('is_active', -1), ('id', -1)])
        return LinkedAccount(row) if row is not None else None

    @staticmethod
    def _lock_owner(db, user_id, active):
        if not db.users.update_one({'id': int(user_id)}, {'$inc': {'_linked_accounts_revision': 1}},
                                   session=active).matched_count:
            raise LookupError('Account no longer exists')

    def create_active(self, user_id, name, login_type, credentials):
        # Encrypt before entering the retryable transaction. No credentials are logged.
        sealed = secret_box.encrypt(json.dumps(credentials))
        profile_id = uuid.uuid4().hex[:16]
        def write(db, active):
            self._lock_owner(db, user_id, active)
            row = {'id': self.store.next_id('linked_accounts', session=active),
                   'user_id': int(user_id), 'profile_id': profile_id,
                   'name': name, 'login_type': login_type, 'is_active': True,
                   'credentials': sealed, 'created_at': utcnow().isoformat()}
            db.linked_accounts.update_many({'user_id': int(user_id)},
                                           update_fields({'is_active': False}), session=active)
            db.linked_accounts.insert_one(pack_document(row), session=active)
            return LinkedAccount(row)
        return self.store.transaction(write)

    def switch(self, user_id, profile_id):
        if not isinstance(profile_id, str) or not profile_id:
            return False
        def write(db, active):
            self._lock_owner(db, user_id, active)
            query = {'user_id': int(user_id), 'profile_id': profile_id}
            row = db.linked_accounts.find_one(query, session=active)
            if row is None:
                return False
            db.linked_accounts.update_many({'user_id': int(user_id)},
                                           update_fields({'is_active': False}), session=active)
            db.linked_accounts.update_one(query, update_fields({'is_active': True}), session=active)
            return True
        return self.store.transaction(write)

    def delete(self, user_id, profile_id):
        return self._change(user_id, profile_id, None)

    def rename(self, user_id, profile_id, name):
        return self._change(user_id, profile_id, {'name': name})

    def _change(self, user_id, profile_id, values):
        if not isinstance(profile_id, str) or not profile_id:
            return False
        def write(db, active):
            self._lock_owner(db, user_id, active)
            query = {'user_id': int(user_id), 'profile_id': profile_id}
            if values is None:
                return bool(db.linked_accounts.delete_one(query, session=active).deleted_count)
            return bool(db.linked_accounts.update_one(query, update_fields(values), session=active).matched_count)
        return self.store.transaction(write)
