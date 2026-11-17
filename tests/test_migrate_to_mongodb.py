from datetime import datetime
from decimal import Decimal
import hashlib
import json

import pytest

from bson import Binary, Decimal128
from scripts import migrate_to_mongodb as migration

mongomock = pytest.importorskip("mongomock")

def make_snapshot(path):
    path.mkdir()
    rows = [
        {"_id": "a", "id": 1, "password_hash": "hash-not-plaintext",
         "session": Binary(b"\x00\xff"), "amount": Decimal128("12.30"),
         "created_at": "2026-10-17T12:30:00.123456", "extra": None},
        {"_id": "b", "id": 2, "extra": {"nested": [1, None, True]}},
    ]
    content = "".join(migration.canonical(row) + "\n" for row in rows)
    filename = migration.safe_file("users")
    (path / filename).write_text(content, encoding="utf-8", newline="\n")
    (path / "manifest.json").write_text(json.dumps({
        "format": 1, "status": "complete", "created_at": "now", "tables": [
            {"name": "users", "file": filename, "rows": 2,
             "sha256": hashlib.sha256(content.encode()).hexdigest()}]}), encoding="utf-8")
    return rows


def test_values_keep_precision_and_binary():
    timestamp = datetime(2026, 10, 17, 12, 30, 0, 123456)
    assert migration.mongo_value(timestamp) == "2026-10-17T12:30:00.123456"
    assert migration.mongo_value(memoryview(b"\x00\xff")) == Binary(b"\x00\xff")
    assert migration.mongo_value(Decimal("12.30")) == Decimal128("12.30")
    with pytest.raises(TypeError):
        migration.mongo_value(object())


def test_json_text_is_not_reinterpreted_or_rounded():
    value = '{"n":0.1234567890123456789012345,"$numberLong":"42","$date":"literal","$binary":"literal","$oid":"literal"}'
    from bson import json_util
    restored = json_util.loads(migration.canonical({"payload": value}))
    assert restored["payload"] == value


def test_stable_primary_keys_and_distinct_keyless_rows():
    assert migration.document_id({"id": 7}, ["id"], 0) == migration.document_id({"id": 7}, ["id"], 8)
    assert migration.document_id({}, [], 0) != migration.document_id({}, [], 1)


def test_restore_and_compare_all_document_values(tmp_path):
    path = tmp_path / "backup"
    make_snapshot(path)
    db = mongomock.MongoClient().intelliplan_migration
    assert migration.restore(db, path) == {"tables": 1, "rows": 2}
    assert db["_migration_status"].find_one()["application_ready"] is False
    db.users.update_one({"id": 1}, {"$set": {"password_hash": "changed"}})
    with pytest.raises(ValueError, match="content mismatch"):
        migration.verify(db, path)


def test_does_not_overwrite_existing_database(tmp_path):
    path = tmp_path / "backup"
    make_snapshot(path)
    db = mongomock.MongoClient().intelliplan_migration
    db.users.insert_one({"id": "existing"})
    with pytest.raises(ValueError, match="empty"):
        migration.restore(db, path)
    assert db.users.find_one()["id"] == "existing"


def test_backup_tampering_is_detected_before_writes(tmp_path):
    path = tmp_path / "backup"
    make_snapshot(path)
    (path / migration.safe_file("users")).write_text("{}\n", encoding="utf-8")
    db = mongomock.MongoClient().intelliplan_migration
    with pytest.raises(ValueError, match="integrity"):
        migration.restore(db, path)
    assert db.list_collection_names() == []


def test_extra_collection_and_missing_rows_are_detected(tmp_path):
    path = tmp_path / "backup"
    make_snapshot(path)
    db = mongomock.MongoClient().intelliplan_migration
    migration.restore(db, path)
    db.users.delete_one({"id": 2})
    with pytest.raises(ValueError, match="count mismatch"):
        migration.verify(db, path)


def test_sequence_seeds_exceed_rows_and_reserved_source_ids():
    db = mongomock.MongoClient().intelliplan_migration
    db.users.insert_one({"id": 42})
    manifest = {"sequences": [{"last": 999, "start": 1}], "tables": [
        {"name": "users", "primary_key": ["id"], "columns": [{"name": "id", "udt": "int4"}]}]}
    migration.seed_sequences(db, manifest)
    from intelliplan.persistence.mongo import MongoStore
    store = MongoStore("mongodb://example", "intelliplan_migration", client_factory=lambda *a, **kw: db.client)
    assert store.next_id("users") == 1000
