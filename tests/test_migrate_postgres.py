"""The database copy script: the parts that must hold without a live server."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "migrate_postgres", Path(__file__).resolve().parents[1] / "scripts" / "migrate_postgres.py"
)
migrate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migrate)


def test_describe_never_includes_credentials():
    shown = migrate.describe("postgresql://postgres.ref:s3cret@pooler.supabase.com:5432/postgres")
    assert shown == "pooler.supabase.com:5432/postgres"
    assert "s3cret" not in shown and "postgres.ref" not in shown


@pytest.mark.parametrize("raw", [
    "postgres://u:p@h:5432/db",
    "postgresql://u:p@h:5432/db",
    "postgresql+psycopg2://u:p@h:5432/db",
])
def test_urls_are_normalised_for_libpq(raw):
    assert migrate.libpq_url(raw) == "postgresql://u:p@h:5432/db"


def test_settings_an_older_server_rejects_are_dropped():
    script = ["SET statement_timeout = 0;\n", "SET transaction_timeout = 0;\n", "COPY t FROM stdin;\n"]
    assert list(migrate.portable_sql(script)) == [script[0], script[2]]


def test_env_file_parsing_ignores_comments_and_quotes(tmp_path):
    env = tmp_path / "migration.env"
    env.write_text('# note\nSOURCE_DATABASE_URL="postgresql://a@h/db?sslmode=require"\n\nTARGET_DATABASE_URL=postgresql://b@h2/db\n')
    assert migrate.load_env_file(env) == {
        "SOURCE_DATABASE_URL": "postgresql://a@h/db?sslmode=require",
        "TARGET_DATABASE_URL": "postgresql://b@h2/db",
    }


def test_missing_connection_strings_stop_before_any_work(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SOURCE_DATABASE_URL", raising=False)
    monkeypatch.delenv("TARGET_DATABASE_URL", raising=False)
    assert migrate.main(["check", "--env-file", str(tmp_path / "absent.env")]) == 2
    assert "SOURCE_DATABASE_URL" in capsys.readouterr().err


def test_copying_a_database_onto_itself_is_refused(monkeypatch, capsys):
    monkeypatch.setenv("SOURCE_DATABASE_URL", "postgresql://u:p@h:5432/db")
    monkeypatch.setenv("TARGET_DATABASE_URL", "postgresql://other:q@h:5432/db")
    assert migrate.main(["copy"]) == 2
    assert "same database" in capsys.readouterr().err
