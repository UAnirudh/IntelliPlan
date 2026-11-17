"""MongoDB web sessions, compatible with migrated Flask-SQLAlchemy sessions."""
from __future__ import annotations

from datetime import datetime, timezone

from flask_session.base import ServerSideSession, ServerSideSessionInterface


class MongoSession(ServerSideSession):
    pass


def prepare_sessions(database):
    """Run once after snapshot verification, before enabling session writes.

    Expired sessions can then be removed by the ordinary TTL retention policy.
    Keep the original expiry string and binary data intact for active sessions.
    """
    store = database["sessions"]
    prepared = 0
    for row in store.find({"expires_at": {"$exists": False}, "expiry": {"$ne": None}}):
        expiry = row["expiry"]
        if isinstance(expiry, str):
            expiry = datetime.fromisoformat(expiry)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        result = store.update_one({"_id": row["_id"], "expiry": row["expiry"],
                                   "expires_at": {"$exists": False}}, {"$set": {"expires_at": expiry}})
        prepared += result.modified_count
    return prepared


class MongoSessionInterface(ServerSideSessionInterface):
    session_class = MongoSession
    ttl = True

    def __init__(self, app, backend):
        self.backend = backend
        self.store = backend.database["sessions"]
        self.store.create_index("session_id", unique=True)
        self.store.create_index('user_id')
        self.store.create_index("expires_at", expireAfterSeconds=0)
        super().__init__(
            app, key_prefix=app.config.get("SESSION_KEY_PREFIX", "session:"),
            use_signer=app.config.get("SESSION_USE_SIGNER", False),
            permanent=app.config.get("SESSION_PERMANENT", True),
            sid_length=app.config.get("SESSION_ID_LENGTH", 32),
            serialization_format=app.config.get("SESSION_SERIALIZATION_FORMAT", "msgpack"))

    def _retrieve_session_data(self, store_id):
        row = self.store.find_one({"session_id": store_id})
        if row is None:
            return None
        expiry = row.get("expiry")
        if isinstance(expiry, str):
            expiry = datetime.fromisoformat(expiry)
        if expiry is not None:
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry <= datetime.now(timezone.utc):
                self.store.delete_one({"_id": row["_id"], "expiry": row["expiry"]})
                return None
        return self.serializer.decode(bytes(row["data"]))

    def _delete_session(self, store_id):
        self.store.delete_one({"session_id": store_id})

    def _upsert_session(self, session_lifetime, session, store_id):
        expiry = datetime.now(timezone.utc) + session_lifetime
        user_id = str(session.get('_user_id') or '')
        values = {
            "data": self.serializer.encode(session), "expiry": expiry.isoformat(),
            "expires_at": expiry,
            'user_id': int(user_id) if user_id.isdigit() else None,
        }
        if not user_id.isdigit():
            self.store.update_one({'session_id': store_id}, {'$set': values}, upsert=True)
            return

        def write(db, active):
            epoch = int(session.get('session_epoch') or 0)
            owner = {'id': int(user_id), '$expr': {
                '$eq': [{'$ifNull': ['$session_epoch', 0]}, epoch]}}
            if not db.users.update_one(owner, {'$inc': {'_session_revision': 1}},
                                       session=active).matched_count:
                db.sessions.delete_one({'session_id': store_id}, session=active)
                return
            db.sessions.update_one({'session_id': store_id}, {'$set': values}, upsert=True, session=active)

        self.backend.transaction(write)
