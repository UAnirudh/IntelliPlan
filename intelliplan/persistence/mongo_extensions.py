"""Native MongoDB extension credential storage."""
import secrets

from intelliplan.persistence.accounts import PasswordConflict
from intelliplan.persistence.mongo_accounts import Account
from time_utils import utcnow


class MongoExtensionTokens:
    def __init__(self, store):
        self.store = store

    def ensure_indexes(self):
        self.store.database.extension_tokens.create_index('id', unique=True)
        self.store.database.extension_tokens.create_index('token', unique=True)
        self.store.database.extension_tokens.create_index('user_id')

    def issue(self, user_id, *, reuse=False, expected_hash=None, expected_epoch=None):
        token = secrets.token_hex(32)

        def write(db, active):
            owner = {'id': int(user_id)}
            if expected_hash is not None:
                owner['password_hash'] = expected_hash
            if expected_epoch is not None:
                owner['$expr'] = {'$eq': [{'$ifNull': ['$session_epoch', 0]}, int(expected_epoch)]}
            if not db.users.update_one(owner, {'$inc': {'_auth_revision': 1}},
                                       session=active).matched_count:
                raise PasswordConflict('Account credentials changed. Sign in again.')
            if reuse:
                existing = db.extension_tokens.find_one({'user_id': int(user_id)},
                    sort=[('id', 1)], session=active)
                if existing:
                    return existing['token']
            db.extension_tokens.insert_one({
                'id': self.store.next_id('extension_tokens', session=active),
                'user_id': int(user_id), 'token': token, 'created_at': utcnow().isoformat()}, session=active)
            return token

        return self.store.transaction(write)

    def revoke(self, token):
        self.store.database.extension_tokens.delete_many({'token': token})

    def user_for(self, token):
        if not token:
            return None
        # Resolve the token and account from one consistent snapshot.
        def read(db, active):
            row = db.extension_tokens.find_one({'token': token}, session=active)
            if row is None:
                return None
            user = db.users.find_one({'id': row['user_id']}, session=active)
            return Account(user) if user else None

        return self.store.transaction(read)
