"""Boot-time database plumbing: the connection URL and the schema lock.

Two production outages (#42, then #45) came from here:

1. Driver. SQLAlchemy 2.1 changed the driver a bare ``postgresql://`` URL
   loads from psycopg2 to psycopg (v3). We ship psycopg2-binary only, so the
   first image built after 2.1.0 was released (2026-09-24) crashed every
   gunicorn worker at import with ``No module named 'psycopg'``. CI runs on
   SQLite and never loaded a Postgres driver. :func:`resolve` names the
   driver so the URL means the same thing on every SQLAlchemy version.

2. Schema race. Each gunicorn worker imports App.py and runs create_all() plus
   the migrations at the same moment. On a deploy that adds a table, two
   workers both see it missing, both CREATE it, and the loser dies on
   ``pg_type_typname_nsp_index``. Gunicorn treats a worker that fails to boot
   as fatal and stops the whole server. :func:`schema_lock` makes the workers
   take turns.

:func:`engine_options` sizes the connection pool for a hosted Postgres
(Supabase). Its session pooler admits about 15 clients in total, and
SQLAlchemy's default is up to 15 per gunicorn worker; it also closes idle
connections, which the pool must notice before a request trips over one.

Use a session-mode connection (port 5432, direct or pooler). The transaction
pooler (port 6543) hands each statement to a different backend, so the
advisory lock in :func:`schema_lock` would be taken on one and never released.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import text

DEFAULT_URL = "sqlite:///intelliplan.db"
PG_DRIVER = "postgresql+psycopg2"
# Arbitrary, fixed: any process holding this key is running schema setup.
SCHEMA_LOCK_KEY = 0x1D7E_B007

# Per gunicorn worker. Sync workers serve one request at a time; the overflow
# slot is for a background thread. 4 workers x 3 = 12 connections.
DEFAULT_POOL_SIZE = 2
DEFAULT_MAX_OVERFLOW = 1
POOL_RECYCLE_SECONDS = 300
CONNECT_TIMEOUT_SECONDS = 10


def resolve(url: str | None) -> str:
    """DATABASE_URL -> SQLAlchemy URL. ``postgres://`` is accepted too."""
    url = (url or "").strip() or DEFAULT_URL
    for bare in ("postgres://", "postgresql://"):
        if url.startswith(bare):
            return PG_DRIVER + "://" + url[len(bare):]
    return url


def url_from_env() -> str:
    return resolve(os.getenv("DATABASE_URL"))


def _env_count(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, ""))
    except ValueError:
        return default
    return value if value >= 0 else default


def engine_options(url: str) -> dict[str, Any]:
    """SQLALCHEMY_ENGINE_OPTIONS for ``url``. SQLite keeps the defaults."""
    if not url.startswith("postgresql"):
        return {}
    return {
        "pool_pre_ping": True,
        "pool_recycle": POOL_RECYCLE_SECONDS,
        "pool_size": _env_count("DB_POOL_SIZE", DEFAULT_POOL_SIZE) or DEFAULT_POOL_SIZE,
        "max_overflow": _env_count("DB_MAX_OVERFLOW", DEFAULT_MAX_OVERFLOW),
        "connect_args": {
            "connect_timeout": CONNECT_TIMEOUT_SECONDS,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 3,
        },
    }


@contextmanager
def schema_lock(engine: Any) -> Iterator[None]:
    """Serialize boot-time schema work across processes (Postgres only).

    A session-level advisory lock on its own connection, so the migrations
    inside may commit freely. Released when the connection closes, including
    if the process dies. Other databases run unlocked: SQLite is dev/CI with
    one process.
    """
    if engine.dialect.name != "postgresql":
        yield
        return
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": SCHEMA_LOCK_KEY})
        conn.commit()
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": SCHEMA_LOCK_KEY})
            conn.commit()
