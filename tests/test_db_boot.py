"""Boot-time DB plumbing: the Postgres driver we ship, and the schema lock.

Production was down after #42 and #45 because SQLAlchemy 2.1 maps a bare
``postgresql://`` URL to psycopg (v3), which is not installed. CI runs on
SQLite and never loaded a Postgres driver, so these tests do it directly.
"""

from __future__ import annotations

import pytest
import sqlalchemy

import db_boot


@pytest.mark.parametrize("raw", [
    "postgresql://u:p@host:5432/railway",
    "postgres://u:p@host:5432/railway",
])
def test_postgres_urls_name_psycopg2(raw):
    assert db_boot.resolve(raw) == "postgresql+psycopg2://u:p@host:5432/railway"


@pytest.mark.parametrize("raw", [
    "postgresql+psycopg2://u@h/db",
    "sqlite:///:memory:",
])
def test_other_urls_pass_through(raw):
    assert db_boot.resolve(raw) == raw


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_unset_falls_back_to_local_sqlite(raw):
    assert db_boot.resolve(raw) == db_boot.DEFAULT_URL


def test_the_resolved_driver_is_installed():
    # create_engine imports the DBAPI without connecting: this is the exact
    # import that crashed every gunicorn worker in production.
    engine = sqlalchemy.create_engine(db_boot.resolve("postgresql://u@h/db"))
    assert engine.dialect.driver == "psycopg2"


def test_schema_lock_is_a_no_op_off_postgres():
    engine = sqlalchemy.create_engine("sqlite://")
    with db_boot.schema_lock(engine):
        with engine.connect() as conn:
            assert conn.execute(sqlalchemy.text("SELECT 1")).scalar() == 1


def test_postgres_engine_checks_connections_and_caps_the_pool(monkeypatch):
    # A hosted pooler (Supabase) drops idle connections and caps clients far
    # below SQLAlchemy's default of 15 per gunicorn worker.
    monkeypatch.delenv("DB_POOL_SIZE", raising=False)
    monkeypatch.delenv("DB_MAX_OVERFLOW", raising=False)
    options = db_boot.engine_options("postgresql+psycopg2://u@h/db")
    assert options["pool_pre_ping"] is True
    assert options["pool_recycle"] > 0
    assert options["pool_size"] + options["max_overflow"] <= 3
    assert options["connect_args"]["connect_timeout"] > 0


def test_pool_size_is_tunable_from_the_environment(monkeypatch):
    monkeypatch.setenv("DB_POOL_SIZE", "6")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "0")
    options = db_boot.engine_options("postgresql+psycopg2://u@h/db")
    assert (options["pool_size"], options["max_overflow"]) == (6, 0)


@pytest.mark.parametrize("bad", ["", "lots", "-2"])
def test_a_bad_pool_setting_falls_back_to_the_default(monkeypatch, bad):
    monkeypatch.setenv("DB_POOL_SIZE", bad)
    options = db_boot.engine_options("postgresql+psycopg2://u@h/db")
    assert options["pool_size"] == db_boot.DEFAULT_POOL_SIZE


def test_sqlite_keeps_sqlalchemy_defaults():
    assert db_boot.engine_options("sqlite:///intelliplan.db") == {}


def test_postgres_engine_options_build_a_real_engine():
    url = db_boot.resolve("postgresql://u@h/db")
    engine = sqlalchemy.create_engine(url, **db_boot.engine_options(url))
    assert engine.pool.size() == db_boot.DEFAULT_POOL_SIZE
