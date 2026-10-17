"""Native MongoDB connection lifecycle and transaction boundary.

The application port must use these operations explicitly. This module does
not emulate SQLAlchemy or silently send existing SQL queries to another store.
"""
from __future__ import annotations

import os
import threading
from typing import Callable, TypeVar

from pymongo import MongoClient, ReturnDocument
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

T = TypeVar("T")


class MongoStore:
    is_mongo_store = True

    def __init__(self, uri: str, database: str, *, client_factory=MongoClient):
        if not uri.startswith(("mongodb://", "mongodb+srv://")):
            raise ValueError("MONGODB_URI must be a MongoDB connection URI")
        if not database or database in {"admin", "config", "local"}:
            raise ValueError("MONGODB_DATABASE must name an application database")
        self._uri = uri
        self._name = database
        self._client_factory = client_factory
        self._client = None
        self._pid = None
        self._lock = threading.Lock()
        if hasattr(os, "register_at_fork"):
            os.register_at_fork(after_in_child=self._after_fork)

    @classmethod
    def from_env(cls):
        # Explicit credentials and database; never connect to localhost as a
        # fallback when the production environment is incomplete.
        return cls(os.environ["MONGODB_URI"], os.environ["MONGODB_DATABASE"])

    def _after_fork(self):
        self._lock = threading.Lock()
        self._client = None
        self._pid = None

    @property
    def client(self):
        with self._lock:
            if self._client is None or self._pid != os.getpid():
                self._client = self._client_factory(
                    self._uri, appname="IntelliPlan", serverSelectionTimeoutMS=15000,
                    connectTimeoutMS=10000, socketTimeoutMS=30000,
                    maxPoolSize=10, minPoolSize=0, retryWrites=True)
                self._pid = os.getpid()
            return self._client

    @property
    def database(self):
        return self.client.get_database(self._name, write_concern=WriteConcern("majority"))

    def ping(self):
        return self.client.admin.command("ping")["ok"] == 1

    def close(self):
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None
                self._pid = None

    def transaction(self, operation: Callable, *args, **kwargs) -> T:
        """Retry only database work; send emails/API calls after this returns.

        The driver may invoke operation more than once on transient conflicts.
        Every collection call inside it must receive the supplied session.
        No fallback to nontransactional writes is allowed.
        """
        with self.client.start_session() as session:
            return session.with_transaction(
                lambda active: operation(self.database, active, *args, **kwargs),
                read_concern=ReadConcern("snapshot"),
                write_concern=WriteConcern("majority"), max_commit_time_ms=15000)

    def next_id(self, collection: str, *, session=None) -> int:
        return self.reserve_ids(collection, 1, session=session)[0]

    def reserve_ids(self, collection: str, count: int, *, session=None) -> list[int]:
        if count < 0:
            raise ValueError("ID reservation size must be nonnegative")
        if count == 0:
            return []
        row = self.database["_sequences"].find_one_and_update(
            {"_id": collection}, {"$inc": {"value": count}}, upsert=True,
            return_document=ReturnDocument.AFTER, session=session)
        last = int(row["value"])
        return list(range(last - count + 1, last + 1))

    def seed_id(self, collection: str, high_watermark: int):
        if high_watermark < 0:
            raise ValueError("Sequence high-water mark must be nonnegative")
        self.database["_sequences"].update_one(
            {"_id": collection}, {"$max": {"value": high_watermark}}, upsert=True)
