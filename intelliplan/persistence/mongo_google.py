"""Native multi-account Google grants, active selection and refresh proof."""
import secret_box

from intelliplan.persistence.codec import pack_document, update_fields
from intelliplan.persistence.google import merged_token
from intelliplan.persistence.mongo_accounts import Account
from intelliplan.persistence.mongo_outlook import OutlookGrant
from intelliplan.persistence.outlook import same_grant
from time_utils import utcnow


class MongoGoogle:
    def __init__(self, store, kind='calendar'):
        if kind not in {'calendar', 'drive'}:
            raise ValueError('Unsupported Google integration')
        self.store, self.kind = store, kind
        self.collection_name = 'google_integrations' if kind == 'calendar' else 'google_drive_integrations'

    def ensure_indexes(self):
        collection = self.store.database[self.collection_name]
        collection.create_index('id', unique=True)
        # Source allowed duplicate/null account emails; retain every restored row.
        collection.create_index([('user_id', 1), ('account_email', 1)])

    @staticmethod
    def _lock(db, user_id, active):
        from pymongo import ReturnDocument
        raw = db.users.find_one_and_update({'id': int(user_id)}, {'$inc': {'_google_revision': 1}},
            return_document=ReturnDocument.AFTER, session=active)
        if raw is None:
            raise LookupError('Account no longer exists')
        return Account(raw)

    def list(self, user_id):
        return [OutlookGrant(raw) for raw in self.store.database[self.collection_name].find(
            {'user_id': int(user_id)}).sort('id', 1)]

    def get(self, user_id, account_email=None):
        if account_email is not None and not isinstance(account_email, str):
            raise ValueError('Invalid Google account selector')
        rows = self.list(user_id)
        if account_email is not None:
            candidates = reversed(rows) if self.kind == 'drive' else rows
            return next((row for row in candidates if row.account_email == account_email), None)
        if self.kind == 'calendar':
            active = next((row for row in rows if getattr(row, 'is_active', False)), None)
            if active:
                return active
        return rows[-1] if rows else None

    def capture_profile(self, user_id, *, email, subject, name):
        def write(db, active):
            owner = self._lock(db, user_id, active)
            values = {}
            if (owner.email or '').lower() == email.lower() and not owner.google_id:
                values['google_id'] = subject
            if not owner.name:
                values['name'] = name
            if values:
                db.users.update_one({'id': int(user_id)}, update_fields(values), session=active)
        self.store.transaction(write)

    def connect(self, user_id, *, token, account_email, account_name):
        if not isinstance(account_email, str) or not account_email:
            raise ValueError('Google account email is required')
        def write(db, active):
            self._lock(db, user_id, active)
            collection = db[self.collection_name]
            raw = collection.find_one({'user_id': int(user_id), 'account_email': account_email},
                sort=[('id', 1)], session=active)
            previous = OutlookGrant(raw) if raw else None
            values = {'token_data': secret_box.encrypt(merged_token(previous.token_data if previous else None, token)),
                'account_email': account_email, 'account_name': account_name or (previous.account_name if previous else None),
                'connected_at': utcnow().isoformat()}
            if self.kind == 'calendar':
                collection.update_many({'user_id': int(user_id)}, update_fields({'is_active': False}), session=active)
                values['is_active'] = True
            if raw is None:
                values.update(id=self.store.next_id(self.collection_name, session=active), user_id=int(user_id))
                collection.insert_one(pack_document(values), session=active)
                return OutlookGrant(values)
            collection.update_one({'_id': raw['_id']}, update_fields(values), session=active)
            return OutlookGrant(collection.find_one({'_id': raw['_id']}, session=active))
        return self.store.transaction(write)

    def refresh(self, user_id, expected, token_data):
        if not isinstance(token_data, str):
            raise ValueError('Invalid Google token data')
        def write(db, active):
            self._lock(db, user_id, active)
            collection = db[self.collection_name]
            raw = collection.find_one({'id': expected.id, 'user_id': int(user_id)}, session=active)
            if raw is None or not same_grant(OutlookGrant(raw), expected):
                return False
            collection.update_one({'_id': raw['_id']}, update_fields({'token_data': secret_box.encrypt(token_data)}), session=active)
            return True
        return self.store.transaction(write)

    def activate(self, user_id, identity):
        if self.kind != 'calendar':
            raise ValueError('Only calendar accounts can be activated')
        def write(db, active):
            self._lock(db, user_id, active)
            collection = db[self.collection_name]
            raw = collection.find_one({'id': int(identity), 'user_id': int(user_id)}, session=active)
            if raw is None:
                return None
            collection.update_many({'user_id': int(user_id)}, update_fields({'is_active': False}), session=active)
            collection.update_one({'_id': raw['_id']}, update_fields({'is_active': True}), session=active)
            return OutlookGrant(collection.find_one({'_id': raw['_id']}, session=active))
        return self.store.transaction(write)

    def remove(self, user_id, identity, *, expected=None):
        def write(db, active):
            self._lock(db, user_id, active)
            collection = db[self.collection_name]
            raw = collection.find_one({'id': int(identity), 'user_id': int(user_id)}, session=active)
            if raw is None or (expected is not None and not same_grant(OutlookGrant(raw), expected)):
                return False, False, None
            was_active = self.kind == 'calendar' and bool(raw.get('is_active'))
            collection.delete_one({'_id': raw['_id']}, session=active)
            next_row = None
            if was_active:
                collection.update_many({'user_id': int(user_id)}, update_fields({'is_active': False}), session=active)
                next_row = collection.find_one({'user_id': int(user_id)}, sort=[('id', -1)], session=active)
                if next_row:
                    collection.update_one({'_id': next_row['_id']}, update_fields({'is_active': True}), session=active)
                    next_row = collection.find_one({'_id': next_row['_id']}, session=active)
            return True, was_active, OutlookGrant(next_row) if next_row else None
        return self.store.transaction(write)

    def disconnect(self, user_id):
        def write(db, active):
            self._lock(db, user_id, active)
            db[self.collection_name].delete_many({'user_id': int(user_id)}, session=active)
        self.store.transaction(write)
