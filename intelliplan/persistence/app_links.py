"""Single-use mobile-to-browser tickets with credential-epoch binding."""
from dataclasses import dataclass
from datetime import timedelta

from flask import current_app
from sqlalchemy import func, inspect, select, text, update

import app_link
from time_utils import utcnow


@dataclass(frozen=True)
class AppLinkRedemption:
    user: object
    session_epoch: int
    next_path: str


def allowed_path(value):
    return value in {app_link.resolve_next(target) for target in (*app_link.LINKABLE, *app_link.PAGES, 'integrations')}


def ensure_sql_link_epoch(db):
    inspector = inspect(db.engine)
    if 'app_link_codes' not in inspector.get_table_names():
        return
    if 'session_epoch' not in {column['name'] for column in inspector.get_columns('app_link_codes')}:
        db.session.execute(text('ALTER TABLE app_link_codes ADD COLUMN session_epoch INTEGER DEFAULT 0'))
        db.session.commit()


def app_link_tickets():
    repository = current_app.extensions.get('app_link_tickets_repository')
    if repository is not None:
        return repository
    store = current_app.extensions.get('mongo_store')
    if store is not None:
        from intelliplan.persistence.mongo_app_links import MongoAppLinks
        return MongoAppLinks(store)
    return SQLAppLinks(current_app.intelliplan_db, current_app.intelliplan_app_link_code_model,
                       current_app.intelliplan_user_model)


class SQLAppLinks:
    def __init__(self, db, model, user_model):
        self.db, self.model, self.user_model = db, model, user_model

    def issue(self, user_id, *, code_hash, next_path, expected_epoch=None):
        if not allowed_path(next_path):
            raise ValueError('Unsupported browser destination')
        try:
            epoch = self.db.session.execute(select(func.coalesce(self.user_model.session_epoch, 0)).where(
                self.user_model.id == int(user_id)).with_for_update()).scalar_one_or_none()
            if epoch is None:
                raise LookupError('Account no longer exists')
            if expected_epoch is not None and int(expected_epoch) != int(epoch):
                raise ValueError('Account credentials changed. Sign in again.')
            now = utcnow()
            self.db.session.execute(update(self.model).where(self.model.user_id == int(user_id),
                self.model.used_at.is_(None)).values(used_at=now), execution_options={'synchronize_session': False})
            self.db.session.add(self.model(user_id=int(user_id), code_hash=code_hash,
                next_path=next_path, session_epoch=int(epoch), created_at=now))
            self.db.session.commit()
        except Exception:
            self.db.session.rollback()
            raise

    def redeem(self, code):
        if not app_link.is_valid_code(code):
            return None
        try:
            now = utcnow()
            row = self.model.query.filter_by(code_hash=app_link.hash_code(code)).populate_existing().first()
            if row is None or row.used_at is not None or app_link.is_expired(row.created_at, now):
                self.db.session.rollback()
                return None
            epoch = self.db.session.execute(select(func.coalesce(self.user_model.session_epoch, 0)).where(
                self.user_model.id == row.user_id).with_for_update()).scalar_one_or_none()
            if epoch is None or int(epoch) != int(row.session_epoch or 0):
                self.db.session.rollback()
                return None
            claimed = self.db.session.execute(update(self.model).where(self.model.id == row.id,
                self.model.used_at.is_(None), self.model.created_at > now - timedelta(seconds=app_link.CODE_TTL_SECONDS))
                .values(used_at=now).returning(self.model.user_id),
                execution_options={'synchronize_session': False}).scalar_one_or_none()
            valid = allowed_path(row.next_path) and app_link.codes_match(code, row.code_hash)
            next_path = row.next_path
            self.db.session.commit()
            if claimed is None or not valid:
                return None
            self.db.session.expire_all()
            user = self.db.session.get(self.user_model, claimed)
            return AppLinkRedemption(user, int(epoch), next_path) if user else None
        except Exception:
            self.db.session.rollback()
            raise
