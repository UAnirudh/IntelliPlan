"""Copy the production Postgres database to another host and prove it matches.

Written for the Railway -> Supabase move. Connection strings are read from an
env file kept outside the repository and are never printed:

    SOURCE_DATABASE_URL=postgresql://...   (Railway: DATABASE_PUBLIC_URL)
    TARGET_DATABASE_URL=postgresql://...   (Supabase: direct or session pooler, port 5432)

Commands, in the order a cutover uses them:

    check      connect to both, report versions, size and what the target holds
    copy       pg_dump the source, restore into the target in one transaction
    sync       replace the target's rows with the source's, table by table
    verify     compare every table's row count and content hash, and sequences
    lockdown   close Supabase's public Data API over the copied tables

The source is only ever read. ``copy`` refuses a target that already has
tables unless ``--replace-target`` is passed.

``sync`` is for a target that already has the schema, and for a source newer
than the local pg_dump (which refuses to dump a newer server). It needs
``--replace-target``, keeps a file per table as a backup of both sides, and
swaps the rows in one transaction.
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import psycopg2
from psycopg2 import sql

DEFAULT_ENV_FILE = Path.home() / ".intelliplan-migration.env"
DEFAULT_DUMP_DIR = Path.home() / "intelliplan-db-backups"
SCHEMA = "public"
# pg_dump 17 emits settings older servers reject; they are session tuning, not data.
UNPORTABLE_SETTINGS = ("SET transaction_timeout",)
# Supabase's API roles. Tables in ``public`` are readable through the Data API
# with the publishable key unless these are shut out.
API_ROLES = ("anon", "authenticated")


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def libpq_url(url: str) -> str:
    """Strip a SQLAlchemy driver suffix so libpq tools accept the URL."""
    scheme, sep, rest = url.partition("://")
    scheme = scheme.split("+")[0]
    return ("postgresql" if scheme == "postgres" else scheme) + sep + rest


def describe(url: str) -> str:
    """Host, port and database of ``url``. Never the credentials."""
    parts = urlsplit(url)
    return f"{parts.hostname}:{parts.port or 5432}{parts.path}"


def portable_sql(lines):
    """Drop settings the target server may not know."""
    for line in lines:
        if not line.startswith(UNPORTABLE_SETTINGS):
            yield line


def find_tool(name: str) -> str:
    override = os.getenv("PG_BIN")
    if override:
        return str(Path(override) / name)
    found = shutil.which(name)
    if found:
        return found
    installs = sorted(
        glob.glob(rf"C:\Program Files\PostgreSQL\*\bin\{name}.exe"),
        key=lambda p: int(Path(p).parts[-3]) if Path(p).parts[-3].isdigit() else 0,
    )
    if not installs:
        raise SystemExit(f"{name} not found. Install PostgreSQL client tools or set PG_BIN.")
    return installs[-1]


def run_tool(name: str, args: list[str], url: str) -> None:
    """Run a libpq tool with the password in the environment, not the argv."""
    parts = urlsplit(url)
    env = dict(os.environ, PGPASSWORD=parts.password or "", PGCONNECT_TIMEOUT="15")
    env.setdefault("PGSSLMODE", "require")
    target = [
        "--host", parts.hostname or "", "--port", str(parts.port or 5432),
        "--username", parts.username or "", "--dbname", parts.path.lstrip("/"),
    ]
    result = subprocess.run([find_tool(name), *target, *args], env=env, text=True,
                            capture_output=True)
    if result.returncode != 0:
        raise SystemExit(f"{name} failed:\n{result.stderr.strip()[-4000:]}")


def connect(url: str):
    conn = psycopg2.connect(url, connect_timeout=15)
    conn.set_session(readonly=True, autocommit=True)
    # Row text is hashed in verify; both servers must render it the same way.
    with conn.cursor() as cur:
        cur.execute("SET TimeZone = 'UTC'; SET DateStyle = 'ISO, MDY'; "
                    "SET IntervalStyle = 'postgres'; SET extra_float_digits = 1")
    return conn


def tables(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY 1", (SCHEMA,)
        )
        return [row[0] for row in cur.fetchall()]


def table_fingerprint(conn, table: str) -> tuple[int, str]:
    """Row count and an order-independent hash of every row's text form."""
    query = sql.SQL(
        "SELECT count(*), coalesce(md5(string_agg(h, '' ORDER BY h)), '') "
        "FROM (SELECT md5(t::text) AS h FROM {}.{} t) rows"
    ).format(sql.Identifier(SCHEMA), sql.Identifier(table))
    with conn.cursor() as cur:
        cur.execute(query)
        count, digest = cur.fetchone()
        return count, digest


def sequences(conn) -> dict[str, int | None]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT sequencename, last_value FROM pg_sequences WHERE schemaname = %s",
            (SCHEMA,),
        )
        return dict(cur.fetchall())


def server_summary(conn) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT current_setting('server_version'), "
                    "pg_size_pretty(pg_database_size(current_database()))")
        version, size = cur.fetchone()
    return f"PostgreSQL {version}, {size}"


def cmd_check(source: str, target: str, _args) -> int:
    for label, url in (("source", source), ("target", target)):
        conn = connect(url)
        names = tables(conn)
        rows = sum(table_fingerprint(conn, name)[0] for name in names) if label == "target" else None
        print(f"{label}: {describe(url)} | {server_summary(conn)} | {len(names)} tables"
              + (f", {rows} rows" if rows is not None else ""))
        with conn.cursor() as cur:
            cur.execute("SELECT extname FROM pg_extension WHERE extname <> 'plpgsql' ORDER BY 1")
            print(f"  extensions: {', '.join(r[0] for r in cur.fetchall()) or 'none'}")
        conn.close()
    return 0


def cmd_copy(source: str, target: str, args) -> int:
    conn = connect(target)
    existing = tables(conn)
    conn.close()
    if existing and not args.replace_target:
        print(f"Target already has {len(existing)} tables in {SCHEMA}. "
              "Re-run with --replace-target to drop and replace them.", file=sys.stderr)
        return 1

    dump_dir = Path(args.dump_dir)
    dump_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dump_file = dump_dir / f"intelliplan-{stamp}.dump"
    sql_file = dump_dir / f"intelliplan-{stamp}.sql"

    print(f"Dumping {describe(source)} ...")
    run_tool("pg_dump", ["--format=custom", "--no-owner", "--no-privileges",
                         f"--schema={SCHEMA}", f"--file={dump_file}"], source)
    print(f"  backup kept at {dump_file} ({dump_file.stat().st_size / 1e6:.1f} MB)")

    # To a script first, so settings the target rejects can be removed and the
    # whole restore can run as one transaction: it lands completely or not at all.
    script = [find_tool("pg_restore"), "--no-owner", "--no-privileges", "--file=-"]
    if args.replace_target:
        script += ["--clean", "--if-exists"]
    raw = subprocess.run([*script, str(dump_file)], capture_output=True, check=True)
    lines = raw.stdout.decode("utf-8").splitlines(keepends=True)
    # The public schema already exists on the target and belongs to it.
    lines = [l for l in lines if not l.startswith(("CREATE SCHEMA public", "DROP SCHEMA"))]
    sql_file.write_text("".join(portable_sql(lines)), encoding="utf-8", newline="")

    print(f"Restoring into {describe(target)} ...")
    try:
        run_tool("psql", ["--no-psqlrc", "--quiet", "--single-transaction",
                          "--set=ON_ERROR_STOP=1", f"--file={sql_file}"], target)
    finally:
        sql_file.unlink(missing_ok=True)
    print("Restore committed. Run verify next.")
    return 0


def columns(conn, table: str) -> list[tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT a.attname, format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
            "WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped "
            "AND a.attgenerated = '' ORDER BY a.attnum",
            (f'{SCHEMA}."{table}"',),
        )
        return cur.fetchall()


def export_tables(conn, names: list[str], folder: Path) -> dict[str, list[str]]:
    """COPY each table to ``folder``; returns the column order used per table."""
    folder.mkdir(parents=True, exist_ok=True)
    layout: dict[str, list[str]] = {}
    for table in names:
        layout[table] = [name for name, _ in columns(conn, table)]
        statement = sql.SQL("COPY {}.{} ({}) TO STDOUT").format(
            sql.Identifier(SCHEMA), sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, layout[table])))
        with conn.cursor() as cur, open(folder / f"{table}.copy", "wb") as out:
            cur.copy_expert(statement, out)
    return layout


def cmd_sync(source: str, target: str, args) -> int:
    if not args.replace_target:
        print("sync replaces every row on the target. Re-run with --replace-target.",
              file=sys.stderr)
        return 1
    src, dst = connect(source), connect(target)
    names = tables(src)
    drift = [f"{t}: missing on target" for t in names if t not in tables(dst)]
    drift += [f"{t}: columns differ" for t in names
              if not drift and columns(src, t) != columns(dst, t)]
    if drift:
        print("Schemas differ; nothing was changed:", file=sys.stderr)
        for line in drift:
            print(f"  {line}", file=sys.stderr)
        return 1

    backup = Path(args.dump_dir) / time.strftime("sync-%Y%m%d-%H%M%S")
    # One snapshot of the source, so every table is from the same instant.
    src.set_session(readonly=True, isolation_level="REPEATABLE READ", autocommit=False)
    layout = export_tables(src, names, backup / "source")
    src_sequences = sequences(src)
    src.rollback()
    export_tables(dst, names, backup / "target-before")
    src.close()
    dst.close()
    print(f"Backups of both sides kept in {backup}")

    conn = psycopg2.connect(target, connect_timeout=15)
    with conn, conn.cursor() as cur:  # one transaction: all tables or none
        # Skip foreign key triggers while loading, like pg_restore does.
        cur.execute("SET LOCAL session_replication_role = replica")
        cur.execute(sql.SQL("TRUNCATE {}").format(sql.SQL(", ").join(
            sql.SQL("{}.{}").format(sql.Identifier(SCHEMA), sql.Identifier(t)) for t in names)))
        for table in names:
            statement = sql.SQL("COPY {}.{} ({}) FROM STDIN").format(
                sql.Identifier(SCHEMA), sql.Identifier(table),
                sql.SQL(", ").join(map(sql.Identifier, layout[table])))
            with open(backup / "source" / f"{table}.copy", "rb") as data:
                cur.copy_expert(statement, data)
        for name, value in src_sequences.items():
            if value is not None:
                cur.execute("SELECT setval(%s::regclass, %s, true)",
                            (f'{SCHEMA}."{name}"', value))
    conn.close()
    print(f"Replaced the rows of {len(names)} tables. Run verify next.")
    return 0


def cmd_verify(source: str, target: str, _args) -> int:
    src, dst = connect(source), connect(target)
    src_tables, dst_tables = tables(src), tables(dst)
    problems = [f"missing on target: {t}" for t in sorted(set(src_tables) - set(dst_tables))]
    total = 0
    for table in src_tables:
        if table not in dst_tables:
            continue
        before, after = table_fingerprint(src, table), table_fingerprint(dst, table)
        total += before[0]
        if before[0] != after[0]:
            problems.append(f"{table}: {before[0]} rows on source, {after[0]} on target")
        elif before[1] != after[1]:
            problems.append(f"{table}: same row count ({before[0]}) but content differs")
    src_seq, dst_seq = sequences(src), sequences(dst)
    for name, value in src_seq.items():
        if (dst_seq.get(name) or 0) < (value or 0):
            problems.append(f"sequence {name}: {value} on source, {dst_seq.get(name)} on target")
    src.close()
    dst.close()

    print(f"Compared {len(src_tables)} tables, {total} rows, {len(src_seq)} sequences.")
    if problems:
        print("MISMATCH:", *problems, sep="\n  ", file=sys.stderr)
        return 1
    print("MATCH: every table has identical rows and every sequence is caught up.")
    return 0


def cmd_lockdown(_source: str, target: str, _args) -> int:
    conn = psycopg2.connect(target, connect_timeout=15)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)", (list(API_ROLES),))
        roles = [row[0] for row in cur.fetchall()]
        names = tables(conn)
        for table in names:
            cur.execute(sql.SQL("ALTER TABLE {}.{} ENABLE ROW LEVEL SECURITY").format(
                sql.Identifier(SCHEMA), sql.Identifier(table)))
        for role in roles:
            who, schema = sql.Identifier(role), sql.Identifier(SCHEMA)
            for kind in ("TABLES", "SEQUENCES", "FUNCTIONS"):
                cur.execute(sql.SQL("REVOKE ALL ON ALL {} IN SCHEMA {} FROM {}").format(
                    sql.SQL(kind), schema, who))
                # Tables the app creates on a later deploy stay closed too.
                cur.execute(sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA {} REVOKE ALL ON {} FROM {}"
                ).format(schema, sql.SQL(kind), who))
    conn.close()
    print(f"Row level security on for {len(names)} tables; "
          f"API roles shut out: {', '.join(roles) or 'none present'}.")
    return 0


COMMANDS = {"check": cmd_check, "copy": cmd_copy, "sync": cmd_sync, "verify": cmd_verify,
            "lockdown": cmd_lockdown}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--dump-dir", default=str(DEFAULT_DUMP_DIR))
    parser.add_argument("--replace-target", action="store_true",
                        help="copy/sync: replace what the target already holds")
    args = parser.parse_args(argv)

    env_file = Path(args.env_file)
    settings = dict(load_env_file(env_file) if env_file.exists() else {}, **{
        k: v for k, v in os.environ.items() if k.endswith("_DATABASE_URL")})
    try:
        source = libpq_url(settings["SOURCE_DATABASE_URL"])
        target = libpq_url(settings["TARGET_DATABASE_URL"])
    except KeyError as missing:
        print(f"{missing.args[0]} is not set (looked in {env_file}).", file=sys.stderr)
        return 2
    if describe(source) == describe(target):
        print("Source and target are the same database.", file=sys.stderr)
        return 2
    try:
        return COMMANDS[args.command](source, target, args)
    except psycopg2.OperationalError as exc:
        # libpq messages name the host, never the password.
        print(f"Could not connect: {str(exc).strip()}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
