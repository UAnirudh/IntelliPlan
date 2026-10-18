"""Account repository boundary shared by cookie and bearer authentication.

The Mongo implementation returns ordinary UserMixin records; callers must save
changes through the repository rather than depending on an ORM session.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import secrets
from flask import current_app
from sqlalchemy import and_, case, func, literal, or_, select, update
from sqlalchemy.exc import IntegrityError
from time_utils import utcnow


class AccountExists(ValueError):
    pass


class PasswordConflict(ValueError):
    """The credential changed since the caller proved knowledge of it."""


class IdentityConflict(ValueError):
    """A verified provider identity conflicts with an existing binding."""


REGISTRATION_FIELDS = frozenset({
    'role', 'phone', 'sms_reminders_opt_in', 'birth_year', 'parent_email',
    'parent_consent_granted', 'parent_consent_token', 'marketing_emails_opt_in',
    'marketing_opt_in_at', 'marketing_consent_version',
})

PREFERENCE_FIELDS = frozenset({
    'ai_personalization_opt_in', 'phone', 'sms_reminders_opt_in',
    'push_reminders_opt_in', 'reminder_lead_minutes', 'sms_carrier',
})


def preference_fields(values):
    changes = dict(values)
    if changes.keys() - PREFERENCE_FIELDS:
        raise ValueError('Unsupported account preference fields')
    return changes


def initial_age_values(birth_year, parent_email, *, email, consent_token):
    from intelliplan.email.eligibility import age_from_birth_year
    birth_year = int(birth_year)
    now = utcnow()
    if not 1900 <= birth_year <= now.year:
        raise ValueError('Please enter a valid birth year.')
    child = age_from_birth_year(birth_year, now) < 13
    parent_email = normalize_email(parent_email or '')
    if child and ('@' not in parent_email or parent_email == normalize_email(email)):
        raise ValueError('A different parent or guardian email is required.')
    values = {'birth_year': birth_year, 'parent_consent_granted': not child,
              'parent_email': parent_email if child else None,
              'parent_consent_token': consent_token if child else None,
              'parent_consent_at': None, 'parent_consent_notice_version': None}
    if child:
        values.update(marketing_emails_opt_in=False, marketing_opt_in_at=None)
    return values


def registration_fields(attributes):
    values = dict(attributes or {})
    if values.keys() - REGISTRATION_FIELDS:
        raise ValueError('Unsupported account registration fields')
    return values


def policy_acknowledgements(values):
    result = []
    for value in values:
        if value.keys() - {'doc', 'version', 'user_agent'}:
            raise ValueError('Unsupported policy acknowledgement fields')
        result.append({'doc': value['doc'], 'version': int(value['version']),
                       'user_agent': str(value.get('user_agent') or '')[:256]})
    return tuple(result)


@dataclass(frozen=True)
class CredentialChange:
    user_id: int
    session_epoch: int


@dataclass(frozen=True)
class LoginFailures:
    attempts: int
    locked_until: datetime | None


def normalize_email(value: str) -> str:
    return value.strip().lower()


def policy_owner(*, user_id=None, guest_session_id=None):
    if (user_id is None) == (guest_session_id is None):
        raise ValueError('Exactly one policy owner is required')
    if user_id is not None:
        return {'user_id': int(user_id)}
    if not isinstance(guest_session_id, str) or not guest_session_id:
        raise ValueError('A guest session is required')
    return {'guest_session_id': guest_session_id}


def accounts():
    repository = current_app.extensions.get("accounts_repository")
    if repository is not None:
        return repository
    # Import only for the legacy backend. A native app must never import App.py
    # here, because importing it initializes a PostgreSQL engine and migrations.
    from App import PasswordResetToken, PolicyAcknowledgement, SecurityEvent, User, db
    return SQLAccounts(db, User, reset_model=PasswordResetToken,
                       policy_model=PolicyAcknowledgement, security_model=SecurityEvent)


class SQLAccounts:
    def __init__(self, db, model, *, reset_model=None, policy_model=None, security_model=None):
        self.db = db
        self.model = model
        self.reset_model = reset_model
        self.policy_model = policy_model
        self.security_model = security_model

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

    def set_preferences(self, user_id, values):
        values = preference_fields(values)
        # Derive the SMS invariant from the row being updated, not a caller's
        # possibly stale account snapshot. A removed phone cannot stay opted in.
        phone = literal(values['phone']) if 'phone' in values else self.model.phone
        opt_in = values.get('sms_reminders_opt_in', self.model.sms_reminders_opt_in)
        values['sms_reminders_opt_in'] = case(
            (and_(phone.is_not(None), phone != ''), opt_in), else_=False)
        try:
            changed = self.db.session.execute(update(self.model).where(
                self.model.id == int(user_id)).values(**values).returning(self.model.id),
                execution_options={'synchronize_session': False}).scalar_one_or_none()
            if changed is None:
                raise LookupError('Account no longer exists')
            self.db.session.commit()
            self.db.session.expire_all()
            return self.get(user_id)
        except Exception:
            self.db.session.rollback()
            raise

    def establish_age(self, user_id, *, birth_year, parent_email=None):
        consent_token = secrets.token_urlsafe(24)
        try:
            user = self.model.query.populate_existing().filter_by(id=int(user_id)).with_for_update().first()
            if user is None:
                raise LookupError('Account no longer exists')
            if user.birth_year is None:
                values = initial_age_values(birth_year, parent_email,
                                            email=user.email, consent_token=consent_token)
                for field, value in values.items():
                    setattr(user, field, value)
            self.db.session.commit()
            return user
        except Exception:
            self.db.session.rollback()
            raise

    def by_parent_token(self, token):
        if not token:
            return None
        return self.model.query.populate_existing().filter_by(parent_consent_token=token).first()

    def approve_parent(self, token, *, notice_version):
        if not token:
            return None
        try:
            user_id = self.db.session.execute(update(self.model).where(
                self.model.parent_consent_token == token,
                or_(self.model.parent_consent_granted.is_(False), self.model.parent_consent_granted.is_(None)),
            ).values(parent_consent_granted=True, parent_consent_token=None,
                     parent_consent_at=utcnow(), parent_consent_notice_version=notice_version).returning(
                         self.model.id), execution_options={'synchronize_session': False}).scalar_one_or_none()
            self.db.session.commit()
            self.db.session.expire_all()
            return self.get(user_id) if user_id is not None else None
        except Exception:
            self.db.session.rollback()
            raise

    def latest_policy(self, doc, *, user_id=None, guest_session_id=None):
        owner = policy_owner(user_id=user_id, guest_session_id=guest_session_id)
        return self.policy_model.query.filter_by(doc=doc, **owner).order_by(
            self.policy_model.version.desc()).first()

    def has_policy(self, *, user_id):
        return self.policy_model.query.filter_by(user_id=int(user_id)).first() is not None

    def acknowledge_policy(self, doc, version, *, user_id=None, guest_session_id=None, user_agent=''):
        owner = policy_owner(user_id=user_id, guest_session_id=guest_session_id)
        try:
            if user_id is not None:
                found = self.db.session.execute(select(self.model.id).where(
                    self.model.id == int(user_id)).with_for_update()).scalar_one_or_none()
                if found is None:
                    raise LookupError('Account no longer exists')
            row = self.latest_policy(doc, **owner)
            accepted = int(row.version) if row is not None else 0
            if accepted < int(version):
                self.db.session.add(self.policy_model(doc=doc, version=int(version),
                    user_agent=str(user_agent or '')[:256], **owner))
                accepted = int(version)
            self.db.session.commit()
            return accepted
        except Exception:
            self.db.session.rollback()
            raise

    def resolve_google(self, *, google_id, email, name, allow_email_link=False):
        """Resolve only after the caller authenticates the Google subject."""
        if not google_id:
            raise ValueError('Google identity is required')
        for attempt in range(2):
            try:
                user = self.model.query.populate_existing().filter_by(google_id=google_id).first()
                if user is not None:
                    return user, False
                user = self.model.query.populate_existing().filter_by(
                    email=normalize_email(email)).with_for_update().first()
                created = user is None
                if user is None:
                    user = self.model(email=normalize_email(email), google_id=google_id,
                                      name=name, password_hash='')
                    self.db.session.add(user)
                else:
                    if not allow_email_link:
                        raise IdentityConflict('Sign in to your existing account before linking Google')
                    if user.google_id not in (None, google_id):
                        raise IdentityConflict('This account is linked to a different Google identity')
                    values = {'google_id': google_id}
                    if not user.name:
                        values['name'] = name
                    changed = self.db.session.execute(update(self.model).where(
                        self.model.id == user.id, or_(self.model.google_id.is_(None),
                            self.model.google_id == google_id)).values(**values),
                        execution_options={'synchronize_session': False})
                    if changed.rowcount != 1:
                        raise IdentityConflict('This account is linked to a different Google identity')
                self.db.session.commit()
                self.db.session.expire_all()
                return user, created
            except IntegrityError:
                self.db.session.rollback()
                if attempt:
                    raise
            except Exception:
                self.db.session.rollback()
                raise

    def record_event(self, **values):
        if self.security_model is None:
            raise RuntimeError('Security event model is required')
        self.db.session.add(self.security_model(**values))
        self.db.session.commit()

    def failed_login(self, user_id, *, threshold=8, lock_minutes=15):
        now = utcnow()
        attempts = func.coalesce(self.model.failed_login_count, 0) + 1
        locked = attempts >= threshold
        row = self.db.session.execute(update(self.model).where(
            self.model.id == int(user_id), or_(self.model.login_locked_until.is_(None),
                self.model.login_locked_until <= now),
        ).values(failed_login_count=case((locked, 0), else_=attempts),
            login_locked_until=case((locked, now + timedelta(minutes=lock_minutes)),
                                   else_=self.model.login_locked_until)).returning(
            self.model.failed_login_count, self.model.login_locked_until),
            execution_options={'synchronize_session': False}).first()
        self.db.session.commit()
        self.db.session.expire_all()
        if row is None:
            return None
        return LoginFailures(threshold if row[1] and row[1] > now else int(row[0]), row[1])

    def clear_failures(self, user_id, *, expected_hash):
        now = utcnow()
        result = self.db.session.execute(update(self.model).where(
            self.model.id == int(user_id), self.model.password_hash == expected_hash,
            or_(self.model.login_locked_until.is_(None), self.model.login_locked_until <= now),
        ).values(failed_login_count=0, login_locked_until=None).returning(
            func.coalesce(self.model.session_epoch, 0)),
            execution_options={'synchronize_session': False})
        epoch = result.scalar_one_or_none()
        self.db.session.commit()
        self.db.session.expire_all()
        return CredentialChange(int(user_id), int(epoch or 0)) if epoch is not None else None

    def create(self, *, email, password_hash, name=None, attributes=None, acknowledgements=()):
        acknowledgements = policy_acknowledgements(acknowledgements)
        user = self.model(email=normalize_email(email), password_hash=password_hash, name=name,
                          **registration_fields(attributes))
        self.db.session.add(user)
        try:
            if acknowledgements:
                if self.policy_model is None:
                    raise RuntimeError('Policy acknowledgement model is required')
                self.db.session.flush()
                for acknowledgement in acknowledgements:
                    self.db.session.add(self.policy_model(user_id=user.id, **acknowledgement))
            self.db.session.commit()
        except IntegrityError:
            self.db.session.rollback()
            if self.by_email(email) is not None:
                raise AccountExists("An account with that email already exists.") from None
            raise
        except Exception:
            self.db.session.rollback()
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
