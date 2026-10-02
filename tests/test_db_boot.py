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


def test_off_postgres_the_caller_always_does_the_schema_work():
    engine = sqlalchemy.create_engine("sqlite://")
    with db_boot.schema_lock(engine) as ours:
        assert ours is True


def test_no_marker_without_a_deployment_id(monkeypatch):
    # Local runs must re-check the schema on every boot.
    monkeypatch.delenv("RAILWAY_DEPLOYMENT_ID", raising=False)
    assert db_boot._schema_marker() is None


def test_marker_is_per_deployment(monkeypatch):
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "deploy-a")
    first = db_boot._schema_marker()
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "deploy-b")
    assert first and db_boot._schema_marker() != first


class _FakePostgres:
    """Just enough engine for schema_lock: the lock is busy ``busy`` times."""

    class dialect:
        name = "postgresql"

    def __init__(self, busy=0):
        self.busy, self.statements = busy, []

    def connect(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def commit(self):
        pass

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        engine = self

        class _Result:
            def scalar(self):
                if "pg_try_advisory_lock" not in sql:
                    return True
                engine.busy -= 1
                return engine.busy < 0

        return _Result()


def test_lock_is_polled_never_waited_on(monkeypatch):
    # A blocking pg_advisory_lock ran into Supabase's two minute statement
    # timeout and killed gunicorn in a loop.
    monkeypatch.delenv("RAILWAY_DEPLOYMENT_ID", raising=False)
    monkeypatch.setattr(db_boot.time, "sleep", lambda _s: None)
    engine = _FakePostgres(busy=3)
    with db_boot.schema_lock(engine) as ours:
        assert ours is True
    assert sum("pg_try_advisory_lock" in s for s in engine.statements) == 4
    assert not any("pg_advisory_lock(" in s for s in engine.statements)
    assert "pg_advisory_unlock" in engine.statements[-1]


def test_second_worker_in_a_deployment_skips_the_schema_work(monkeypatch, tmp_path):
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "deploy-x")
    monkeypatch.setattr(db_boot.tempfile, "gettempdir", lambda: str(tmp_path))
    with db_boot.schema_lock(_FakePostgres()) as first:
        assert first is True
    with db_boot.schema_lock(_FakePostgres()) as second:
        assert second is False


def test_a_failed_schema_pass_is_not_marked_done(monkeypatch, tmp_path):
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "deploy-y")
    monkeypatch.setattr(db_boot.tempfile, "gettempdir", lambda: str(tmp_path))
    engine = _FakePostgres()
    with pytest.raises(RuntimeError):
        with db_boot.schema_lock(engine):
            raise RuntimeError("migration failed")
    assert "pg_advisory_unlock" in engine.statements[-1]
    with db_boot.schema_lock(_FakePostgres()) as retry:
        assert retry is True


def test_steps_are_skipped_independently(monkeypatch, tmp_path):
    # The second import-time pass must still run once after the first step
    # has marked itself done.
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "deploy-z")
    monkeypatch.setattr(db_boot.tempfile, "gettempdir", lambda: str(tmp_path))
    with db_boot.schema_lock(_FakePostgres()) as schema:
        assert schema is True
    with db_boot.schema_lock(_FakePostgres(), step="columns") as columns:
        assert columns is True
    with db_boot.schema_lock(_FakePostgres(), step="columns") as again:
        assert again is False
