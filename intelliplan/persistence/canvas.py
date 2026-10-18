"""Transactional Canvas OAuth storage and mirrored LMS credentials."""
from flask import current_app

from time_utils import utcnow

CANVAS_SECRETS = ('canvas_token', 'canvas_refresh_token', 'canvas_token_expires_at', 'canvas_oauth')


def canvas_integrations():
    repository = current_app.extensions.get('canvas_repository')
    if repository is not None:
        return repository
    store = current_app.extensions.get('mongo_store')
    if store is not None:
        from intelliplan.persistence.mongo_canvas import MongoCanvas
        return MongoCanvas(store)
    from App import CanvasIntegration, LinkedAccount, User, db
    return SQLCanvas(db, CanvasIntegration, LinkedAccount, User)


def same_connection(current, expected):
    if current is None or expected is None:
        return current is None and expected is None
    return all(getattr(current, name) == getattr(expected, name)
               for name in ('id', 'canvas_base', 'access_token', 'refresh_token', 'connected_at'))


class SQLCanvas:
    def __init__(self, db, model, linked_model, user_model):
        self.db, self.model, self.linked_model = db, model, linked_model
        self.user_model = user_model

    def get(self, user_id):
        # Return detached proof values, never the identity-map object that a
        # later populate_existing would silently mutate during CAS validation.
        from types import SimpleNamespace
        row = self.model.query.filter_by(user_id=int(user_id)).populate_existing().order_by(self.model.id).first()
        if row is None:
            return None
        return SimpleNamespace(**{column.name: getattr(row, column.name) for column in self.model.__table__.columns})

    def _lock_owner(self, user_id):
        if self.user_model.query.filter_by(id=int(user_id)).populate_existing().with_for_update().first() is None:
            raise LookupError('Account no longer exists')

    def connect(self, user_id, *, canvas_base, access_token, refresh_token,
                expires_at, canvas_user_id, canvas_user_name, profile_name, credentials):
        try:
            self._lock_owner(user_id)
            row = self.model.query.filter_by(user_id=int(user_id)).populate_existing().order_by(self.model.id).first()
            if row is None:
                row = self.model(user_id=int(user_id))
                self.db.session.add(row)
            row.canvas_base = canvas_base
            row.access_token = access_token
            row.refresh_token = refresh_token or row.refresh_token
            row.token_expires_at = expires_at
            row.canvas_user_id = canvas_user_id or row.canvas_user_id
            row.canvas_user_name = canvas_user_name or row.canvas_user_name
            row.connected_at = utcnow()
            self.linked_model.query.filter_by(user_id=int(user_id)).update({'is_active': False})
            linked = self.linked_model(user_id=int(user_id), name=canvas_user_name or profile_name,
                                       login_type='canvas', is_active=True)
            linked.set_credentials(credentials)
            self.db.session.add(linked)
            self.db.session.commit()
        except Exception:
            self.db.session.rollback()
            raise

    def refresh(self, user_id, expected, *, access_token, refresh_token, expires_at):
        try:
            self._lock_owner(user_id)
            row = self.model.query.filter_by(user_id=int(user_id)).populate_existing().order_by(self.model.id).first()
            if not same_connection(row, expected):
                self.db.session.rollback()
                return False
            row.access_token = access_token
            if refresh_token:
                row.refresh_token = refresh_token
            row.token_expires_at = expires_at
            for linked in self.linked_model.query.filter_by(
                    user_id=int(user_id), is_active=True, login_type='canvas').populate_existing().all():
                values = linked.get_credentials()
                # A pasted token or a different Canvas school is not this OAuth connection.
                if not values.get('canvas_oauth') or values.get('canvas_url') != row.canvas_base:
                    continue
                values.update(canvas_token=access_token,
                              canvas_token_expires_at=expires_at.isoformat() if expires_at else None)
                if refresh_token:
                    values['canvas_refresh_token'] = refresh_token
                linked.set_credentials(values)
            self.db.session.commit()
            return True
        except Exception:
            self.db.session.rollback()
            raise

    def disconnect(self, user_id, expected):
        try:
            self._lock_owner(user_id)
            row = self.model.query.filter_by(user_id=int(user_id)).populate_existing().order_by(self.model.id).first()
            if not same_connection(row, expected):
                self.db.session.rollback()
                return False
            if row is not None:
                self.db.session.delete(row)
            for linked in self.linked_model.query.filter_by(user_id=int(user_id), login_type='canvas').populate_existing().all():
                values = linked.get_credentials()
                for key in CANVAS_SECRETS:
                    values.pop(key, None)
                linked.set_credentials(values)
                linked.is_active = False
            self.db.session.commit()
            return True
        except Exception:
            self.db.session.rollback()
            raise
