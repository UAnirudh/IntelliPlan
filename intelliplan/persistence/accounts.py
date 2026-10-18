"""Account repository boundary shared by cookie and bearer authentication.

The Mongo implementation returns ordinary UserMixin records; callers must save
changes through the repository rather than depending on an ORM session.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from flask import current_app
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from time_utils import utcnow


class AccountExists(ValueError):
    pass


class PasswordConflict(ValueError):
    """The credential changed since the caller proved knowledge of it."""


@dataclass(frozen=True)
class CredentialChange:
    user_id: int
    session_epoch: int


def normalize_email(value: str) -> str:
    return value.strip().lower()


def accounts():
    repository = current_app.extensions.get("accounts_repository")
    if repository is not None:
        return repository
    # Import only for the legacy backend. A native app must never import App.py
    # here, because importing it initializes a PostgreSQL engine and migrations.
    from App import PasswordResetToken, User, db
    return SQLAccounts(db, User, reset_model=PasswordResetToken)


class SQLAccounts:
    def __init__(self, db, model, *, reset_model=None):
        self.db = db
        self.model = model
        self.reset_model = reset_model

    def get(self, user_id):
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            return None
        return self.db.session.get(self.model, user_id)

    def by_email(self, email):
        return self.model.query.filter_by(email=normalize_email(email)).first()

    def count(self):
        return self.model.query.count()

    def create(self, *, email, password_hash, name):
        user = self.model(email=normalize_email(email), password_hash=password_hash, name=name)
        self.db.session.add(user)
        try:
            self.db.session.commit()
        except IntegrityError:
            self.db.session.rollback()
            if self.by_email(email) is not None:
                raise AccountExists("An account with that email already exists.") from None
            raise
        return user

    def change_password(self, user_id, *, expected_hash, password_hash):
        result = self.db.session.execute(update(self.model).where(
            self.model.id == int(user_id), self.model.password_hash == expected_hash,
        ).values(password_hash=password_hash,
                 session_epoch=func.coalesce(self.model.session_epoch, 0) + 1,
                 failed_login_count=0, login_locked_until=None).returning(self.model.session_epoch),
            execution_options={"synchronize_session": False})
        epoch = result.scalar_one_or_none()
        if epoch is None:
            self.db.session.rollback()
            raise PasswordConflict("Account credentials changed. Sign in again.")
        self.db.session.commit()
        self.db.session.expire_all()
        return CredentialChange(int(user_id), int(epoch))

    def issue_reset(self, user_id, *, token_hash, requested_ip, ttl_minutes=60):
        token = self.reset_model
        now = utcnow()
        # Serialize issuance/completion for one account, including requests
        # that try to replace a token while another request is using it.
        user_id = self.db.session.execute(select(self.model.id).where(
            self.model.id == int(user_id)).with_for_update()).scalar_one_or_none()
        if user_id is None:
            raise LookupError("Account no longer exists")
        self.db.session.execute(update(token).where(token.user_id == user_id,
            token.used_at.is_(None)).values(used_at=now))
        self.db.session.add(token(user_id=user_id, token_hash=token_hash,
            expires_at=now + timedelta(minutes=ttl_minutes), created_at=now,
            requested_ip=requested_ip))
        self.db.session.commit()

    def reset_usable(self, token_hash):
        token = self.reset_model
        return self.db.session.execute(select(token.id).where(
            token.token_hash == token_hash, token.used_at.is_(None),
            token.expires_at > utcnow())).first() is not None

    def recent_resets(self, user_id):
        token = self.reset_model
        return self.db.session.execute(select(func.count()).select_from(token).where(
            token.user_id == int(user_id),
            token.created_at >= utcnow() - timedelta(hours=1))).scalar_one()

    def complete_reset(self, token_hash, *, password_hash):
        token = self.reset_model
        now = utcnow()
        # Lock the parent before token mutation, matching issuance lock order.
        owner = self.db.session.execute(select(token.user_id).where(
            token.token_hash == token_hash)).scalar_one_or_none()
        if owner is None:
            return None
        owner = self.db.session.execute(select(self.model.id).where(
            self.model.id == owner).with_for_update()).scalar_one_or_none()
        if owner is None:
            return None
        claimed = self.db.session.execute(update(token).where(
            token.token_hash == token_hash, token.used_at.is_(None), token.expires_at > now,
        ).values(used_at=now).returning(token.user_id)).scalar_one_or_none()
        if claimed is None:
            return None
        epoch = self.db.session.execute(update(self.model).where(self.model.id == owner).values(
            password_hash=password_hash, session_epoch=func.coalesce(self.model.session_epoch, 0) + 1,
            failed_login_count=0, login_locked_until=None).returning(
                self.model.session_epoch), execution_options={"synchronize_session": False}).scalar_one()
        self.db.session.execute(update(token).where(token.user_id == owner,
            token.used_at.is_(None)).values(used_at=now))
        self.db.session.commit()
        self.db.session.expire_all()
        return CredentialChange(int(owner), int(epoch))
