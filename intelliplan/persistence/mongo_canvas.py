"""Native Canvas OAuth transactions over migrated encrypted tokens."""
from datetime import datetime
import json
from types import SimpleNamespace
import uuid

import secret_box
from intelliplan.persistence.canvas import CANVAS_SECRETS, same_connection
from intelliplan.persistence.codec import pack_document, unpack_document, update_fields
from intelliplan.persistence.mongo_linked_accounts import LinkedAccount, MongoLinkedAccounts
from time_utils import utcnow


def canvas_record(document):
    if document is None:
        return None
    values = unpack_document(document)
    values.pop('_id', None)
    values.pop('_ip_encoding', None)
    for key in ('access_token', 'refresh_token'):
        values[key] = secret_box.decrypt(values.get(key))
    for key in ('token_expires_at', 'connected_at'):
        if isinstance(values.get(key), str):
            values[key] = datetime.fromisoformat(values[key])
    return SimpleNamespace(**values)


class MongoCanvas:
    def __init__(self, store):
        self.store = store

    def ensure_indexes(self):
        self.store.database.canvas_integrations.create_index('id', unique=True)
        self.store.database.canvas_integrations.create_index('user_id')

    def get(self, user_id):
        return canvas_record(self.store.database.canvas_integrations.find_one({'user_id': int(user_id)}, sort=[('id', 1)]))

    def connect(self, user_id, *, canvas_base, access_token, refresh_token,
                expires_at, canvas_user_id, canvas_user_name, profile_name, credentials):
        def write(db, active):
            MongoLinkedAccounts._lock_owner(db, user_id, active)
            raw = db.canvas_integrations.find_one({'user_id': int(user_id)}, sort=[('id', 1)], session=active)
            old = canvas_record(raw)
            values = {'canvas_base': canvas_base, 'access_token': secret_box.encrypt(access_token),
                      'refresh_token': secret_box.encrypt(refresh_token or (old.refresh_token if old else None)),
                      'token_expires_at': expires_at.isoformat() if expires_at else None,
                      'canvas_user_id': canvas_user_id or (getattr(old, 'canvas_user_id', None) if old else None),
                      'canvas_user_name': canvas_user_name or (getattr(old, 'canvas_user_name', None) if old else None),
                      'connected_at': utcnow().isoformat()}
            if raw is None:
                values.update(id=self.store.next_id('canvas_integrations', session=active), user_id=int(user_id))
                db.canvas_integrations.insert_one(pack_document(values), session=active)
            else:
                db.canvas_integrations.update_one({'_id': raw['_id']}, update_fields(values), session=active)
            db.linked_accounts.update_many({'user_id': int(user_id)}, update_fields({'is_active': False}), session=active)
            db.linked_accounts.insert_one(pack_document({
                'id': self.store.next_id('linked_accounts', session=active), 'user_id': int(user_id),
                'profile_id': uuid.uuid4().hex[:16], 'name': canvas_user_name or profile_name,
                'login_type': 'canvas', 'is_active': True, 'created_at': utcnow().isoformat(),
                'credentials': secret_box.encrypt(json.dumps(credentials)),
            }), session=active)
        self.store.transaction(write)

    def refresh(self, user_id, expected, *, access_token, refresh_token, expires_at):
        def write(db, active):
            MongoLinkedAccounts._lock_owner(db, user_id, active)
            raw = db.canvas_integrations.find_one({'user_id': int(user_id)}, sort=[('id', 1)], session=active)
            row = canvas_record(raw)
            if not same_connection(row, expected):
                return False
            values = {'access_token': secret_box.encrypt(access_token),
                      'token_expires_at': expires_at.isoformat() if expires_at else None}
            if refresh_token:
                values['refresh_token'] = secret_box.encrypt(refresh_token)
            db.canvas_integrations.update_one({'_id': raw['_id']}, update_fields(values), session=active)
            for raw_link in db.linked_accounts.find({'user_id': int(user_id), 'is_active': True,
                                                     'login_type': 'canvas'}, session=active):
                credentials = LinkedAccount(raw_link).get_credentials()
                if not credentials.get('canvas_oauth') or credentials.get('canvas_url') != row.canvas_base:
                    continue
                credentials.update(canvas_token=access_token,
                                   canvas_token_expires_at=expires_at.isoformat() if expires_at else None)
                if refresh_token:
                    credentials['canvas_refresh_token'] = refresh_token
                db.linked_accounts.update_one({'_id': raw_link['_id']}, update_fields({
                    'credentials': secret_box.encrypt(json.dumps(credentials))}), session=active)
            return True
        return self.store.transaction(write)

    def disconnect(self, user_id, expected):
        def write(db, active):
            MongoLinkedAccounts._lock_owner(db, user_id, active)
            raw = db.canvas_integrations.find_one({'user_id': int(user_id)}, sort=[('id', 1)], session=active)
            if not same_connection(canvas_record(raw), expected):
                return False
            if raw is not None:
                db.canvas_integrations.delete_one({'_id': raw['_id']}, session=active)
            for raw_link in db.linked_accounts.find({'user_id': int(user_id), 'login_type': 'canvas'}, session=active):
                credentials = LinkedAccount(raw_link).get_credentials()
                for key in CANVAS_SECRETS:
                    credentials.pop(key, None)
                db.linked_accounts.update_one({'_id': raw_link['_id']}, update_fields({
                    'credentials': secret_box.encrypt(json.dumps(credentials)), 'is_active': False}), session=active)
            return True
        return self.store.transaction(write)
