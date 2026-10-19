"""Address suppressions persist independently of deleted/recreated accounts."""
import re
import secrets
from datetime import timezone
from types import SimpleNamespace

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from intelliplan.persistence.codec import pack_document, unpack_document, update_fields
from intelliplan.persistence.mongo_accounts import Account
from time_utils import utcnow


class MongoEmailState:
    def __init__(self, store):
        self.store = store

    def ensure_indexes(self):
        self.store.database.email_suppressions.create_index('id', unique=True)
        self.store.database.email_suppressions.create_index('email', unique=True)
        self.store.database.email_sends.create_index('id', unique=True)
        self.store.database.email_sends.create_index([('user_id', 1), ('email_key', 1)], unique=True)

    def is_suppressed(self, address):
        return self.store.database.email_suppressions.find_one(
            {'email': address.strip().lower()}, {'_id': 1}) is not None

    def suppress(self, address, reason='unsubscribe'):
        address = address.strip().lower()
        if not address:
            return False
        def write(db, active):
            if db.email_suppressions.find_one({'email': address}, session=active):
                return True
            db.email_suppressions.insert_one(pack_document({
                'id': self.store.next_id('email_suppressions', session=active),
                'email': address, 'reason': reason[:32], 'created_at': utcnow().isoformat(),
            }), session=active)
            return True
        try:
            return self.store.transaction(write)
        except DuplicateKeyError:
            return self.is_suppressed(address)

    def unsubscribe(self, address):
        address = address.strip().lower()
        if not address:
            return False
        def write(db, active):
            if db.email_suppressions.find_one({'email': address}, session=active) is None:
                db.email_suppressions.insert_one(pack_document({
                    'id': self.store.next_id('email_suppressions', session=active),
                    'email': address, 'reason': 'unsubscribe', 'created_at': utcnow().isoformat(),
                }), session=active)
            # Preserve reminder consent and dated marketing evidence. Changing
            # existing accounts here also coordinates with account erasure.
            db.users.update_many({'email': {'$regex': '^' + re.escape(address) + '$', '$options': 'i'}},
                update_fields({'marketing_emails_opt_in': False}), session=active)
            return True
        try:
            return self.store.transaction(write)
        except DuplicateKeyError:
            # A concurrent suppression may have won; re-run the entire update.
            return self.store.transaction(write)

    def claim_budget_notice(self, user_id, period):
        if not isinstance(period, str) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period):
            raise ValueError('Invalid budget period')
        def write(db, active):
            raw = db.users.find_one_and_update(
                {'id': int(user_id), '$or': [
                    {'ai_low_budget_notified': None}, {'ai_low_budget_notified': {'$lt': period}}]},
                update_fields({'ai_low_budget_notified': period}),
                return_document=ReturnDocument.AFTER, session=active)
            return Account(raw) if raw is not None else None
        return self.store.transaction(write)

    @staticmethod
    def _owner(db, user_id, active):
        result = db.users.update_one({'id': int(user_id)}, {'$inc': {'_email_revision': 1}}, session=active)
        if not result.matched_count:
            raise LookupError('Account no longer exists')

    def claim_send(self, user_id, email_key):
        if not isinstance(email_key, str) or not email_key or len(email_key) > 64:
            raise ValueError('Invalid email identity')
        def write(db, active):
            self._owner(db, user_id, active)
            raw = db.email_sends.find_one({'user_id': int(user_id), 'email_key': email_key}, session=active)
            if raw is not None and raw.get('status') != 'failed':
                return None
            values = {'status': 'pending', '_claim_token': secrets.token_hex(16)}
            if raw is None:
                values.update(id=self.store.next_id('email_sends', session=active), user_id=int(user_id),
                              email_key=email_key, sent_at=utcnow().isoformat(), provider_message_id=None)
                db.email_sends.insert_one(pack_document(values), session=active)
                return SimpleNamespace(**values)
            db.email_sends.update_one({'_id': raw['_id']}, update_fields(values), session=active)
            return SimpleNamespace(**{**unpack_document(raw), **values})
        return self.store.transaction(write)

    def record_send(self, expected, status, message_id=None):
        if status not in {'sent', 'failed'}:
            raise ValueError('Invalid email outcome')
        def write(db, active):
            self._owner(db, expected.user_id, active)
            values = {'status': status}
            if isinstance(message_id, str):
                values['provider_message_id'] = message_id[:128]
            result = db.email_sends.update_one({'id': expected.id, 'user_id': expected.user_id,
                'email_key': expected.email_key, 'status': 'pending', '_claim_token': expected._claim_token},
                update_fields(values), session=active)
            return result.matched_count == 1
        return self.store.transaction(write)

    def onboarding_exists(self, model_name, user_id):
        collections = {'LinkedAccount': 'linked_accounts', 'CanvasIntegration': 'canvas_integrations',
            'GoogleIntegration': 'google_integrations', 'ClassroomIntegration': 'classroom_integrations',
            'BlackboardIntegration': 'blackboard_integrations', 'MoodleIntegration': 'moodle_integrations',
            'NotionIntegration': 'notion_integrations', 'SavedSchedule': 'saved_schedules'}
        name = collections.get(model_name)
        if name is None:
            raise ValueError('Unsupported onboarding goal')
        return self.store.database[name].find_one({'user_id': int(user_id)}, {'_id': 1}) is not None

    def completed_session(self, user_id):
        return self.store.database.active_sessions.find_one(
            {'user_id': int(user_id), 'state': 'completed'}, {'_id': 1}) is not None

    @staticmethod
    def utc_naive(value):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value

    def onboarding_candidates(self, oldest, youngest, limit):
        # Restored timestamps can be ISO strings; native fixtures may use BSON
        # dates. Decode through Account rather than compare unlike BSON types.
        oldest, youngest = self.utc_naive(oldest), self.utc_naive(youngest)
        result = []
        if limit <= 0:
            return result
        for raw in self.store.database.users.find({}).sort('id', 1):
            account = Account(raw)
            created = getattr(account, 'created_at', None)
            if created is not None and oldest <= self.utc_naive(created) <= youngest:
                result.append(account)
                if len(result) >= limit:
                    break
        return result

    def onboarding_history(self, user_ids, step_keys):
        rows = self.store.database.email_sends.find({'user_id': {'$in': list(user_ids)},
            'email_key': {'$in': list(step_keys)}, 'status': {'$ne': 'failed'}}, {'user_id': 1, 'email_key': 1})
        return [(row['user_id'], row['email_key']) for row in rows]

    def settled_send_users(self, email_key):
        return set(self.store.database.email_sends.distinct('user_id',
            {'email_key': email_key, 'status': {'$ne': 'failed'}}))

    def campaign_candidates(self, *, oldest=None, newest=None, email_key=None, limit=None,
                            opted_in=False, admins=None):
        excluded = self.settled_send_users(email_key) if email_key else set()
        query = {'marketing_emails_opt_in': True} if opted_in else {}
        result = []
        if limit is not None and limit <= 0:
            return result
        for raw in self.store.database.users.find(query).sort('id', 1):
            if raw['id'] in excluded:
                continue
            if opted_in and raw.get('marketing_emails_opt_in') is not True:
                continue
            account = Account(raw)
            if admins is not None and (account.email or '').strip().lower() not in admins:
                continue
            if oldest is not None or newest is not None:
                created = getattr(account, 'created_at', None)
                if created is None:
                    continue
                created = self.utc_naive(created)
                if oldest is not None and created < self.utc_naive(oldest):
                    continue
                if newest is not None and created > self.utc_naive(newest):
                    continue
            result.append(account)
            if limit is not None and len(result) >= limit:
                break
        return result

    def integration_types(self):
        return len(self.store.database.linked_accounts.distinct('login_type', {'login_type': {'$ne': None}}))
