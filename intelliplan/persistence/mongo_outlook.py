"""Native Outlook calendar grant persistence and refresh conflict checks."""
from datetime import datetime
from types import SimpleNamespace

import secret_box
from intelliplan.persistence.codec import pack_document, unpack_document, update_fields
from intelliplan.persistence.outlook import same_grant
from time_utils import utcnow


class OutlookGrant(SimpleNamespace):
    def __init__(self, raw):
        values = {'account_email': None, 'account_name': None, 'connected_at': None, **unpack_document(raw)}
        values.pop('_id', None)
        values.pop('_ip_encoding', None)
        values['token_data'] = secret_box.decrypt(values.get('token_data'))
        if isinstance(values.get('connected_at'), str):
            values['connected_at'] = datetime.fromisoformat(values['connected_at'])
        super().__init__(**values)

    def __repr__(self):
        return f'OutlookGrant(id={self.id})'


class MongoOutlook:
    def __init__(self, store):
        self.store = store

    def ensure_indexes(self):
        self.store.database.outlook_integrations.create_index('id', unique=True)
        self.store.database.outlook_integrations.create_index('user_id', unique=True)

    @staticmethod
    def _lock(db, user_id, active):
        result = db.users.update_one({'id': int(user_id)}, {'$inc': {'_outlook_revision': 1}}, session=active)
        if not result.matched_count:
            raise LookupError('Account no longer exists')

    def get(self, user_id):
        raw = self.store.database.outlook_integrations.find_one({'user_id': int(user_id)})
        return OutlookGrant(raw) if raw is not None else None

    def connect(self, user_id, *, token_data, account_email, account_name):
        if not isinstance(token_data, str):
            raise ValueError('Invalid token data')
        def write(db, active):
            self._lock(db, user_id, active)
            raw = db.outlook_integrations.find_one({'user_id': int(user_id)}, session=active)
            values = {'token_data': secret_box.encrypt(token_data), 'account_email': account_email,
                      'account_name': account_name, 'connected_at': utcnow().isoformat()}
            if raw is None:
                values.update(id=self.store.next_id('outlook_integrations', session=active), user_id=int(user_id))
                db.outlook_integrations.insert_one(pack_document(values), session=active)
                return OutlookGrant(values)
            db.outlook_integrations.update_one({'_id': raw['_id']}, update_fields(values), session=active)
            return OutlookGrant(db.outlook_integrations.find_one({'_id': raw['_id']}, session=active))
        return self.store.transaction(write)

    def refresh(self, user_id, expected, token_data):
        if not isinstance(token_data, str):
            raise ValueError('Invalid token data')
        def write(db, active):
            self._lock(db, user_id, active)
            raw = db.outlook_integrations.find_one({'user_id': int(user_id)}, session=active)
            if raw is None or not same_grant(OutlookGrant(raw), expected):
                return False
            db.outlook_integrations.update_one({'_id': raw['_id']}, update_fields({
                'token_data': secret_box.encrypt(token_data)}), session=active)
            return True
        return self.store.transaction(write)

    def disconnect(self, user_id):
        def write(db, active):
            self._lock(db, user_id, active)
            db.outlook_integrations.delete_many({'user_id': int(user_id)}, session=active)
        self.store.transaction(write)
