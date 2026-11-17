"""Extension credentials, preserving existing opaque tokens and owner IDs."""
import secrets

from flask import current_app
from sqlalchemy import func, select

from intelliplan.persistence.accounts import PasswordConflict
from time_utils import utcnow


def extension_tokens():
    repository = current_app.extensions.get('extension_tokens_repository')
    if repository is not None:
        return repository
    from App import ExtensionToken, User, db
    return SQLExtensionTokens(db, ExtensionToken, User)


class SQLExtensionTokens:
    def __init__(self, db, model, user_model):
        self.db, self.model, self.user_model = db, model, user_model

    def issue(self, user_id, *, reuse=False, expected_hash=None, expected_epoch=None):
        try:
            query = select(self.user_model.id).where(self.user_model.id == int(user_id))
            if expected_hash is not None:
                query = query.where(self.user_model.password_hash == expected_hash)
            if expected_epoch is not None:
                query = query.where(func.coalesce(self.user_model.session_epoch, 0) == int(expected_epoch))
            if self.db.session.execute(query.with_for_update()).scalar_one_or_none() is None:
                raise PasswordConflict('Account credentials changed. Sign in again.')
            row = self.model.query.filter_by(user_id=int(user_id)).order_by(self.model.id).first() if reuse else None
            if row is None:
                row = self.model(user_id=int(user_id), token=secrets.token_hex(32), created_at=utcnow())
                self.db.session.add(row)
            token = row.token
            self.db.session.commit()
            return token
        except Exception:
            self.db.session.rollback()
            raise

    def revoke(self, token):
        self.model.query.filter_by(token=token).delete(synchronize_session=False)
        self.db.session.commit()

    def user_for(self, token):
        if not token:
            return None
        row = self.model.query.filter_by(token=token).first()
        return self.db.session.get(self.user_model, row.user_id) if row else None
