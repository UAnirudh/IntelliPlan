"""Native lifecycle campaign selection, consent and dry-run boundaries."""
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask
import mongomock
import pytest

from intelliplan.email import campaigns, content
from intelliplan.persistence.mongo import MongoStore
from intelliplan.persistence.mongo_email import MongoEmailState


NOW = datetime(2026, 10, 18, 12)


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setenv('MARKETING_POSTAL_ADDRESS', 'Synthetic Test Address')
    monkeypatch.setattr(campaigns, 'BATCH_DELAY_SECONDS', 0)
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / 'Main_Project' / 'templates'))
    app.config.update(SECRET_KEY='synthetic-campaigns', APP_BASE_URL='https://example.invalid',
                      ADMIN_EMAILS=['admin@example.invalid'])
    client = mongomock.MongoClient()
    store = MongoStore('mongodb://test', 'campaigns', client_factory=lambda *a, **k: client)
    store.transaction = lambda work: work(store.database, None)
    repo = MongoEmailState(store)
    repo.ensure_indexes()
    app.extensions['mongo_store'] = store
    calls = []
    app.extensions['email_sender'] = lambda *args, **kwargs: calls.append(args) or 'synthetic-provider-id'
    return app, store, repo, calls


def add_user(store, identity, *, age_days=0, **values):
    store.database.users.insert_one({'id': identity, 'email': f'campaign-{identity}@example.invalid',
        'birth_year': 1990, 'created_at': (NOW - timedelta(days=age_days)).isoformat(),
        'marketing_emails_opt_in': True, 'marketing_opt_in_at': '2026-10-01T00:00:00',
        'marketing_consent_version': 'weekly_v1', **values})


def test_native_welcome_cohort_dedupes_and_retries_definite_failures(environment):
    app, store, repo, calls = environment
    add_user(store, 1)
    add_user(store, 2, age_days=4)
    app.extensions['email_sender'] = lambda *args, **kwargs: False
    with app.app_context():
        assert campaigns.sweep_welcome(now=NOW)['failed'] == 1
        app.extensions['email_sender'] = lambda *args, **kwargs: calls.append(args) or 'synthetic-retry-id'
        assert campaigns.sweep_welcome(now=NOW)['sent'] == 1
        assert campaigns.sweep_welcome(now=NOW)['sent'] == 0
    assert len(calls) == 1
    assert store.database.email_sends.count_documents({}) == 1


def test_native_feedback_requires_activity_and_current_marketing_consent(environment):
    app, store, repo, calls = environment
    age = campaigns.FEEDBACK_MIN_DAYS + .5
    add_user(store, 1, age_days=age)
    add_user(store, 2, age_days=age)
    add_user(store, 3, age_days=age, marketing_emails_opt_in=False)
    for identity in (1, 3):
        store.database.linked_accounts.insert_one({'id': identity, 'user_id': identity, 'login_type': 'canvas'})
    with app.app_context():
        result = campaigns.sweep_feedback(now=NOW)
    assert result['sent'] == 1
    assert result['skipped_inactive'] == 1
    assert result['reasons']['no_marketing_consent'] == 1
    assert calls[0][0] == 'campaign-1@example.invalid'


def test_native_newsletter_preview_obeys_consent_suppression_and_admin_targets(environment):
    app, store, repo, calls = environment
    add_user(store, 1)
    add_user(store, 2, birth_year=2020)
    add_user(store, 3, marketing_consent_version='old')
    add_user(store, 4, marketing_emails_opt_in=False)
    add_user(store, 5, email='admin@example.invalid')
    repo.suppress('campaign-1@example.invalid')
    payload = {'stats': [{'value': '0', 'label': 'Synthetic'}] * 3}
    with app.app_context():
        preview = campaigns.send_newsletter(payload, dry_run=True, now=NOW)
        assert preview['recipients'] == 1
        assert preview['audience']['excluded'] == 3
        assert preview['preview_recipients'] == ['admin@example.invalid']
        test_preview = campaigns.send_newsletter(payload, test=True, dry_run=True, now=NOW)
        assert test_preview['preview_recipients'] == ['admin@example.invalid']
        app.config['ADMIN_EMAILS'] = []
        assert campaigns.send_newsletter(payload, test=True, dry_run=True, now=NOW)['recipients'] == 0
    assert calls == []
    assert 'sqlalchemy' not in app.extensions


def test_native_thank_you_preview_uses_settled_history_and_native_stats(environment):
    app, store, repo, calls = environment
    add_user(store, 1)
    add_user(store, 2)
    claim = repo.claim_send(1, campaigns.THANK_YOU_KEY)
    repo.record_send(claim, 'sent', 'synthetic-id')
    store.database.linked_accounts.insert_many([{'id': index, 'user_id': 1, 'login_type': f'type-{index}'} for index in range(7)])
    with app.app_context():
        assert campaigns.send_thank_you(dry_run=True)['recipients_remaining'] == 1
        assert content.live_stats()[0]['value'] == '7'
    assert calls == []
