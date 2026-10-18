"""One-time desktop handoff tickets with an atomic claim on both backends."""
from datetime import timedelta
from dataclasses import dataclass

from flask import current_app
from sqlalchemy import func, inspect, select, text, update

import desktop_auth
from time_utils import utcnow


@dataclass(frozen=True)
class DesktopRedemption:
    user: object
    session_epoch: int


def ensure_sql_ticket_epoch(db):
    inspector = inspect(db.engine)
    if 'desktop_auth_codes' not in inspector.get_table_names():
        return
    if 'session_epoch' not in {column['name'] for column in inspector.get_columns('desktop_auth_codes')}:
        db.session.execute(text('ALTER TABLE desktop_auth_codes ADD COLUMN session_epoch INTEGER DEFAULT 0'))
        db.session.commit()


def desktop_tickets():
    repository = current_app.extensions.get('desktop_tickets_repository')
    if repository is not None:
        return repository
    from App import DesktopAuthCode, User, db
    return SQLDesktopTickets(db, DesktopAuthCode, User)


class SQLDesktopTickets:
    def __init__(self, db, model, user_model):
        self.db, self.model, self.user_model = db, model, user_model

    def issue(self, user_id, *, code_hash, code_challenge, expected_epoch=None):
        if not desktop_auth.is_valid_challenge(code_challenge):
            raise ValueError('A valid desktop challenge is required')
        try:
            epoch = self.db.session.execute(select(func.coalesce(self.user_model.session_epoch, 0)).where(
                self.user_model.id == int(user_id)).with_for_update()).scalar_one_or_none()
            if epoch is None:
                raise LookupError('Account no longer exists')
            if expected_epoch is not None and epoch != int(expected_epoch):
                raise ValueError('Account credentials changed. Sign in again.')
            self.db.session.add(self.model(user_id=int(user_id), code_hash=code_hash,
                code_challenge=code_challenge, created_at=utcnow(), session_epoch=int(epoch)))
            self.db.session.commit()
        except Exception:
            self.db.session.rollback()
            raise

    def redeem(self, *, code_hash, verifier):
        if not desktop_auth.is_valid_verifier(verifier):
            return None
        try:
            now = utcnow()
            row = self.model.query.filter_by(code_hash=code_hash).first()
            if row is None or row.used_at is not None or desktop_auth.is_expired(row.created_at, now):
                self.db.session.rollback()
                return None
            # Coordinate child lifecycle with account deletion; the conditional
            # ticket update below determines which concurrent claimant wins.
            epoch = self.db.session.execute(select(func.coalesce(self.user_model.session_epoch, 0)).where(
                self.user_model.id == row.user_id).with_for_update()).scalar_one_or_none()
            if epoch is None or int(epoch) != int(row.session_epoch or 0):
                self.db.session.rollback()
                return None
            claimed = self.db.session.execute(update(self.model).where(
                self.model.id == row.id, self.model.used_at.is_(None),
                self.model.created_at > now - timedelta(seconds=desktop_auth.CODE_TTL_SECONDS),
            ).values(used_at=now).returning(self.model.user_id),
                execution_options={'synchronize_session': False}).scalar_one_or_none()
            valid = desktop_auth.verifier_matches(verifier, row.code_challenge)
            self.db.session.commit()
            if claimed is None or not valid:
                return None
            self.db.session.expire_all()
            user = self.db.session.get(self.user_model, claimed)
            return DesktopRedemption(user, int(epoch)) if user else None
        except Exception:
            self.db.session.rollback()
            raise

