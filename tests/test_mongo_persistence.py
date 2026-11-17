from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask, session

from intelliplan.persistence.mongo import MongoStore
from intelliplan.persistence.sessions import MongoSessionInterface, prepare_sessions

mongomock = pytest.importorskip("mongomock")


def test_store_requires_explicit_configuration():
    with pytest.raises(ValueError):
        MongoStore("postgres://example", "intelliplan")
    with pytest.raises(ValueError):
        MongoStore("mongodb://example", "admin")


def test_ids_start_above_the_migrated_high_watermark():
    client = mongomock.MongoClient()
    store = MongoStore("mongodb://example", "intelliplan", client_factory=lambda *a, **k: client)
    store.seed_id("users", 1000)
    assert store.next_id("users") == 1001
    store.seed_id("users", 10)
    assert store.next_id("users") == 1002


def test_clients_are_lazy_and_recreated_after_a_fork():
    clients = []

    def factory(*args, **kwargs):
        client = mongomock.MongoClient()
        clients.append(client)
        return client

    store = MongoStore("mongodb://example", "intelliplan", client_factory=factory)
    assert clients == []
    first = store.client
    assert store.client is first
    store._after_fork()
    assert store.client is not first
    assert len(clients) == 2


def make_app(database):
    app = Flask(__name__)
    app.secret_key = "test-only"
    backend = MongoStore('mongodb://test', database.name, client_factory=lambda *a, **k: database.client)
    backend.transaction = lambda work: work(backend.database, None)
    database.users.update_one({'id': 42}, {'$setOnInsert': {'session_epoch': 0}}, upsert=True)
    app.session_interface = MongoSessionInterface(app, backend)

    @app.get("/set")
    def set_value():
        session["_user_id"] = "42"
        session["oauth_state"] = "state"
        return "ok"

    @app.get("/read")
    def read_value():
        return dict(session)

    @app.get("/logout")
    def logout():
        session.clear()
        return "ok"

    @app.get("/regenerate")
    def regenerate():
        app.session_interface.regenerate(session)
        return "ok"

    return app


def test_sessions_survive_another_app_instance_and_logout():
    db = mongomock.MongoClient().intelliplan
    first = make_app(db).test_client()
    assert first.get("/set").status_code == 200
    cookie = first.get_cookie("session")
    second = make_app(db).test_client()
    second.set_cookie("session", cookie.value)
    assert second.get("/read").json["oauth_state"] == "state"
    assert second.get("/logout").status_code == 200
    assert first.get("/read").json.get("_user_id") is None


def test_expiry_is_enforced_before_mongodb_ttl_cleanup():
    db = mongomock.MongoClient().intelliplan
    app = make_app(db)
    interface = app.session_interface
    db.sessions.insert_one({"session_id": "session:expired", "expiry":
                            (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                            "data": interface.serializer.encode({"_user_id": "42"})})
    assert interface._retrieve_session_data("session:expired") is None


def test_migrated_binary_session_and_microsecond_expiry_are_readable():
    db = mongomock.MongoClient().intelliplan
    interface = make_app(db).session_interface
    db.sessions.insert_one({"session_id": "session:migrated", "expiry":
                            (datetime.now(timezone.utc) + timedelta(days=1)).replace(tzinfo=None).isoformat(),
                            "data": interface.serializer.encode({"_user_id": "7"})})
    assert interface._retrieve_session_data("session:migrated")["_user_id"] == "7"


def test_regeneration_invalidates_old_session_id():
    db = mongomock.MongoClient().intelliplan
    client = make_app(db).test_client()
    client.get("/set")
    old = client.get_cookie("session").value
    client.get("/regenerate")
    assert client.get_cookie("session").value != old
    stolen = make_app(db).test_client()
    stolen.set_cookie("session", old)
    assert stolen.get("/read").json.get("_user_id") is None


def test_prepare_migrated_sessions_preserves_original_data_and_adds_ttl():
    db = mongomock.MongoClient().intelliplan
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    db.sessions.insert_one({"session_id": "session:a", "expiry": future, "data": b"exact-data"})
    assert prepare_sessions(db) == 1
    row = db.sessions.find_one()
    assert row["data"] == b"exact-data"
    assert row["expiry"] == future
    assert isinstance(row["expires_at"], datetime)
    assert prepare_sessions(db) == 0


def test_owned_session_save_cannot_resurrect_a_deleted_account():
    db = mongomock.MongoClient().intelliplan
    app = make_app(db)
    client = app.test_client()
    assert client.get('/set').status_code == 200
    assert db.sessions.find_one()['user_id'] == 42
    db.users.delete_one({'id': 42})
    client.get('/read')
    assert db.sessions.count_documents({'user_id': 42}) == 0
    assert client.get('/read').json.get('_user_id') is None
