"""Explicit Outlook calendar credential repository selection."""
from types import SimpleNamespace

from flask import current_app
from time_utils import utcnow


def outlook_accounts():
    repository = current_app.extensions.get('outlook_repository')
    if repository is not None:
        return repository
    store = current_app.extensions.get('mongo_store')
    if store is not None:
        from intelliplan.persistence.mongo_outlook import MongoOutlook
        return MongoOutlook(store)
    from App import OutlookIntegration, User, db
    return SQLOutlook(db, OutlookIntegration, User)



def onedrive_accounts():
    repository = current_app.extensions.get('onedrive_repository')
    if repository is not None:
        return repository
    store = current_app.extensions.get('mongo_store')
    if store is not None:
        from intelliplan.persistence.mongo_outlook import MongoOneDrive
        return MongoOneDrive(store)
    from App import OneDriveIntegration, User, db
    return SQLOutlook(db, OneDriveIntegration, User)


def same_grant(current, expected):
    return current is not None and expected is not None and all(
        getattr(current, field, None) == getattr(expected, field, None)
        for field in ('id', 'user_id', 'token_data', 'connected_at', 'account_email'))


class SQLOutlook:
    def __init__(self, db, model, users):
        self.db, self.model, self.users = db, model, users

    def get(self, user_id):
        row = self.model.query.filter_by(user_id=int(user_id)).populate_existing().first()
        return self._snapshot(row)

    def _snapshot(self, row):
        return SimpleNamespace(**{column.name: getattr(row, column.name)
            for column in self.model.__table__.columns}) if row is not None else None

    def _lock(self, user_id):
        if self.users.query.filter_by(id=int(user_id)).populate_existing().with_for_update().first() is None:
            raise LookupError('Account no longer exists')

    def connect(self, user_id, *, token_data, account_email, account_name):
        try:
            self._lock(user_id)
            row = self.model.query.filter_by(user_id=int(user_id)).populate_existing().first()
            if row is None:
                row = self.model(user_id=int(user_id))
                self.db.session.add(row)
            row.token_data, row.account_email, row.account_name = token_data, account_email, account_name
            row.connected_at = utcnow()
            self.db.session.commit()
            return self._snapshot(row)
        except Exception:
            self.db.session.rollback()
            raise

    def refresh(self, user_id, expected, token_data):
        try:
            self._lock(user_id)
            row = self.model.query.filter_by(user_id=int(user_id)).populate_existing().first()
            if not same_grant(row, expected):
                self.db.session.rollback()
                return False
            row.token_data = token_data
            self.db.session.commit()
            return True
        except Exception:
            self.db.session.rollback()
            raise

    def disconnect(self, user_id):
        try:
            self._lock(user_id)
            self.model.query.filter_by(user_id=int(user_id)).delete(synchronize_session=False)
            self.db.session.commit()
        except Exception:
            self.db.session.rollback()
            raise
