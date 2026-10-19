"""Explicit multi-account Google calendar and Drive grant repositories."""
from flask import current_app

from google_calendar_helper import merge_token_data
from intelliplan.persistence.json_values import dumps_exact, loads_exact
from intelliplan.persistence.outlook import SQLOutlook, same_grant
from time_utils import utcnow


def google_accounts(kind='calendar'):
    if kind not in {'calendar', 'drive'}:
        raise ValueError('Unsupported Google integration')
    repository = current_app.extensions.get(f'google_{kind}_repository')
    if repository is not None:
        return repository
    store = current_app.extensions.get('mongo_store')
    if store is not None:
        from intelliplan.persistence.mongo_google import MongoGoogle
        return MongoGoogle(store, kind)
    from App import GoogleDriveIntegration, GoogleIntegration, User, db
    return SQLGoogle(db, GoogleIntegration if kind == 'calendar' else GoogleDriveIntegration, User, kind)


def merged_token(existing, fresh):
    previous = loads_exact(existing) if existing else {}
    if not isinstance(previous, dict) or not isinstance(fresh, dict):
        raise ValueError('Invalid Google token data')
    return dumps_exact(merge_token_data(previous, fresh))


class SQLGoogle(SQLOutlook):
    def __init__(self, db, model, users, kind):
        super().__init__(db, model, users)
        self.kind = kind

    def list(self, user_id):
        rows = self.model.query.filter_by(user_id=int(user_id)).populate_existing().order_by(self.model.id).all()
        return [self._snapshot(row) for row in rows]

    def get(self, user_id, account_email=None):
        rows = self.list(user_id)
        if account_email is not None:
            candidates = reversed(rows) if self.kind == 'drive' else rows
            return next((row for row in candidates if row.account_email == account_email), None)
        if self.kind == 'calendar':
            active = next((row for row in rows if row.is_active), None)
            if active:
                return active
        return rows[-1] if rows else None

    def capture_profile(self, user_id, *, email, subject, name):
        try:
            self._lock(user_id)
            user = self.users.query.filter_by(id=int(user_id)).populate_existing().first()
            if (user.email or '').lower() == email.lower() and not user.google_id:
                user.google_id = subject
            if not user.name:
                user.name = name
            self.db.session.commit()
        except Exception:
            self.db.session.rollback()
            raise

    def connect(self, user_id, *, token, account_email, account_name):
        if not isinstance(account_email, str) or not account_email:
            raise ValueError('Google account email is required')
        try:
            self._lock(user_id)
            row = self.model.query.filter_by(user_id=int(user_id), account_email=account_email).populate_existing().order_by(self.model.id).first()
            serialized = merged_token(row.token_data if row else None, token)
            if self.kind == 'calendar':
                self.model.query.filter_by(user_id=int(user_id)).update({'is_active': False})
            if row is None:
                row = self.model(user_id=int(user_id), account_email=account_email)
                self.db.session.add(row)
            row.token_data = serialized
            row.account_name = account_name or row.account_name
            row.connected_at = utcnow()
            if self.kind == 'calendar':
                row.is_active = True
            self.db.session.commit()
            return self._snapshot(row)
        except Exception:
            self.db.session.rollback()
            raise

    def refresh(self, user_id, expected, token_data):
        try:
            self._lock(user_id)
            row = self.model.query.filter_by(user_id=int(user_id), id=expected.id).populate_existing().first()
            if not same_grant(row, expected):
                self.db.session.rollback()
                return False
            row.token_data = token_data
            self.db.session.commit()
            return True
        except Exception:
            self.db.session.rollback()
            raise

    def activate(self, user_id, identity):
        if self.kind != 'calendar':
            raise ValueError('Only calendar accounts can be activated')
        try:
            self._lock(user_id)
            row = self.model.query.filter_by(user_id=int(user_id), id=int(identity)).populate_existing().first()
            if row is None:
                self.db.session.rollback()
                return None
            self.model.query.filter_by(user_id=int(user_id)).update({'is_active': False})
            row.is_active = True
            self.db.session.commit()
            return self._snapshot(row)
        except Exception:
            self.db.session.rollback()
            raise

    def remove(self, user_id, identity, *, expected=None):
        try:
            self._lock(user_id)
            row = self.model.query.filter_by(user_id=int(user_id), id=int(identity)).populate_existing().first()
            if row is None or (expected is not None and not same_grant(row, expected)):
                self.db.session.rollback()
                return False, False, None
            was_active = self.kind == 'calendar' and bool(row.is_active)
            self.db.session.delete(row)
            self.db.session.flush()
            next_row = None
            if was_active:
                self.model.query.filter_by(user_id=int(user_id)).update({'is_active': False})
                next_row = self.model.query.filter_by(user_id=int(user_id)).order_by(self.model.id.desc()).first()
                if next_row:
                    next_row.is_active = True
            self.db.session.commit()
            return True, was_active, self._snapshot(next_row)
        except Exception:
            self.db.session.rollback()
            raise
