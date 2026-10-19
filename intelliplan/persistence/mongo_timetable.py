"""Snapshot-consistent reads of restored class meetings and rotation settings."""
from intelliplan.domain.timetable import (
    Meeting, Rotation, busy_by_date, parse_clock, parse_int_list, parse_weekdays,
)
from datetime import date, datetime

from intelliplan.persistence.codec import pack_document, unpack_document, update_fields
from intelliplan.persistence.json_values import loads_exact
from intelliplan.persistence.timetable import (
    CLASS_DEFAULTS, CLASS_FIELDS, SETTINGS_DEFAULTS, SETTINGS_FIELDS,
    TimetableError, TimetableRecord, import_plan,
)
from time_utils import utcnow


def json_value(raw, fallback):
    try:
        value = loads_exact(raw) if raw else fallback
    except (TypeError, ValueError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def meeting(raw):
    row = unpack_document(raw)
    return Meeting(id=row['id'], course=row.get('course') or '', period=row.get('period') or '',
        room=row.get('room') or '', teacher=row.get('teacher') or '',
        start_minute=parse_clock(row.get('start_time')), end_minute=parse_clock(row.get('end_time')),
        weekdays=parse_weekdays(row.get('weekdays')), rotation_days=parse_int_list(row.get('rotation_days')),
        color=row.get('color') or '', source=row.get('source') or 'manual')


def settings(raw):
    row = unpack_document(raw) if raw else {}
    rotation = Rotation.from_dict({'kind': row.get('rotation_kind') or 'none',
        'length': row.get('cycle_length') or 1, 'anchor_date': row.get('anchor_date'),
        'anchor_day': row.get('anchor_day') or 1,
        'school_weekdays': row.get('school_weekdays', 'Mon,Tue,Wed,Thu,Fri') or '',
        'skip_days': json_value(row.get('skip_days_json'), [])})
    bell = {str(key)[:32]: [str(span[0]), str(span[1])]
        for key, span in json_value(row.get('bell_json'), {}).items()
        if isinstance(span, (list, tuple)) and len(span) >= 2}
    return rotation, bell


class MongoTimetable:
    def __init__(self, store):
        self.store = store

    def ensure_indexes(self):
        for name in ('class_meetings', 'timetable_settings'):
            self.store.database[name].create_index('id', unique=True)
        self.store.database.class_meetings.create_index('user_id')
        self.store.database.timetable_settings.create_index('user_id', unique=True)

    def list(self, uid):
        rows = self.store.database.class_meetings.find({'user_id': int(uid)}).sort([
            ('start_time', 1), ('period', 1), ('id', 1)])
        return [TimetableRecord(row) for row in rows]

    def get_settings(self, uid):
        row = self.store.database.timetable_settings.find_one({'user_id': int(uid)})
        return TimetableRecord(row, configuration=True) if row else None

    @staticmethod
    def lock(db, uid, active):
        if not db.users.update_one({'id': int(uid)}, {'$inc': {'_timetable_revision': 1}}, session=active).matched_count:
            raise LookupError('Account no longer exists')

    @staticmethod
    def stored(values):
        return {key: value.isoformat() if isinstance(value, (date, datetime)) else value
            for key, value in values.items()}

    def add(self, uid, fields, limit=30):
        if fields.keys() - CLASS_FIELDS:
            raise ValueError('Unsupported class fields')
        def write(db, active):
            self.lock(db, uid, active)
            if db.class_meetings.count_documents({'user_id': int(uid)}, session=active) >= limit:
                raise TimetableError(f'A timetable holds up to {limit} classes.')
            now = utcnow().isoformat()
            values = {**CLASS_DEFAULTS, **fields, 'id': self.store.next_id('class_meetings', session=active),
                'user_id': int(uid), 'created_at': now, 'updated_at': now}
            db.class_meetings.insert_one(pack_document(values), session=active)
        return self.store.transaction(write)

    def modify(self, uid, identity, fields=None):
        if fields is not None and fields.keys() - CLASS_FIELDS:
            raise ValueError('Unsupported class fields')
        def write(db, active):
            self.lock(db, uid, active)
            query = {'user_id': int(uid), 'id': int(identity)}
            if fields is None:
                return bool(db.class_meetings.delete_one(query, session=active).deleted_count)
            return bool(db.class_meetings.update_one(query,
                update_fields({**fields, 'updated_at': utcnow().isoformat()}), session=active).matched_count)
        return self.store.transaction(write)

    def _settings(self, db, uid, transform, active):
        raw = db.timetable_settings.find_one({'user_id': int(uid)}, session=active)
        values = transform(TimetableRecord(raw, configuration=True))
        if values.keys() - SETTINGS_FIELDS:
            raise ValueError('Unsupported timetable settings')
        values = self.stored({**values, 'updated_at': utcnow()})
        if raw is None:
            values = {**SETTINGS_DEFAULTS, **values, 'id': self.store.next_id('timetable_settings', session=active), 'user_id': int(uid)}
            db.timetable_settings.insert_one(pack_document(values), session=active)
        else:
            db.timetable_settings.update_one({'_id': raw['_id']}, update_fields(values), session=active)

    def change_settings(self, uid, transform):
        def write(db, active):
            self.lock(db, uid, active)
            self._settings(db, uid, transform, active)
        return self.store.transaction(write)

    def import_classes(self, uid, rows, source, transform=None):
        def write(db, active):
            self.lock(db, uid, active)
            existing = [TimetableRecord(row) for row in db.class_meetings.find(
                {'user_id': int(uid)}, session=active).sort('id', 1)]
            changes, additions, removed, count = import_plan(existing, rows, source)
            now = utcnow().isoformat()
            for identity, values in changes:
                db.class_meetings.update_one({'user_id': int(uid), 'id': identity},
                    update_fields({**values, 'updated_at': now}), session=active)
            for values in additions:
                values = {**values, 'id': self.store.next_id('class_meetings', session=active),
                    'user_id': int(uid), 'created_at': now, 'updated_at': now}
                db.class_meetings.insert_one(pack_document(values), session=active)
            if removed:
                db.class_meetings.delete_many({'user_id': int(uid), 'id': {'$in': removed}}, session=active)
            if transform is not None:
                self._settings(db, uid, transform, active)
            return count
        return self.store.transaction(write)

    def busy(self, user_id, start, days=14):
        def read(db, active):
            if db.users.find_one({'id': int(user_id)}, {'_id': 1}, session=active) is None:
                return {}
            flag = db.feature_flags.find_one({'key': 'timetable'}, session=active)
            if flag is not None:
                enabled = unpack_document(flag).get('enabled')
                if type(enabled) is not bool:
                    raise ValueError('Invalid timetable feature flag')
                if not enabled:
                    return {}
            rows = list(db.class_meetings.find({'user_id': int(user_id)}, session=active))
            if not rows:
                return {}
            rotation, bell = settings(db.timetable_settings.find_one({'user_id': int(user_id)}, session=active))
            return busy_by_date(start, days, [meeting(row) for row in rows], rotation, bell)
        return self.store.transaction(read)
