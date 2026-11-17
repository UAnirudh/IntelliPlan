"""Timetable busy-time reads, without an SQL dependency on the native path."""
from flask import current_app
from datetime import date, datetime
from types import SimpleNamespace

from intelliplan.persistence.records import unpack_record
from time_utils import utcnow


CLASS_DEFAULTS = dict(course='', period='', room='', teacher='', start_time='', end_time='',
    weekdays='', rotation_days='', color='', source='manual', external_id='')
SETTINGS_DEFAULTS = dict(rotation_kind='none', cycle_length=1, anchor_date=None, anchor_day=1,
    school_weekdays='Mon,Tue,Wed,Thu,Fri', skip_days_json='[]', bell_json='{}',
    last_import_source='', last_import_at=None)
CLASS_FIELDS = frozenset(CLASS_DEFAULTS)
SETTINGS_FIELDS = frozenset(SETTINGS_DEFAULTS)


class TimetableError(ValueError):
    pass


class TimetableRecord(SimpleNamespace):
    def __init__(self, raw=None, *, configuration=False):
        values = {**(SETTINGS_DEFAULTS if configuration else CLASS_DEFAULTS), **unpack_record(raw or {})}
        values.pop('_id', None)
        for field in ('created_at', 'updated_at', 'last_import_at'):
            if isinstance(values.get(field), str):
                values[field] = datetime.fromisoformat(values[field])
        if isinstance(values.get('anchor_date'), str):
            values['anchor_date'] = date.fromisoformat(values['anchor_date'])
        super().__init__(**values)

    def to_meeting(self):
        from intelliplan.persistence.timetable_values import meeting
        return meeting(vars(self))

    def rotation(self):
        from intelliplan.persistence.timetable_values import settings
        return settings(vars(self))[0]

    def bell(self):
        from intelliplan.persistence.timetable_values import settings
        return settings(vars(self))[1]


def timetable_repository():
    injected = current_app.extensions.get('timetable_repository')
    if injected is not None:
        return injected
    store = current_app.extensions.get('mongo_store')
    if store is not None:
        from intelliplan.persistence.mongo_timetable import MongoTimetable
        return MongoTimetable(store)
    from App import ClassMeeting, TimetableSettings, User, db
    return SQLTimetable(db, ClassMeeting, TimetableSettings, User)


def import_plan(existing, rows, source, limit=30):
    """Pure reconciliation; preserve manual edits and other import sources."""
    def key(course, period):
        return str(course or '').strip().lower(), str(period or '').strip().lower()
    by_external = {(r.source, r.external_id): r for r in existing if r.external_id}
    by_key = {key(r.course, r.period): r for r in existing}
    kept, changes, added = set(), [], []
    for item in rows:
        fields = dict(item)
        external_id = str(fields.pop('external_id', '') or '')[:64]
        if fields.keys() - CLASS_FIELDS:
            raise ValueError('Unsupported class fields')
        row = (by_external.get((source, external_id)) if external_id else None) or by_key.get(key(fields.get('course'), fields.get('period')))
        if row is not None and row.source not in (source, 'manual'):
            row = None
        if row is None:
            values = {**CLASS_DEFAULTS, **fields, 'source': source, 'external_id': external_id}
            row = TimetableRecord(values)
            added.append(row)
            by_key[key(row.course, row.period)] = row
            if external_id:
                by_external[source, external_id] = row
        else:
            values = {name: value for name, value in fields.items()
                if not (row.source == 'manual' and source != 'manual' and getattr(row, name))}
            if row.source == source and external_id:
                values['external_id'] = external_id
            for name, value in values.items():
                setattr(row, name, value)
            if getattr(row, 'id', None) is not None:
                changes.append((row.id, values))
        kept.add(id(row))
    removed = [r.id for r in existing if source in ('studentvue', 'schoology')
        and r.source == source and id(r) not in kept]
    if len(existing) - len(removed) + len(added) > limit:
        raise TimetableError(f'A timetable holds up to {limit} classes.')
    return changes, [vars(row) for row in added], removed, len(kept)


class SQLTimetable:
    def __init__(self, db, classes, settings, users):
        self.db, self.classes, self.settings, self.users = db, classes, settings, users

    @staticmethod
    def record(row, configuration=False):
        return TimetableRecord({column.name: getattr(row, column.name) for column in row.__table__.columns},
            configuration=configuration) if row is not None else None

    def list(self, uid):
        return [self.record(row) for row in self.classes.query.filter_by(user_id=int(uid)).populate_existing()
            .order_by(self.classes.start_time, self.classes.period, self.classes.id).all()]

    def get_settings(self, uid):
        return self.record(self.settings.query.filter_by(user_id=int(uid)).populate_existing().first(), True)

    def _transaction(self, uid, work):
        try:
            if self.users.query.filter_by(id=int(uid)).populate_existing().with_for_update().first() is None:
                raise LookupError('Account no longer exists')
            result = work()
            self.db.session.commit()
            return result
        except Exception:
            self.db.session.rollback()
            raise

    def add(self, uid, fields, limit=30):
        if fields.keys() - CLASS_FIELDS:
            raise ValueError('Unsupported class fields')
        def write():
            if self.classes.query.filter_by(user_id=int(uid)).count() >= limit:
                raise TimetableError(f'A timetable holds up to {limit} classes.')
            row = self.classes(user_id=int(uid), **fields)
            self.db.session.add(row)
        return self._transaction(uid, write)

    def modify(self, uid, identity, fields=None):
        if fields is not None and fields.keys() - CLASS_FIELDS:
            raise ValueError('Unsupported class fields')
        def write():
            row = self.classes.query.filter_by(user_id=int(uid), id=int(identity)).populate_existing().first()
            if row is None:
                return False
            if fields is None:
                self.db.session.delete(row)
            else:
                for key, value in fields.items():
                    setattr(row, key, value)
                row.updated_at = utcnow()
            return True
        return self._transaction(uid, write)

    def change_settings(self, uid, transform):
        def write():
            row = self.settings.query.filter_by(user_id=int(uid)).populate_existing().first()
            snapshot = self.record(row, True) or TimetableRecord(configuration=True)
            values = transform(snapshot)
            if values.keys() - SETTINGS_FIELDS:
                raise ValueError('Unsupported timetable settings')
            if row is None:
                row = self.settings(user_id=int(uid))
                self.db.session.add(row)
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = utcnow()
        return self._transaction(uid, write)

    def import_classes(self, uid, rows, source, transform=None):
        def write():
            existing = self.classes.query.filter_by(user_id=int(uid)).populate_existing().order_by(self.classes.id).all()
            changes, additions, removed, count = import_plan([self.record(row) for row in existing], rows, source)
            by_id = {row.id: row for row in existing}
            for identity, values in changes:
                for key, value in values.items():
                    setattr(by_id[identity], key, value)
            for values in additions:
                self.db.session.add(self.classes(user_id=int(uid), **values))
            for identity in removed:
                self.db.session.delete(by_id[identity])
            if transform is not None:
                row = self.settings.query.filter_by(user_id=int(uid)).populate_existing().first()
                values = transform(self.record(row, True) or TimetableRecord(configuration=True))
                if values.keys() - SETTINGS_FIELDS:
                    raise ValueError('Unsupported timetable settings')
                if row is None:
                    row = self.settings(user_id=int(uid))
                    self.db.session.add(row)
                for key, value in values.items():
                    setattr(row, key, value)
                row.updated_at = utcnow()
            return count
        return self._transaction(uid, write)


def class_busy(user_id, start, days):
    store = current_app.extensions.get('mongo_store')
    if store is not None:
        from intelliplan.persistence.mongo_timetable import MongoTimetable
        return MongoTimetable(store).busy(user_id, start, days)
    from App import ClassMeeting, TimetableSettings
    from intelliplan.domain.timetable import Rotation, busy_by_date
    rows = ClassMeeting.query.filter_by(user_id=int(user_id)).all()
    if not rows:
        return {}
    settings = TimetableSettings.query.filter_by(user_id=int(user_id)).first()
    return busy_by_date(start, days, [row.to_meeting() for row in rows],
        settings.rotation() if settings else Rotation(), settings.bell() if settings else {})
