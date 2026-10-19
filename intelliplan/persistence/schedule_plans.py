"""Saved-plan reads and conditional timeline writes across SQL and MongoDB."""
from datetime import datetime
from types import SimpleNamespace

from flask import current_app

from intelliplan.persistence.codec import unpack_document


class Plan(SimpleNamespace):
    def __init__(self, raw):
        values = {'schedule_data': None, 'progress_json': None, **unpack_document(raw)}
        values.pop('_id', None)
        if isinstance(values.get('created_at'), str):
            values['created_at'] = datetime.fromisoformat(values['created_at'])
        super().__init__(**values)


def plans():
    injected = current_app.extensions.get('schedule_plan_repository')
    if injected is not None:
        return injected
    store = current_app.extensions.get('mongo_store')
    if store is not None:
        from intelliplan.persistence.mongo_schedule_plans import MongoPlans
        return MongoPlans(store)
    from App import SavedSchedule, User, db
    return SQLPlans(db, SavedSchedule, User)


class SQLPlans:
    def __init__(self, db, model, users):
        self.db, self.model, self.users = db, model, users

    def active(self, uid):
        row = self.model.query.filter_by(user_id=int(uid), is_active=True).populate_existing().order_by(
            self.model.created_at.desc(), self.model.id.desc()).first()
        return Plan({column.name: getattr(row, column.name) for column in row.__table__.columns}) if row else None

    def replace(self, uid, expected, serialized):
        if not isinstance(serialized, str):
            raise ValueError('A serialized plan is required')
        if expected is None or expected.user_id != int(uid):
            return False
        try:
            if self.users.query.filter_by(id=int(uid)).populate_existing().with_for_update().first() is None:
                self.db.session.rollback()
                return False
            changed = self.model.query.filter_by(user_id=int(uid), id=expected.id, is_active=True,
                schedule_data=expected.schedule_data, progress_json=expected.progress_json).update(
                    {'schedule_data': serialized}, synchronize_session=False)
            self.db.session.commit()
            return bool(changed)
        except Exception:
            self.db.session.rollback()
            raise
