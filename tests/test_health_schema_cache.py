"""Repeated health diagnostics must not repeatedly ship the schema catalog."""
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

from intelliplan.services import health_schema


def test_repeated_diagnostics_fetch_no_catalog_rows_and_return_defensive_copies():
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE users (id INTEGER, email TEXT)'))
    statements = []
    event.listen(engine, 'before_cursor_execute', lambda *args: statements.append(args[2]))
    first = health_schema.schema_columns(engine)
    assert first['users'] == ['email', 'id']
    assert first['notion_integrations'] == []
    assert statements
    statements.clear()
    first['users'].clear()
    assert health_schema.schema_columns(engine)['users'] == ['email', 'id']
    assert health_schema.schema_columns(engine)['users'] == ['email', 'id']
    assert statements == []


def test_cache_refresh_and_engine_isolation(monkeypatch):
    engines = [create_engine('sqlite://') for _ in range(2)]
    clock = [100.0]
    monkeypatch.setattr(health_schema.time, 'monotonic', lambda: clock[0])
    inspector = Mock()
    inspector.get_columns.return_value = [{'name': 'id'}]
    lookup = Mock(return_value=inspector)
    monkeypatch.setattr(health_schema, 'inspect', lookup)
    health_schema.schema_columns(engines[0])
    health_schema.schema_columns(engines[0])
    assert lookup.call_count == 1
    health_schema.schema_columns(engines[1])
    assert lookup.call_count == 2
    clock[0] += 61
    health_schema.schema_columns(engines[0])
    assert lookup.call_count == 3


def test_failed_reflection_is_not_cached(monkeypatch):
    engine = create_engine('sqlite://')
    lookup = Mock(side_effect=RuntimeError('catalog unavailable'))
    monkeypatch.setattr(health_schema, 'inspect', lookup)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            health_schema.schema_columns(engine)
    assert lookup.call_count == 2


def test_cold_diagnostics_reuse_liveness_connection_with_single_connection_pool():
    engine = create_engine('sqlite://', poolclass=QueuePool,
                           pool_size=1, max_overflow=0, pool_timeout=.05)
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE users (id INTEGER, email TEXT)'))
    with Session(engine) as session:
        session.execute(text('SELECT 1'))
        columns = health_schema.schema_columns(engine, connection=session.connection())
        assert columns['users'] == ['email', 'id']
        assert engine.pool.checkedout() == 1


def test_real_health_route_keeps_liveness_uncached_without_repeated_catalog_reads():
    import App
    with App.app.app_context():
        App.db.create_all()
        health_schema.invalidate(App.db.engine)
        statements = []
        def capture(_connection, _cursor, statement, *_args):
            statements.append(statement)
        event.listen(App.db.engine, 'before_cursor_execute', capture)
        try:
            client = App.app.test_client()
            assert client.get('/health').status_code == 200
            statements.clear()
            for _ in range(3):
                response = client.get('/health')
                assert response.status_code == 200
                assert response.headers['Cache-Control'] == 'no-store'
                assert response.get_json()['users_schema_ok'] is True
            assert sum(sql.strip() == 'SELECT 1' for sql in statements) == 3
            assert not any('PRAGMA' in sql or 'pg_catalog' in sql for sql in statements)
        finally:
            event.remove(App.db.engine, 'before_cursor_execute', capture)
            health_schema.invalidate(App.db.engine)


def test_cached_schema_cannot_hide_a_failed_database_liveness_check(monkeypatch):
    import App
    with App.app.app_context():
        App.db.create_all()
        health_schema.schema_columns(App.db.engine)
        def unavailable(*args, **kwargs):
            raise RuntimeError('private provider diagnostic')
        with App.app.test_request_context('/health'):
            monkeypatch.setattr(App.db.session, 'execute', unavailable)
            response = App.health_check()
        assert response.status_code == 503
        assert response.get_json()['status'] == 'error'
        assert response.get_json()['users_schema_ok'] is False
        assert b'private provider diagnostic' not in response.data
