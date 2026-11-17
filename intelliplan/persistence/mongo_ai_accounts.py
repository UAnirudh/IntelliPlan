"""Encrypted provider credentials with owner-scoped lifecycle operations."""
from datetime import datetime
import hashlib
import hmac
from types import SimpleNamespace

import secret_box
from pymongo import ReturnDocument

from intelliplan.persistence.codec import pack_document, unpack_document, update_fields
from intelliplan.persistence.mongo_accounts import Account
from intelliplan.premium import byok
from time_utils import utcnow


class AIAccount(SimpleNamespace):
    def __init__(self, raw):
        values = {'api_key': '', 'key_hint': '', 'status': 'active', 'last_error': None,
                  'verified_at': None, 'last_used_at': None, **unpack_document(raw)}
        values.pop('_id', None)
        values.pop('_ip_encoding', None)
        values['_credential_proof'] = hashlib.sha256((values.get('api_key') or '').encode()).digest()
        value = secret_box.decrypt(values.get('api_key'))
        # An undecryptable ciphertext is not a usable provider credential.
        values['api_key'] = '' if secret_box.is_encrypted(value) else value
        for field in ('created_at', 'verified_at', 'last_used_at'):
            if isinstance(values.get(field), str):
                values[field] = datetime.fromisoformat(values[field])
        super().__init__(**values)

    def __repr__(self):
        return f'AIAccount(id={self.id}, provider={self.provider!r})'


class MongoAIAccounts:
    def __init__(self, store):
        self.store = store

    def ensure_indexes(self):
        self.store.database.linked_ai_accounts.create_index('id', unique=True)
        self.store.database.linked_ai_accounts.create_index([('user_id', 1), ('provider', 1)], unique=True)

    @staticmethod
    def _owner(db, user_id, active):
        raw = db.users.find_one_and_update({'id': int(user_id)}, {'$inc': {'_ai_accounts_revision': 1}},
            return_document=ReturnDocument.AFTER, session=active)
        if raw is None:
            raise LookupError('Account no longer exists')
        return Account(raw)

    def list(self, user_id, *, active_only=False):
        rows = [AIAccount(raw) for raw in self.store.database.linked_ai_accounts.find({'user_id': int(user_id)})]
        if active_only:
            rows = [row for row in rows if row.status == 'active' and row.api_key]
        order = {name: index for index, name in enumerate(byok.PREFERENCE)}
        return sorted(rows, key=lambda row: order.get(row.provider, 99))

    def link(self, user_id, provider, key):
        if byok.looks_valid(provider, key):
            raise ValueError('Invalid provider credential')
        if not secret_box.is_enabled():
            raise RuntimeError('Credential encryption is unavailable')
        encrypted = secret_box.encrypt(key)
        if not secret_box.is_encrypted(encrypted) or encrypted == key:
            raise RuntimeError('Credential encryption failed')
        def write(db, active):
            owner = self._owner(db, user_id, active)
            year = getattr(owner, 'birth_year', None)
            if not year or utcnow().year - int(year) - 1 < 18:
                raise PermissionError('Adult account required')
            raw = db.linked_ai_accounts.find_one({'user_id': int(user_id), 'provider': provider}, session=active)
            now = utcnow().isoformat()
            values = {'api_key': encrypted, 'key_hint': byok.hint(key), 'status': 'active',
                      'last_error': None, 'verified_at': now}
            if raw is None:
                values.update(id=self.store.next_id('linked_ai_accounts', session=active),
                              user_id=int(user_id), provider=provider, created_at=now, last_used_at=None)
                db.linked_ai_accounts.insert_one(pack_document(values), session=active)
                return AIAccount(values)
            db.linked_ai_accounts.update_one({'_id': raw['_id']}, update_fields(values), session=active)
            return AIAccount(db.linked_ai_accounts.find_one({'_id': raw['_id']}, session=active))
        return self.store.transaction(write)

    def unlink(self, user_id, provider):
        if not isinstance(provider, str):
            raise ValueError('Invalid provider')
        def write(db, active):
            self._owner(db, user_id, active)
            db.linked_ai_accounts.delete_many({'user_id': int(user_id), 'provider': provider}, session=active)
        self.store.transaction(write)

    def mark(self, expected, *, ok, error=None):
        def write(db, active):
            self._owner(db, expected.user_id, active)
            raw = db.linked_ai_accounts.find_one({'id': expected.id, 'user_id': expected.user_id,
                                                 'provider': expected.provider}, session=active)
            if raw is None:
                return False
            current = AIAccount(raw)
            if not hmac.compare_digest(current._credential_proof, expected._credential_proof):
                return False
            if not current.api_key or not expected.api_key or not hmac.compare_digest(
                    current.api_key.encode(), expected.api_key.encode()):
                return False
            # Persist a fixed message; provider exceptions may contain credentials.
            values = {'status': 'active', 'last_error': None, 'last_used_at': utcnow().isoformat()} if ok else {
                'status': 'invalid', 'last_error': 'The provider rejected that key.'}
            db.linked_ai_accounts.update_one({'_id': raw['_id']}, update_fields(values), session=active)
            return True
        return self.store.transaction(write)
