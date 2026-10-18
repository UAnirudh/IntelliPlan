"""Native account reads and atomic credential writes over migrated documents."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from flask_login import UserMixin
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from intelliplan.persistence.accounts import (
    AccountExists, CredentialChange, IdentityConflict, LoginFailures, PasswordConflict, normalize_email,
    policy_acknowledgements, policy_owner, preference_fields, registration_fields,
)
from intelliplan.persistence.codec import pack_document, unpack_document, update_fields
from time_utils import utcnow


# Match the account model's defaults, including the privacy choices. Restored
# fields win over these defaults, so existing consent and billing are preserved.
DEFAULTS = {
    "password_hash": "", "google_id": None, "name": None,
    "sms_reminders_opt_in": False, "push_reminders_opt_in": False,
    "reminder_lead_minutes": 60, "sms_carrier": "tmobile",
    "email_reminders_opt_in": False, "utc_offset_minutes": 0,
    "quiet_hours_enabled": True, "quiet_hours_start": 22, "quiet_hours_end": 7,
    "session_epoch": 0, "failed_login_count": 0, "login_locked_until": None,
    "birth_year": None, "parent_email": None, "parent_consent_granted": False,
    "parent_consent_at": None, "parent_consent_notice_version": None,
    "parent_consent_token": None, "lms_preferences": "{}",
    "ai_personalization_opt_in": False, "marketing_emails_opt_in": False,
    "marketing_opt_in_at": None, "marketing_consent_version": None,
    "role": "student", "focus_enforcement": "off", "focus_alarm_file": None,
    "focus_grace_seconds": 25, "paid_until": None, "referral_rewarded_at": None,
    "streak_emails_opt_in": True, "stripe_customer_id": None,
    "stripe_paid_until": None, "stripe_event_created": None,
    "monthly_tutor_messages": 0, "tutor_reset_date": None, "plan_tier": None,
    "premium_model_pref": "auto", "premium_clarify": True,
    "ai_low_budget_notified": None, "first_touch_json": None,
    "buddies_opt_in": False, "buddies_consent_version": None,
    "buddies_consent_at": None, "referral_code": None, "referred_by_id": None,
    "phone": None, "notification_kinds": None,
}
DATE_FIELDS = {
    "created_at", "login_locked_until", "parent_consent_at", "marketing_opt_in_at",
    "paid_until", "referral_rewarded_at", "stripe_paid_until", "tutor_reset_date",
    "buddies_consent_at",
}


class Account(UserMixin):
    """Detached account value. It never lazily connects to another database."""

    def __init__(self, document):
        fields = {**DEFAULTS, **unpack_document(document)}
        fields.pop("_id", None)
        fields.pop("_ip_encoding", None)
        for key in DATE_FIELDS:
            if isinstance(fields.get(key), str):
                fields[key] = datetime.fromisoformat(fields[key])
        self.__dict__.update(fields)


class MongoAccounts:
    def __init__(self, store):
        self.store = store
        self.collection = store.database.users

    def ensure_indexes(self):
        # Partial indexes allow many absent/null OAuth and referral values, as
        # the PostgreSQL unique constraints did. Sparse alone indexes nulls.
        self.collection.create_index("id", unique=True)
        self.collection.create_index("email", unique=True)
        for field in ("google_id", "referral_code"):
            self.collection.create_index(field, unique=True,
                partialFilterExpression={field: {"$type": "string"}})
        self.store.database.password_reset_tokens.create_index("id", unique=True)
        self.store.database.password_reset_tokens.create_index("token_hash", unique=True)
        self.store.database.password_reset_tokens.create_index([("user_id", 1), ("created_at", -1)])
        self.store.database.policy_acknowledgements.create_index('id', unique=True)
        self.store.database.policy_acknowledgements.create_index([('user_id', 1), ('doc', 1)])
        self.store.database.policy_acknowledgements.create_index([('guest_session_id', 1), ('doc', 1)])
        self.store.database.security_events.create_index('id', unique=True)
        self.store.database.security_events.create_index([('user_id', 1), ('created_at', -1)])
        self.store.database.security_events.create_index([('event', 1), ('created_at', -1)])

    def get(self, user_id):
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            return None
        row = self.collection.find_one({"id": user_id})
        return Account(row) if row else None

    def by_email(self, email):
        row = self.collection.find_one({"email": normalize_email(email)})
        return Account(row) if row else None

    def count(self):
        return self.collection.count_documents({})

    def set_preferences(self, user_id, values):
        values = preference_fields(values)
        stages = [{'$set': {field: {'$literal': value} for field, value in values.items()}}] if values else []
        stages.append({'$set': {'sms_reminders_opt_in': {'$cond': [
                {'$in': [{'$ifNull': ['$phone', None]}, [None, '']]}, False,
                {'$ifNull': ['$sms_reminders_opt_in', False]},
            ]}}})
        row = self.collection.find_one_and_update({'id': int(user_id)}, stages,
            return_document=ReturnDocument.AFTER)
        if row is None:
            raise LookupError('Account no longer exists')
        return Account(row)

    def latest_policy(self, doc, *, user_id=None, guest_session_id=None):
        owner = policy_owner(user_id=user_id, guest_session_id=guest_session_id)
        row = self.store.database.policy_acknowledgements.find_one(
            {'doc': doc, **owner}, sort=[('version', -1)])
        return SimpleNamespace(**unpack_document(row)) if row else None

    def has_policy(self, *, user_id):
        return self.store.database.policy_acknowledgements.find_one(
            {'user_id': int(user_id)}, {'_id': 1}) is not None

    def acknowledge_policy(self, doc, version, *, user_id=None, guest_session_id=None, user_agent=''):
        owner = policy_owner(user_id=user_id, guest_session_id=guest_session_id)

        def write(db, active):
            if user_id is not None:
                # Coordinate account-owned child writes with account removal.
                if not db.users.update_one({'id': int(user_id)}, {'$inc': {'_auth_revision': 1}},
                                           session=active).matched_count:
                    raise LookupError('Account no longer exists')
            row = db.policy_acknowledgements.find_one({'doc': doc, **owner},
                sort=[('version', -1)], session=active)
            accepted = int(row['version']) if row else 0
            if accepted < int(version):
                db.policy_acknowledgements.insert_one({
                    'id': self.store.next_id('policy_acknowledgements', session=active),
                    'user_id': None, 'guest_session_id': None, **owner,
                    'doc': doc, 'version': int(version),
                    'user_agent': str(user_agent or '')[:256],
                    'acknowledged_at': utcnow().isoformat()}, session=active)
                accepted = int(version)
            return accepted

        return self.store.transaction(write)

    def resolve_google(self, *, google_id, email, name, allow_email_link=False):
        if not google_id:
            raise ValueError('Google identity is required')

        def write(db, active):
            row = db.users.find_one({'google_id': google_id}, session=active)
            if row is not None:
                return Account(row), False
            row = db.users.find_one({'email': normalize_email(email)}, session=active)
            if row is not None:
                if not allow_email_link:
                    raise IdentityConflict('Sign in to your existing account before linking Google')
                if row.get('google_id') not in (None, google_id):
                    raise IdentityConflict('This account is linked to a different Google identity')
                values = {'google_id': google_id}
                if not unpack_document(row).get('name'):
                    values['name'] = name
                changed = db.users.find_one_and_update({'_id': row['_id']}, update_fields(values),
                    return_document=ReturnDocument.AFTER, session=active)
                return Account(changed), False
            row = {**DEFAULTS, 'id': self.store.next_id('users', session=active),
                   'email': normalize_email(email), 'google_id': google_id, 'name': name,
                   'password_hash': '', 'created_at': utcnow().isoformat()}
            db.users.insert_one(pack_document(row), session=active)
            return Account(row), True

        for attempt in range(2):
            try:
                return self.store.transaction(write)
            except DuplicateKeyError:
                if attempt:
                    raise

    def record_event(self, **values):
        self.store.database.security_events.insert_one({
            'id': self.store.next_id('security_events'), 'created_at': utcnow().isoformat(), **values})

    def failed_login(self, user_id, *, threshold=8, lock_minutes=15):
        now = utcnow()
        attempts = {'$add': [{'$ifNull': ['$failed_login_count', 0]}, 1]}
        locked = {'$gte': [attempts, threshold]}
        row = self.collection.find_one_and_update({'id': int(user_id), '$or': [
            {'login_locked_until': None}, {'login_locked_until': {'$lte': now.isoformat()}}]},
            [{'$set': {'failed_login_count': {'$cond': [locked, 0, attempts]},
                'login_locked_until': {'$cond': [locked,
                    {'$literal': (now + timedelta(minutes=lock_minutes)).isoformat()}, '$login_locked_until']}}}],
            return_document=ReturnDocument.AFTER)
        if row is None:
            return None
        until = Account(row).login_locked_until
        return LoginFailures(threshold if until and until > now else int(row['failed_login_count']), until)

    def clear_failures(self, user_id, *, expected_hash):
        row = self.collection.find_one_and_update({'id': int(user_id), 'password_hash': expected_hash,
            '$or': [{'login_locked_until': None}, {'login_locked_until': {'$lte': utcnow().isoformat()}}]},
            {'$set': {'failed_login_count': 0, 'login_locked_until': None}},
            return_document=ReturnDocument.AFTER)
        return CredentialChange(int(user_id), int(row.get('session_epoch') or 0)) if row else None

    def create(self, *, email, password_hash, name=None, attributes=None, acknowledgements=()):
        attributes = registration_fields(attributes)
        acknowledgements = policy_acknowledgements(acknowledgements)
        for key in DATE_FIELDS & attributes.keys():
            if isinstance(attributes[key], datetime):
                attributes[key] = attributes[key].isoformat()

        def write(db, active):
            document = {**DEFAULTS, **attributes, 'id': self.store.next_id('users', session=active),
                        'email': normalize_email(email), 'password_hash': password_hash,
                        'name': name, 'created_at': utcnow().isoformat()}
            db.users.insert_one(pack_document(document), session=active)
            for acknowledgement in acknowledgements:
                db.policy_acknowledgements.insert_one({
                    'id': self.store.next_id('policy_acknowledgements', session=active),
                    'user_id': document['id'], 'guest_session_id': None,
                    'acknowledged_at': document['created_at'], **acknowledgement}, session=active)
            return Account(document)

        try:
            return self.store.transaction(write)
        except DuplicateKeyError:
            if self.by_email(email) is not None:
                raise AccountExists("An account with that email already exists.") from None
            raise

    def change_password(self, user_id, *, expected_hash, password_hash):
        # Match the hash the user actually proved. A concurrent password reset
        # must never be overwritten by a stale authenticated request.
        row = self.collection.find_one_and_update(
            {"id": int(user_id), "password_hash": expected_hash},
            [{"$set": {"password_hash": {"$literal": password_hash},
                       "failed_login_count": 0, "login_locked_until": None,
                       "session_epoch": {"$add": [{"$ifNull": ["$session_epoch", 0]}, 1]}}}],
            return_document=ReturnDocument.AFTER)
        if row is None:
            raise PasswordConflict("Account credentials changed. Sign in again.")
        return CredentialChange(int(user_id), int(row["session_epoch"]))

    def issue_reset(self, user_id, *, token_hash, requested_ip, ttl_minutes=60):
        def write(db, active):
            # Force a parent write to coordinate issuance with completion and
            # eventual account removal; a snapshot read alone is insufficient.
            if not db.users.update_one({"id": int(user_id)}, {"$inc": {"_auth_revision": 1}},
                                       session=active).matched_count:
                raise LookupError("Account no longer exists")
            now = utcnow()
            db.password_reset_tokens.update_many({"user_id": int(user_id), "used_at": None},
                {"$set": {"used_at": now.isoformat()}}, session=active)
            db.password_reset_tokens.insert_one({
                "id": self.store.next_id("password_reset_tokens", session=active),
                "user_id": int(user_id), "token_hash": token_hash,
                "expires_at": (now + timedelta(minutes=ttl_minutes)).isoformat(),
                "used_at": None, "created_at": now.isoformat(), "requested_ip": requested_ip}, session=active)
        self.store.transaction(write)

    def reset_usable(self, token_hash):
        return self.store.database.password_reset_tokens.find_one({"token_hash": token_hash,
            "used_at": None, "expires_at": {"$gt": utcnow().isoformat()}}, {"_id": 1}) is not None

    def recent_resets(self, user_id):
        return self.store.database.password_reset_tokens.count_documents({"user_id": int(user_id),
            "created_at": {"$gte": (utcnow() - timedelta(hours=1)).isoformat()}})

    def complete_reset(self, token_hash, *, password_hash):
        def write(db, active):
            now = utcnow().isoformat()
            token = db.password_reset_tokens.find_one({"token_hash": token_hash,
                "used_at": None, "expires_at": {"$gt": now}}, session=active)
            if token is None:
                return None
            row = db.users.find_one_and_update({"id": token["user_id"]}, [{"$set": {
                "password_hash": {"$literal": password_hash}, "failed_login_count": 0,
                "login_locked_until": None,
                "session_epoch": {"$add": [{"$ifNull": ["$session_epoch", 0]}, 1]}}}],
                return_document=ReturnDocument.AFTER, session=active)
            if row is None:
                return None
            db.password_reset_tokens.update_many({"user_id": token["user_id"], "used_at": None},
                {"$set": {"used_at": now}}, session=active)
            return CredentialChange(int(row["id"]), int(row["session_epoch"]))
        return self.store.transaction(write)
