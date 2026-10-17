"""Real PostgreSQL integration tests. No production credentials are used.

Set MIGRATION_TEST_PG_BIN to an installed PostgreSQL bin directory. An isolated
temporary server binds only to 127.0.0.1 on an available port. The optional
MIGRATION_TEST_NATIVE_DUMP restores only the public schema into that scratch
server; never point the connection at an existing database.
"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess

import psycopg2
import pytest

from scripts import migrate_to_mongodb as migration

mongomock = pytest.importorskip("mongomock")


@pytest.fixture(scope="module")
def postgres(tmp_path_factory):
    binary_dir = os.environ.get("MIGRATION_TEST_PG_BIN")
    if not binary_dir:
        pytest.skip("MIGRATION_TEST_PG_BIN is not configured")
    bin_path = Path(binary_dir)
    root = tmp_path_factory.mktemp("mongo-migration-pg")
    data = root / "data"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    def run(name, *args):
        # On Windows the detached server can inherit pipe handles and keep
        # subprocess.communicate() waiting after pg_ctl has exited.
        with (root / "commands.log").open("ab") as output:
            result = subprocess.run([str(bin_path / name), *map(str, args)],
                                    stdout=output, stderr=output, timeout=90, creationflags=flags)
        if result.returncode:
            pytest.fail(f"Scratch PostgreSQL command failed: {name}")

    run("initdb", "-D", data, "--auth=trust", "--username=migration_test", "--encoding=UTF8", "--no-locale")
    with socket.socket() as binding:
        binding.bind(("127.0.0.1", 0))
        port = binding.getsockname()[1]
    run("pg_ctl", "-D", data, "-l", root / "server.log", "-o", f"-h 127.0.0.1 -p {port}", "-w", "start")
    try:
        yield f"host=127.0.0.1 port={port} user=migration_test dbname=postgres", root
    finally:
        run("pg_ctl", "-D", data, "-m", "fast", "-w", "stop")


def test_real_postgres_snapshot_is_lossless(postgres):
    dsn, root = postgres
    with psycopg2.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute("CREATE SCHEMA migration_fixture")
        cursor.execute("""CREATE TABLE migration_fixture.accounts (
            id SERIAL PRIMARY KEY, name text UNIQUE, data json, payload bytea,
            created_at timestamp, stamp timestamptz, birthday date, exact numeric(34,25))""")
        cursor.execute("CREATE TABLE migration_fixture.empty_table (id SERIAL PRIMARY KEY)")
        cursor.execute("CREATE TABLE migration_fixture.composite (a integer,b integer,PRIMARY KEY(a,b))")
        cursor.execute("INSERT INTO migration_fixture.composite VALUES(1,2),(1,3)")
        literal_json = '{"n":0.1234567890123456789012345,"$numberLong":"42","$date":"literal"}'
        cursor.execute("""INSERT INTO migration_fixture.accounts
            (name,data,payload,created_at,stamp,birthday,exact)
            VALUES (%s,%s,%s,'2026-10-17 12:30:00.123456',
            '2026-10-17 12:30:00.123456+00','2011-01-02',0.1234567890123456789012345)""",
                       ("test", literal_json, psycopg2.Binary(b"\x00\xff")))
        cursor.execute("SELECT setval('migration_fixture.accounts_id_seq',1000)")
    path = root / "fixture-snapshot"
    manifest = migration.snapshot(dsn, path, "migration_fixture")
    db = mongomock.MongoClient().intelliplan_fixture
    assert migration.restore(db, path) == {"tables": 3, "rows": 3}
    row = db.accounts.find_one({"name": "test"})
    assert row["data"] == literal_json
    assert row["payload"] == b"\x00\xff"
    assert row["created_at"] == "2026-10-17T12:30:00.123456"
    assert row["stamp"].endswith(".123456+00:00")
    assert str(row["exact"]) == "0.1234567890123456789012345"
    assert db["_sequences"].find_one({"_id": "accounts"})["value"] >= 1000
    assert {t["name"] for t in manifest["tables"]} == {"accounts", "empty_table", "composite"}


def test_native_backup_can_be_restored_and_reexported(postgres):
    dump = os.environ.get("MIGRATION_TEST_NATIVE_DUMP")
    if not dump:
        pytest.skip("MIGRATION_TEST_NATIVE_DUMP is not configured")
    dsn, root = postgres
    bin_path = Path(os.environ["MIGRATION_TEST_PG_BIN"])
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    # This connection is exclusively the isolated server created by the fixture.
    with psycopg2.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
        assert cursor.fetchone()[0] == 0
    result = subprocess.run([
        str(bin_path / "pg_restore"), "--no-owner", "--no-privileges", "--schema=public",
        "--single-transaction", "--exit-on-error", "--dbname", dsn, dump,
    ], capture_output=True, timeout=120, creationflags=flags)
    if result.returncode:
        # pg_restore logs contain SQL/data; keep them local to the scratch server.
        (root / "restore-errors.log").write_bytes(result.stderr)
        pytest.fail(f"Native restore failed; diagnostics: {root / 'restore-errors.log'}")
    path = root / "native-restored-snapshot"
    manifest = migration.snapshot(dsn, path)
    assert len(manifest["tables"]) == 132
    db = mongomock.MongoClient().intelliplan_native_restored
    counts = migration.restore(db, path)
    assert counts["rows"] > 0
    assert migration.verify(db, path) == counts


def test_rls_cannot_silently_hide_rows_in_a_snapshot(postgres):
    dsn, root = postgres
    with psycopg2.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute("CREATE SCHEMA rls_fixture")
        cursor.execute("CREATE TABLE rls_fixture.hidden (id integer PRIMARY KEY)")
        cursor.execute("INSERT INTO rls_fixture.hidden VALUES(1)")
        cursor.execute("ALTER TABLE rls_fixture.hidden ENABLE ROW LEVEL SECURITY")
        cursor.execute("CREATE ROLE migration_restricted LOGIN")
        cursor.execute("GRANT USAGE ON SCHEMA rls_fixture TO migration_restricted")
        cursor.execute("GRANT SELECT ON rls_fixture.hidden TO migration_restricted")
    restricted = psycopg2.extensions.make_dsn(dsn, user="migration_restricted")
    path = root / "rls-snapshot"
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        migration.snapshot(restricted, path, "rls_fixture")
    assert not (path / "manifest.json").exists()


def test_unsupported_json_array_column_fails_before_completion(postgres):
    dsn, root = postgres
    with psycopg2.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute("CREATE SCHEMA json_array_fixture")
        cursor.execute("CREATE TABLE json_array_fixture.data (id integer PRIMARY KEY, payload json[])")
    path = root / "array-snapshot"
    with pytest.raises(ValueError, match="JSON array"):
        migration.snapshot(dsn, path, "json_array_fixture")
    assert not (path / "manifest.json").exists()
