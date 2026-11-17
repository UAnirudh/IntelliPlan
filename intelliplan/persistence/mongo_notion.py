"""Native Notion grants with owner serialization and frozen refresh proof."""
from datetime import datetime

import secret_box

from intelliplan.persistence.codec import pack_document, update_fields
from intelliplan.persistence.notion import NotionGrant, connection_values, same_grant


def stored_values(values):
    return {key: secret_box.encrypt(value) if key in {'token', 'refresh_token'} else
        value.isoformat() if isinstance(value, datetime) else value for key, value in values.items()}


class MongoNotion:
    def __init__(self, store):
        self.store = store

    def ensure_indexes(self):
        self.store.database.notion_integrations.create_index('id', unique=True)
        # Old SQL rows may have duplicate/null owners. Preserve restored rows.
        self.store.database.notion_integrations.create_index('user_id')

    @staticmethod
    def _lock(db, user_id, active):
        result = db.users.update_one({'id': int(user_id)}, {'$inc': {'_notion_revision': 1}}, session=active)
        if not result.matched_count:
            raise LookupError('Account no longer exists')

    def get(self, user_id):
        raw = self.store.database.notion_integrations.find_one({'user_id': int(user_id)}, sort=[('id', 1)])
        return NotionGrant(raw) if raw else None

    def connect(self, user_id, **parameters):
        def write(db, active):
            self._lock(db, user_id, active)
            raw = db.notion_integrations.find_one({'user_id': int(user_id)}, sort=[('id', 1)], session=active)
            values = stored_values(connection_values(NotionGrant(raw) if raw else None, **parameters))
            if raw is None:
                values.update(id=self.store.next_id('notion_integrations', session=active), user_id=int(user_id))
                db.notion_integrations.insert_one(pack_document(values), session=active)
                return NotionGrant(values)
            db.notion_integrations.update_one({'_id': raw['_id']}, update_fields(values), session=active)
            return NotionGrant(db.notion_integrations.find_one({'_id': raw['_id']}, session=active))
        return self.store.transaction(write)

    def _change(self, user_id, values, expected=None):
        def write(db, active):
            self._lock(db, user_id, active)
            query = {'user_id': int(user_id)}
            if expected is not None:
                query['id'] = expected.id
            raw = db.notion_integrations.find_one(query, sort=[('id', 1)], session=active)
            if raw is None or (expected is not None and not same_grant(NotionGrant(raw), expected)):
                return False
            db.notion_integrations.update_one({'_id': raw['_id']}, update_fields(stored_values(values)), session=active)
            return True
        return self.store.transaction(write)

    def refresh(self, user_id, expected, fresh):
        return self._change(user_id, {'token': fresh['access_token'],
            'refresh_token': fresh.get('refresh_token') or expected.refresh_token,
            'token_expires_at': fresh.get('expires_at')}, expected)

    def select_database(self, user_id, database_id, *, expected=None):
        if not isinstance(database_id, str) or not database_id:
            raise ValueError('Notion database is required')
        return self._change(user_id, {'database_id': database_id}, expected)

    def disconnect(self, user_id):
        def write(db, active):
            self._lock(db, user_id, active)
            db.notion_integrations.delete_many({'user_id': int(user_id)}, session=active)
        self.store.transaction(write)
