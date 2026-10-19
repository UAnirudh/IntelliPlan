"""Native saved-plan timeline boundary with exact source-document proof."""
from intelliplan.persistence.codec import unpack_document, update_fields
from intelliplan.persistence.schedule_plans import Plan


class MongoPlans:
    def __init__(self, store):
        self.store = store

    def ensure_indexes(self):
        self.store.database.saved_schedules.create_index('id', unique=True)
        self.store.database.saved_schedules.create_index([('user_id', 1), ('is_active', 1), ('created_at', -1), ('id', -1)])

    def active(self, uid):
        raw = self.store.database.saved_schedules.find_one({'user_id': int(uid), 'is_active': True},
            sort=[('created_at', -1), ('id', -1)])
        return Plan(raw) if raw else None

    def replace(self, uid, expected, serialized):
        if not isinstance(serialized, str):
            raise ValueError('A serialized plan is required')
        if expected is None or expected.user_id != int(uid):
            return False
        def write(db, active):
            if not db.users.update_one({'id': int(uid)}, {'$inc': {'_schedule_revision': 1}}, session=active).matched_count:
                return False
            raw = db.saved_schedules.find_one({'id': expected.id, 'user_id': int(uid), 'is_active': True}, session=active)
            if raw is None:
                return False
            decoded = unpack_document(raw)
            if any(decoded.get(key) != getattr(expected, key) for key in ('schedule_data', 'progress_json')):
                return False
            db.saved_schedules.update_one({'_id': raw['_id']}, update_fields({'schedule_data': serialized}), session=active)
            return True
        return self.store.transaction(write)
