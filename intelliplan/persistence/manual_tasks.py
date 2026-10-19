"""Owner-scoped manual task persistence for browser and guest requests."""
from datetime import datetime
from types import SimpleNamespace

from flask import current_app

from intelliplan.persistence.codec import unpack_document


DEFAULTS = dict(title='', due_date='', priority='Medium', course='Personal', estimated_time=60,
    notes='', done=False, notion_page_id=None, import_source='', import_batch_id='', external_id='')
FIELDS = frozenset(DEFAULTS)


def checked(values):
    values = dict(values)
    if values.keys() - FIELDS:
        raise ValueError('Unsupported task fields')
    limits = {'title': 512, 'due_date': 32, 'priority': 16, 'course': 256,
        'notion_page_id': 256, 'import_source': 32, 'import_batch_id': 64, 'external_id': 128}
    for key in FIELDS - {'done', 'estimated_time'}:
        if key not in values:
            continue
        value = values[key]
        if value is None and key != 'title':
            continue
        if not isinstance(value, str) or (key in limits and len(value) > limits[key]):
            raise ValueError('Invalid task text field')
    if 'done' in values and type(values['done']) is not bool:
        raise ValueError('Task completion must be a boolean')
    if 'estimated_time' in values:
        values['estimated_time'] = int(values['estimated_time'])
    return values


def owner(user_id=None, guest_id=None):
    if user_id is not None:
        return {'user_id': int(user_id)}
    if not isinstance(guest_id, str) or not guest_id:
        raise ValueError('A task owner is required')
    return {'user_id': None, 'guest_session_id': guest_id}


class Task(SimpleNamespace):
    def __init__(self, raw):
        values = {**DEFAULTS, 'guest_session_id': None, **unpack_document(raw)}
        values.pop('_id', None)
        if isinstance(values.get('created_at'), str):
            values['created_at'] = datetime.fromisoformat(values['created_at'])
        super().__init__(**values)


def tasks(user_id=None, guest_id=None):
    scope = owner(user_id, guest_id)
    store = current_app.extensions.get('mongo_store')
    if store is not None:
        from intelliplan.persistence.mongo_manual_tasks import MongoTasks
        return MongoTasks(store, scope)
    from App import ManualTask, User, db
    return SQLTasks(db, ManualTask, User, scope)


class SQLTasks:
    def __init__(self, db, model, users, scope):
        self.db, self.model, self.users, self.scope = db, model, users, scope

    @staticmethod
    def snapshot(row):
        return Task({column.name: getattr(row, column.name) for column in row.__table__.columns}) if row else None

    def list(self, done=None):
        query = self.model.query.filter_by(**self.scope).populate_existing()
        if done is not None:
            query = query.filter_by(done=done)
        return [self.snapshot(row) for row in query.order_by(self.model.id).all()]

    def get(self, identity):
        return self.snapshot(self.model.query.filter_by(id=int(identity), **self.scope).populate_existing().first())

    def _write(self, work):
        try:
            uid = self.scope.get('user_id')
            if uid is not None and self.users.query.filter_by(id=uid).populate_existing().with_for_update().first() is None:
                raise LookupError('Account no longer exists')
            result = work()
            self.db.session.commit()
            return result
        except Exception:
            self.db.session.rollback()
            raise

    def create(self, values):
        values = checked(values)
        def write():
            row = self.model(**self.scope, **values)
            self.db.session.add(row)
            self.db.session.flush()
            return self.snapshot(row)
        return self._write(write)

    def update(self, identity, values):
        values = checked(values)
        def write():
            row = self.model.query.filter_by(id=int(identity), **self.scope).populate_existing().with_for_update().first()
            if row is None:
                return None, False
            completed = values.get('done') is True and not row.done
            for key, value in values.items():
                setattr(row, key, value)
            return self.snapshot(row), completed
        return self._write(write)

    def delete(self, identity):
        def write():
            row = self.model.query.filter_by(id=int(identity), **self.scope).populate_existing().with_for_update().first()
            if row is None:
                return None if self.db.session.get(self.model, int(identity)) is None else False
            self.db.session.delete(row)
            return True
        return self._write(write)
