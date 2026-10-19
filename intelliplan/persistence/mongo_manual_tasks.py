"""Native task CRUD; all account writes coordinate with account erasure."""
from intelliplan.persistence.codec import pack_document, update_fields
from intelliplan.persistence.manual_tasks import DEFAULTS, Task, checked
from time_utils import utcnow


class MongoTasks:
    def __init__(self, store, scope):
        self.store, self.scope = store, scope

    def ensure_indexes(self):
        self.store.database.manual_tasks.create_index('id', unique=True)
        self.store.database.manual_tasks.create_index([('user_id', 1), ('done', 1)])
        self.store.database.manual_tasks.create_index([('guest_session_id', 1), ('done', 1)])

    def list(self, done=None):
        query = {**self.scope, **({'done': done} if done is not None else {})}
        return [Task(raw) for raw in self.store.database.manual_tasks.find(query).sort('id', 1)]

    def get(self, identity):
        raw = self.store.database.manual_tasks.find_one({'id': int(identity), **self.scope})
        return Task(raw) if raw else None

    def _write(self, work):
        def write(db, active):
            uid = self.scope.get('user_id')
            if uid is not None and not db.users.update_one({'id': uid},
                    {'$inc': {'_manual_task_revision': 1}}, session=active).matched_count:
                raise LookupError('Account no longer exists')
            return work(db, active)
        return self.store.transaction(write)

    def create(self, values):
        values = checked(values)
        def write(db, active):
            row = {**DEFAULTS, **values, **self.scope, 'created_at': utcnow().isoformat(),
                'id': self.store.next_id('manual_tasks', session=active)}
            db.manual_tasks.insert_one(pack_document(row), session=active)
            return Task(row)
        return self._write(write)

    def update(self, identity, values):
        values = checked(values)
        def write(db, active):
            raw = db.manual_tasks.find_one({'id': int(identity), **self.scope}, session=active)
            if raw is None:
                return None, False
            previous = Task(raw)
            completed = values.get('done') is True and not previous.done
            if values:
                db.manual_tasks.update_one({'_id': raw['_id']}, update_fields(values), session=active)
            return Task(db.manual_tasks.find_one({'_id': raw['_id']}, session=active)), completed
        return self._write(write)

    def delete(self, identity):
        def write(db, active):
            query = {'id': int(identity), **self.scope}
            if db.manual_tasks.delete_one(query, session=active).deleted_count:
                return True
            return None if db.manual_tasks.find_one({'id': int(identity)}, {'_id': 1}, session=active) is None else False
        return self._write(write)
