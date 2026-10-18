"""Student identity storage with explicit, partial updates."""
from flask import current_app
from sqlalchemy import select

from time_utils import utcnow


IDENTITY_FIELDS = frozenset({
    'grade_level', 'focus_areas', 'goals', 'completed', 'availability',
    'weekly_commitments', 'class_schedule', 'assistant_name',
})


def identity_changes(values):
    changes = dict(values)
    if changes.keys() - IDENTITY_FIELDS:
        raise ValueError('Unsupported identity fields')
    return changes


def identities():
    repository = current_app.extensions.get('identity_repository')
    if repository is not None:
        return repository
    store = current_app.extensions.get('mongo_store')
    if store is not None:
        from intelliplan.persistence.mongo_identity import MongoIdentities
        return MongoIdentities(store)
    from App import User, UserIdentity, db
    return SQLIdentities(db, UserIdentity, User)


class SQLIdentities:
    def __init__(self, db, model, user_model):
        self.db, self.model, self.user_model = db, model, user_model

    def _locked_owner(self, user_id):
        owner = self.db.session.execute(select(self.user_model.id).where(
            self.user_model.id == int(user_id)).with_for_update()).scalar_one_or_none()
        if owner is None:
            raise LookupError('Account no longer exists')

    def get(self, user_id):
        try:
            return self.model.query.populate_existing().filter_by(user_id=int(user_id)).first()
        except Exception:
            self.db.session.rollback()
            raise

    def get_or_create(self, user_id):
        row = self.get(user_id)
        if row is not None:
            return row
        try:
            self._locked_owner(user_id)
            row = self.model.query.populate_existing().filter_by(user_id=int(user_id)).first()
            if row is None:
                row = self.model(user_id=int(user_id))
                self.db.session.add(row)
            self.db.session.commit()
            return row
        except Exception:
            self.db.session.rollback()
            raise

    def save(self, user_id, values):
        values = identity_changes(values)
        try:
            self._locked_owner(user_id)
            row = self.model.query.populate_existing().filter_by(user_id=int(user_id)).first()
            if row is None:
                row = self.model(user_id=int(user_id))
                self.db.session.add(row)
            for field, value in values.items():
                setattr(row, field, value)
            row.updated_at = utcnow()
            self.db.session.commit()
            return row
        except Exception:
            self.db.session.rollback()
            raise
