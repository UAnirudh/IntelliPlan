"""Native suppressions and budget notices respect current account eligibility."""
from datetime import datetime

from flask import Flask
import mongomock
import pytest

import premium_glue
from intelliplan.email import eligibility
from intelliplan.persistence.mongo import MongoStore
from intelliplan.persistence.mongo_accounts import MongoAccounts
from intelliplan.persistence.mongo_email import MongoEmailState


@pytest.fixture
def environment():
    app = Flask(__name__)
    app.config['APP_BASE_URL'] = 'https://example.invalid'
    client = mongomock.MongoClient()
    store = MongoStore('mongodb://test', 'email', client_factory=lambda *a, **k: client)
    store.transaction = lambda work: work(store.database, None)
    store.database.users.insert_one({'id': 1, 'email': 'student@example.invalid', 'birth_year': 1990})
    app.extensions['mongo_store'] = store
    repo = MongoEmailState(store)
    repo.ensure_indexes()
    return app, store, repo


def test_normalized_suppression_is_idempotent_and_survives_account_deletion(environment):
    app, store, repo = environment
    with app.app_context():
        assert eligibility.suppress(' STUDENT@EXAMPLE.INVALID ', 'unsubscribe')
        assert eligibility.suppress('student@example.invalid', 'bounce')
        assert eligibility.is_suppressed('Student@Example.Invalid')
        assert store.database.email_suppressions.count_documents({}) == 1
        assert store.database.email_suppressions.find_one({})['reason'] == 'unsubscribe'
        store.database.users.delete_one({'id': 1})
        assert eligibility.is_suppressed('student@example.invalid')


def test_native_suppression_read_failure_fails_closed(environment, monkeypatch):
    app, store, repo = environment
    def fail(*args, **kwargs):
        raise RuntimeError('Synthetic storage failure')
    monkeypatch.setattr(MongoEmailState, 'is_suppressed', fail)
    monkeypatch.setattr(MongoEmailState, 'suppress', fail)
    with app.app_context():
        assert eligibility.is_suppressed('student@example.invalid')
        assert not eligibility.is_transactional_eligible(MongoAccounts(store).get(1))[0]
        assert not eligibility.suppress('student@example.invalid')


STATE = {'low': True, 'period': '2026-10', 'remaining_usd': 1.0, 'budget_usd': 20.0}


def test_budget_notice_claim_is_single_use_and_requires_sender(environment):
    app, store, repo = environment
    user = MongoAccounts(store).get(1)
    sent = []
    with app.app_context():
        premium_glue._maybe_warn_low_budget(user, STATE)
        assert MongoAccounts(store).get(1).ai_low_budget_notified is None
        app.extensions['email_sender'] = lambda *args: sent.append(args) or True
        premium_glue._maybe_warn_low_budget(user, STATE)
        premium_glue._maybe_warn_low_budget(user, STATE)
    assert len(sent) == 1
    assert sent[0][0] == 'student@example.invalid'
    assert 'https://example.invalid/settings/ai' in sent[0][2]
    assert repo.claim_budget_notice(1, '2026-09') is None
    assert repo.claim_budget_notice(1, '2026-11') is not None
    assert repo.claim_budget_notice(1, '2026-10') is None
    store.database.users.delete_one({'id': 1})
    assert repo.claim_budget_notice(1, '2026-11') is None


@pytest.mark.parametrize('change', ['suppressed', 'child', 'deleted'])
def test_budget_notice_rechecks_current_account_eligibility(environment, change):
    app, store, repo = environment
    stale = MongoAccounts(store).get(1)
    sent = []
    app.extensions['email_sender'] = lambda *args: sent.append(args) or True
    if change == 'suppressed':
        repo.suppress(stale.email)
    elif change == 'child':
        store.database.users.update_one({'id': 1}, {'$set': {'birth_year': datetime.utcnow().year - 8}})
    else:
        store.database.users.delete_one({'id': 1})
    with app.app_context():
        premium_glue._maybe_warn_low_budget(stale, STATE)
    assert sent == []
