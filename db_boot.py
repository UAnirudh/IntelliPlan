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


def resolve(url: str | None) -> str:
    """DATABASE_URL -> SQLAlchemy URL. ``postgres://`` is accepted too."""
    url = (url or "").strip() or DEFAULT_URL
    for bare in ("postgres://", "postgresql://"):
        if url.startswith(bare):
            return PG_DRIVER + "://" + url[len(bare):]
    return url


def url_from_env() -> str:
    return resolve(os.getenv("DATABASE_URL"))


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
