"""Snapshot PostgreSQL and restore into a NEW MongoDB database.

This is a data migration, not an application cutover. The source transaction
is repeatable-read and read-only. No command drops or replaces a database.
Credentials are read from environment variables or a private env file and
are never included in the snapshot or progress output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg2
from psycopg2 import sql
from bson import BSON, Binary, Decimal128, json_util
from bson.json_util import CANONICAL_JSON_OPTIONS
from pymongo import MongoClient

try:
    from intelliplan.persistence.codec import ENCODING_KEY, pack_document, unpack_document
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from intelliplan.persistence.codec import ENCODING_KEY, pack_document, unpack_document

FORMAT_VERSION = 1


def canonical(value: Any) -> str:
    return json_util.dumps(value, json_options=CANONICAL_JSON_OPTIONS,
                           sort_keys=True, separators=(",", ":"))


def mongo_value(value: Any) -> Any:
    """Keep microseconds (BSON dates have only millisecond precision)."""
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (memoryview, bytes, bytearray)):
        return Binary(bytes(value))
    if isinstance(value, Decimal):
        return Decimal128(value)  # Refuses lossy conversion.
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {k: mongo_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mongo_value(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported PostgreSQL value type: {type(value).__name__}")


def document_id(row: dict, primary_key: list[str], ordinal: int) -> str:
    key = [row[name] for name in primary_key] if primary_key else [ordinal]
    return hashlib.sha256(canonical(key).encode()).hexdigest()


def safe_file(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest() + ".jsonl"


def read_env(path: Path | None) -> dict[str, str]:
    values = {}
    if path:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    return {**values, **os.environ}


def postgres_backup(source_url: str, destination: Path, pg_dump: str) -> None:
    """Retain a native backup alongside the portable MongoDB snapshot."""
    from psycopg2.extensions import parse_dsn

    if destination.exists():
        raise ValueError("Backup destination already exists")
    parameters = parse_dsn(source_url)
    environment = os.environ.copy()
    mapping = {"host": "PGHOST", "port": "PGPORT", "dbname": "PGDATABASE",
               "user": "PGUSER", "password": "PGPASSWORD", "sslmode": "PGSSLMODE",
               "options": "PGOPTIONS"}
    for key, value in parameters.items():
        if key not in mapping:
            raise ValueError("Unsupported DSN option for native backup")
        environment[mapping[key]] = value
    environment["PGCONNECT_TIMEOUT"] = "15"
    process = subprocess.run(
        [pg_dump, "--no-password", "--lock-wait-timeout=30000", "--format=custom", "--no-owner", "--no-privileges",
         "--no-subscriptions", "--quote-all-identifiers", "--file", str(destination)],
        env=environment, capture_output=True, timeout=600, check=False)
    if process.returncode:
        raise RuntimeError("Native PostgreSQL backup failed")


def snapshot(source_url: str, destination: Path, schema: str = "public") -> dict:
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    manifest = {"format": FORMAT_VERSION, "schema": schema,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "incomplete", "tables": []}
    with psycopg2.connect(source_url, connect_timeout=15) as connection:
        connection.set_session(isolation_level="REPEATABLE READ", readonly=True)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '120s'")
            cursor.execute("SET LOCAL row_security = off")
            cursor.execute("SET LOCAL TIME ZONE 'UTC'")
            cursor.execute("""SELECT sequencename, start_value, min_value, max_value,
                increment_by, cycle, cache_size, last_value FROM pg_sequences
                WHERE schemaname=%s ORDER BY sequencename""", (schema,))
            sequence_keys = ("name", "start", "min", "max", "increment", "cycle", "cache", "last")
            manifest["sequences"] = [dict(zip(sequence_keys, r)) for r in cursor.fetchall()]
            cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename", (schema,))
            tables = [r[0] for r in cursor.fetchall()]
            for table in tables:
                cursor.execute("""SELECT column_name, data_type, udt_name, is_nullable,
                    column_default FROM information_schema.columns
                    WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position""", (schema, table))
                columns = [dict(zip(("name", "type", "udt", "nullable", "default"), r)) for r in cursor.fetchall()]
                if any(c["udt"] in {"_json", "_jsonb"} for c in columns):
                    raise ValueError("JSON array columns require a lossless explicit mapping")
                cursor.execute("""SELECT k.column_name FROM information_schema.table_constraints t
                    JOIN information_schema.key_column_usage k
                    ON k.constraint_catalog=t.constraint_catalog AND k.constraint_schema=t.constraint_schema
                    AND k.constraint_name=t.constraint_name AND k.table_name=t.table_name
                    WHERE t.table_schema=%s AND t.table_name=%s AND t.constraint_type='PRIMARY KEY'
                    ORDER BY k.ordinal_position""", (schema, table))
                primary_key = [r[0] for r in cursor.fetchall()]
                cursor.execute("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname=%s AND tablename=%s ORDER BY indexname", (schema, table))
                indexes = [{"name": r[0], "sql": r[1]} for r in cursor.fetchall()]
                cursor.execute("""SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint
                    WHERE conrelid=%s::regclass ORDER BY conname""", (f'"{schema}"."{table}"',))
                constraints = [{"name": r[0], "definition": r[1]} for r in cursor.fetchall()]
                if any(c["name"] in {"_id", ENCODING_KEY} for c in columns):
                    raise ValueError("A source column conflicts with MongoDB _id")
                names = [c["name"] for c in columns]
                table_info = {"name": table, "file": safe_file(table), "columns": columns,
                              "primary_key": primary_key, "indexes": indexes,
                              "constraints": constraints, "rows": 0}
                digest = hashlib.sha256()
                with connection.cursor(name="migration_rows") as rows:
                    # PostgreSQL JSON is copied as its original text. Decoding it
                    # here loses high-precision numbers and lets literal BSON
                    # marker keys be reinterpreted by Extended JSON on restore.
                    expressions = [sql.SQL("{}::text").format(sql.Identifier(c["name"]))
                                   if c["udt"] in {"json", "jsonb"} else sql.Identifier(c["name"])
                                   for c in columns]
                    query = sql.SQL("SELECT {} FROM {}.{}").format(
                        sql.SQL(",").join(expressions),
                        sql.Identifier(schema), sql.Identifier(table))
                    if primary_key:
                        query += sql.SQL(" ORDER BY {} ").format(sql.SQL(",").join(map(sql.Identifier, primary_key)))
                    rows.itersize = 500
                    rows.execute(query)
                    with (destination / table_info["file"]).open("x", encoding="utf-8", newline="\n") as output:
                        for ordinal, row in enumerate(rows):
                            doc = {name: mongo_value(value) for name, value in zip(names, row)}
                            doc["_id"] = document_id(doc, primary_key, ordinal)
                            if len(BSON.encode(doc)) >= 16 * 1024 * 1024:
                                raise ValueError(f"Document exceeds MongoDB size limit in {table}")
                            encoded = canonical(doc) + "\n"
                            output.write(encoded)
                            digest.update(encoded.encode())
                            table_info["rows"] += 1
                table_info["sha256"] = digest.hexdigest()
                manifest["tables"].append(table_info)
                print(f"Snapshot {table}: {table_info['rows']} rows", flush=True)
    manifest["status"] = "complete"
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def validate_snapshot(path: Path) -> dict:
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != FORMAT_VERSION or manifest.get("status") != "complete":
        raise ValueError("Snapshot is incomplete or has an unsupported format")
    names = set()
    for table in manifest["tables"]:
        if table["name"] in names or table["file"] != safe_file(table["name"]):
            raise ValueError("Invalid or duplicate table manifest")
        names.add(table["name"])
        digest = hashlib.sha256()
        count = 0
        with (path / table["file"]).open("rb") as rows:
            for line in rows:
                digest.update(line)
                json_util.loads(line)
                count += 1
        if digest.hexdigest() != table["sha256"] or count != table["rows"]:
            raise ValueError(f"Snapshot integrity check failed for {table['name']}")
    return manifest


def documents(path: Path, table: dict):
    with (path / table["file"]).open(encoding="utf-8") as rows:
        for line in rows:
            yield json_util.loads(line)


def restore(database, path: Path) -> dict:
    manifest = validate_snapshot(path)
    if database.list_collection_names():
        raise ValueError("Target database must be empty; use a new migration database")
    database.create_collection("_migration_status")
    database["_migration_status"].insert_one({"_id": "snapshot", "status": "incomplete"})
    for table in manifest["tables"]:
        collection = database.create_collection(table["name"])
        batch = []
        for doc in documents(path, table):
            batch.append(pack_document(doc))
            if len(batch) == 100:
                collection.insert_many(batch, ordered=True)
                batch = []
        if batch:
            collection.insert_many(batch, ordered=True)
        print(f"Restored {table['name']}: {table['rows']} rows", flush=True)
    result = verify(database, path)
    seed_sequences(database, manifest)
    database["_migration_status"].update_one({"_id": "snapshot"}, {"$set": {
        "status": "verified_snapshot", "application_ready": False,
        "source_snapshot_at": manifest["created_at"], "tables": result["tables"], "rows": result["rows"]}})
    return result


def seed_sequences(database, manifest: dict) -> None:
    """Allocate above every retained source sequence AND restored business ID.

    Sequence values are not MVCC data. A conservative schema-wide high-water
    mark avoids reusing allocated-but-not-yet-committed IDs from the source.
    Gaps are harmless; reusing a business ID is not.
    """
    sequence_floor = max((int(s["last"] or s["start"]) for s in manifest.get("sequences", [])), default=0)
    for table in manifest["tables"]:
        primary_key = table.get("primary_key", [])
        columns = {c["name"]: c for c in table.get("columns", [])}
        if len(primary_key) != 1 or columns[primary_key[0]]["udt"] not in {"int2", "int4", "int8"}:
            continue
        key = primary_key[0]
        highest = database[table["name"]].find_one({}, {key: 1}, sort=[(key, -1)])
        floor = max(sequence_floor, int(highest[key]) if highest else 0)
        database["_sequences"].update_one({"_id": table["name"]}, {"$max": {"value": floor}}, upsert=True)


def verify(database, path: Path) -> dict:
    manifest = validate_snapshot(path)
    total = 0
    for table in manifest["tables"]:
        collection = database[table["name"]]
        if collection.count_documents({}) != table["rows"]:
            raise ValueError(f"Row count mismatch in {table['name']}")
        batch = []
        for expected in documents(path, table):
            batch.append(expected)
            if len(batch) == 100:
                verify_batch(collection, batch, table["name"])
                batch = []
        if batch:
            verify_batch(collection, batch, table["name"])
        total += table["rows"]
    expected_names = {t["name"] for t in manifest["tables"]}
    actual_names = set(database.list_collection_names()) - {"_migration_status", "_sequences"}
    if actual_names != expected_names:
        raise ValueError("Target collection inventory differs from snapshot")
    return {"tables": len(manifest["tables"]), "rows": total}


def verify_batch(collection, expected: list[dict], table: str) -> None:
    actual = {row["_id"]: canonical(unpack_document(row)) for row in collection.find({"_id": {"$in": [r["_id"] for r in expected]}})}
    if any(actual.get(row["_id"]) != canonical(row) for row in expected):
        raise ValueError(f"Document content mismatch in {table}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("backup", "snapshot", "validate", "restore", "verify"))
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--source-key", default="SOURCE_DATABASE_URL")
    parser.add_argument("--schema", default="public")
    parser.add_argument("--database", default="intelliplan_migration")
    parser.add_argument("--pg-dump", default="pg_dump")
    args = parser.parse_args()
    values = read_env(args.env_file)
    if args.command == "backup":
        postgres_backup(values[args.source_key], args.snapshot, args.pg_dump)
        print("Native PostgreSQL backup complete")
    elif args.command == "snapshot":
        result = snapshot(values[args.source_key], args.snapshot, args.schema)
        print(f"Snapshot complete: {len(result['tables'])} tables")
    elif args.command == "validate":
        result = validate_snapshot(args.snapshot)
        print(f"Integrity verified: {len(result['tables'])} tables")
    else:
        if args.database in {"admin", "config", "local"} or not args.database.startswith("intelliplan_"):
            raise ValueError("Use a dedicated intelliplan_ migration database")
        with MongoClient(values["MONGODB_URI"], serverSelectionTimeoutMS=15000) as client:
            db = client[args.database]
            result = restore(db, args.snapshot) if args.command == "restore" else verify(db, args.snapshot)
            print(json.dumps(result))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # Driver errors can include connection details, keys, and document values.
        print(f"Migration stopped ({type(error).__name__}); source was not modified.", file=sys.stderr)
        raise SystemExit(1)
