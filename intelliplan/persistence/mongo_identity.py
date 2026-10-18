"""Native identity profiles, retaining migrated JSON text and precise dates."""
from datetime import datetime
import json

from pymongo import ReturnDocument

import assistant_name
from intelliplan.persistence.codec import pack_document, unpack_document, update_fields
from intelliplan.persistence.identity import identity_changes
from time_utils import utcnow


DEFAULTS = {'grade_level': None, 'focus_areas': '[]', 'goals': '', 'completed': False,
            'availability': '{}', 'weekly_commitments': '', 'class_schedule': '[]',
            'assistant_name': None}


class Identity:
    def __init__(self, row):
        self.__dict__.update({**DEFAULTS, **unpack_document(row)})
        for field in ('created_at', 'updated_at'):
            if isinstance(getattr(self, field, None), str):
                setattr(self, field, datetime.fromisoformat(getattr(self, field)))

    def _json(self, field, kind):
        try:
            value = json.loads(getattr(self, field) or json.dumps(kind()))
            return value if isinstance(value, kind) else kind()
        except (TypeError, ValueError):
            return kind()

    def focus_list(self):
        return self._json('focus_areas', list)

    def avail_dict(self):
        return self._json('availability', dict)

    def class_list(self):
        return self._json('class_schedule', list)

    def to_dict(self):
        return {'grade_level': self.grade_level or '', 'focus_areas': self.focus_list(),
                'goals': self.goals or '', 'completed': bool(self.completed),
                'availability': self.avail_dict(), 'weekly_commitments': self.weekly_commitments or '',
                'class_schedule': self.class_list(),
                'assistant_name': assistant_name.display(self.assistant_name)}


class MongoIdentities:
    def __init__(self, store):
        self.store = store

    def ensure_indexes(self):
        self.store.database.user_identities.create_index('id', unique=True)
        self.store.database.user_identities.create_index('user_id', unique=True)

    def get(self, user_id):
        row = self.store.database.user_identities.find_one({'user_id': int(user_id)})
        return Identity(row) if row else None

    def get_or_create(self, user_id):
        return self.get(user_id) or self.save(user_id, {})

    def save(self, user_id, values):
        values = identity_changes(values)

        def write(db, active):
            if not db.users.update_one({'id': int(user_id)}, {'$inc': {'_profile_revision': 1}},
                                       session=active).matched_count:
                raise LookupError('Account no longer exists')
            row = db.user_identities.find_one({'user_id': int(user_id)}, session=active)
            now = utcnow().isoformat()
            if row is None:
                row = {**DEFAULTS, **values, 'id': self.store.next_id('user_identities', session=active),
                       'user_id': int(user_id), 'created_at': now, 'updated_at': now}
                db.user_identities.insert_one(pack_document(row), session=active)
            else:
                row = db.user_identities.find_one_and_update({'_id': row['_id']},
                    update_fields({**values, 'updated_at': now}),
                    return_document=ReturnDocument.AFTER, session=active)
            return Identity(row)

        return self.store.transaction(write)
