"""Owner-scoped LMS profiles and encrypted credentials."""
from __future__ import annotations

from flask import current_app


def linked_accounts():
    repository = current_app.extensions.get('linked_accounts_repository')
    if repository is not None:
        return repository
    store = current_app.extensions.get('mongo_store')
    if store is not None:
        from intelliplan.persistence.mongo_linked_accounts import MongoLinkedAccounts
        return MongoLinkedAccounts(store)
    from App import LinkedAccount, User, db
    return SQLLinkedAccounts(db, LinkedAccount, User)


class SQLLinkedAccounts:
    def __init__(self, db, model, user_model):
        self.db, self.model, self.user_model = db, model, user_model

    def list(self, user_id, *, login_type=None, active=None):
        query = self.model.query.filter_by(user_id=int(user_id))
        if login_type is not None:
            query = query.filter_by(login_type=login_type)
        if active is not None:
            query = query.filter_by(is_active=bool(active))
        return query.order_by(self.model.id).all()

    def active(self, user_id):
        return next(iter(self.list(user_id, active=True)), None)

    def integration_presence(self, user_id):
        from App import BlackboardIntegration, ClassroomIntegration, MoodleIntegration
        return {name: model.query.filter_by(user_id=int(user_id)).first() is not None
                for name, model in (('google_classroom', ClassroomIntegration),
                                    ('blackboard', BlackboardIntegration), ('moodle', MoodleIntegration))}

    def by_type(self, user_id, login_type):
        rows = self.list(user_id, login_type=login_type)
        return max(rows, key=lambda row: (bool(row.is_active), row.id), default=None)

    def _lock_owner(self, user_id):
        if self.user_model.query.filter_by(id=int(user_id)).populate_existing().with_for_update().first() is None:
            raise LookupError('Account no longer exists')

    def create_active(self, user_id, name, login_type, credentials):
        try:
            self._lock_owner(user_id)
            self.model.query.filter_by(user_id=int(user_id)).update({'is_active': False})
            row = self.model(user_id=int(user_id), name=name, login_type=login_type, is_active=True)
            row.set_credentials(credentials)
            self.db.session.add(row)
            self.db.session.commit()
            return row
        except Exception:
            self.db.session.rollback()
            raise

    def switch(self, user_id, profile_id):
        if not isinstance(profile_id, str) or not profile_id:
            return False
        try:
            self._lock_owner(user_id)
            row = self.model.query.filter_by(user_id=int(user_id), profile_id=profile_id).populate_existing().first()
            if row is None:
                self.db.session.rollback()
                return False
            self.model.query.filter_by(user_id=int(user_id)).update({'is_active': False})
            # An explicit UPDATE avoids stale identity-map values after a bulk update.
            self.model.query.filter_by(id=row.id, user_id=int(user_id)).update({'is_active': True})
            self.db.session.commit()
            return True
        except Exception:
            self.db.session.rollback()
            raise

    def delete(self, user_id, profile_id):
        return self._change(user_id, profile_id, None)

    def rename(self, user_id, profile_id, name):
        return self._change(user_id, profile_id, {'name': name})

    def _change(self, user_id, profile_id, values):
        if not isinstance(profile_id, str) or not profile_id:
            return False
        try:
            self._lock_owner(user_id)
            query = self.model.query.filter_by(user_id=int(user_id), profile_id=profile_id)
            changed = query.delete() if values is None else query.update(values)
            self.db.session.commit()
            return bool(changed)
        except Exception:
            self.db.session.rollback()
            raise
