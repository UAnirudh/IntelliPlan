"""Native onboarding reads real progress and supports restored timestamp formats."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask
import mongomock
import pytest

from intelliplan.email import onboarding
from intelliplan.persistence.mongo import MongoStore
from intelliplan.persistence.mongo_email import MongoEmailState


NOW = datetime(2026, 10, 18, 12)


@pytest.fixture
def environment():
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / 'Main_Project' / 'templates'))
    app.config.update(SECRET_KEY='synthetic-onboarding', APP_BASE_URL='https://example.invalid')
    client = mongomock.MongoClient()
    store = MongoStore('mongodb://test', 'onboarding', client_factory=lambda *a, **k: client)
    store.transaction = lambda work: work(store.database, None)
    repo = MongoEmailState(store)
    repo.ensure_indexes()
    app.extensions['mongo_store'] = store
    calls = []
    app.extensions['email_sender'] = lambda *args, **kwargs: calls.append(args) or 'synthetic-provider-id'
    return app, store, repo, calls


def add_user(store, identity, age, created=None):
    store.database.users.insert_one({'id': identity, 'email': f'cohort-{identity}@example.invalid',
        'birth_year': 1990, 'name': 'Sam', 'created_at': created or (NOW - timedelta(days=age)).isoformat()})


def test_native_cohort_includes_iso_offset_and_bson_dates_but_skips_outside_window(environment):
    app, store, repo, calls = environment
    add_user(store, 1, 3)
    add_user(store, 2, 3, (NOW - timedelta(days=3)).replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=-7))).isoformat())
    add_user(store, 3, 3, NOW - timedelta(days=3))
    add_user(store, 4, 0)
    add_user(store, 5, 20)
    with app.app_context():
        result = onboarding.sweep_onboarding(now=NOW.replace(tzinfo=timezone.utc))
        assert result['sent'] == 3
        assert result['by_step']['onboarding_connect_v1']['sent'] == 3
        assert onboarding.sweep_onboarding(now=NOW)['sent'] == 0
    assert len(calls) == 3
    assert 'sqlalchemy' not in app.extensions


def test_native_progress_skips_completed_goals_and_is_owner_scoped(environment):
    app, store, repo, calls = environment
    add_user(store, 1, 5)
    add_user(store, 2, 5)
    store.database.linked_accounts.insert_one({'id': 1, 'user_id': 1})
    store.database.saved_schedules.insert_one({'id': 1, 'user_id': 1})
    store.database.active_sessions.insert_one({'id': 1, 'user_id': 1, 'state': 'abandoned'})
    with app.app_context():
        assert onboarding.onboarding_progress(1) == {'connected': True, 'planned': True, 'studied': False}
        assert onboarding.onboarding_progress(2) == {'connected': False, 'planned': False, 'studied': False}
        result = onboarding.sweep_onboarding(now=NOW)
    assert result['sent'] == 1
    assert calls[0][0] == 'cohort-2@example.invalid'
    store.database.active_sessions.update_one({'id': 1}, {'$set': {'state': 'completed'}})
    with app.app_context():
        assert onboarding.onboarding_progress(1)['studied'] is True


def test_native_failed_send_retries_within_window_and_limit_is_respected(environment):
    app, store, repo, calls = environment
    add_user(store, 1, 3)
    add_user(store, 2, 3)
    app.extensions['email_sender'] = lambda *args, **kwargs: False
    with app.app_context():
        assert onboarding.sweep_onboarding(now=NOW, limit=1)['failed'] == 1
        app.extensions['email_sender'] = lambda *args, **kwargs: calls.append(args) or 'synthetic-retry-id'
        result = onboarding.sweep_onboarding(now=NOW, limit=1)
    assert result['sent'] == 1 and result['truncated'] is True
    assert len(calls) == 1
    assert store.database.email_sends.count_documents({}) == 1
