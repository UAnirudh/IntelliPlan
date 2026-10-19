"""Exercise native ledger dispatch without a SQL application."""
from datetime import datetime
from types import SimpleNamespace

from flask import Flask
from flask_login import LoginManager
import mongomock
import pytest

import premium_glue
from intelliplan.persistence.mongo import MongoStore
from intelliplan.persistence.mongo_accounts import MongoAccounts
from intelliplan.persistence.mongo_ai_ledger import MongoAILedger
from intelliplan.premium import catalog, pricing


@pytest.fixture
def environment():
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-ai-ledger')
    client = mongomock.MongoClient()
    store = MongoStore('mongodb://test', 'ledger', client_factory=lambda *a, **k: client)
    store.transaction = lambda work: work(store.database, None)
    store.database.users.insert_many([{'id': 1}, {'id': 2}])
    repo = MongoAILedger(store)
    repo.ensure_indexes()
    app.extensions['mongo_store'] = store
    login = LoginManager(app)
    login.user_loader(MongoAccounts(store).get)
    app.register_blueprint(premium_glue.premium_bp)
    return app, store, repo


def test_native_payment_retries_refunds_and_owner_isolation(environment):
    app, store, repo = environment
    now = datetime(2026, 10, 18)
    event = {'type': 'checkout.session.completed', 'data': {'object': {
        'id': 'synthetic-checkout', 'mode': 'payment', 'payment_status': 'paid',
        'metadata': {'kind': 'ai_topup'}}}}
    with app.app_context():
        assert premium_glue.apply_billing_event(SimpleNamespace(id=1), event) == 'topup'
        assert premium_glue.apply_billing_event(SimpleNamespace(id=1), event) == 'topup'
        with pytest.raises(RuntimeError):
            premium_glue.apply_billing_event(SimpleNamespace(id=2), event)
        assert premium_glue.grant_credit(1, 123, stripe_ref='synthetic-extra', now=now)
        assert not premium_glue.grant_credit(1, 123, stripe_ref='synthetic-extra', now=now)
        assert premium_glue.revoke_credit(1, 123, stripe_ref='synthetic-refund', now=now)
        assert not premium_glue.revoke_credit(1, 123, stripe_ref='synthetic-refund', now=now)
        assert premium_glue.granted_micro(1, '2026-10') == catalog.topup().credit_micro
        assert premium_glue.granted_micro(2, '2026-10') == 0
    assert store.database.ai_credit_grants.count_documents({}) == 3


def test_integer_totals_month_isolation_and_nullable_references(environment):
    app, store, repo = environment
    now = datetime(2026, 10, 18)
    for amount in (2**53 + 1, 7):
        assert repo.credit(1, amount, period='2026-10', reason='test', stripe_ref=None, now=now)
    assert repo.granted(1, '2026-10') == 2**53 + 8
    assert repo.granted(1, '2026-09') == 0
    with pytest.raises(LookupError):
        repo.credit(99, 10, period='2026-10', reason='test', stripe_ref='deleted', now=now)
    assert not repo.has_credit(99, 'deleted')


def test_priced_calls_and_native_usage_endpoint(environment):
    app, store, repo = environment
    now = datetime(2026, 10, 18)
    usage = pricing.Usage(input_tokens=1000000)
    with app.app_context():
        for source in ('platform', 'byok', 'router'):
            cost = premium_glue.record_spend(1, provider='anthropic', model='claude-sonnet-5-5',
                usage=usage, source=source, purpose='tutor', now=now)
            assert cost == (0 if source == 'byok' else 2000000)
        assert premium_glue.spent_micro(1, '2026-10') == 2000000
        assert premium_glue.spent_micro(1, '2026-10', 'byok') == 2000000
        assert premium_glue.spent_micro(2, '2026-10') == 0
    assert app.test_client().get('/api/premium/usage').status_code == 401
    client = app.test_client()
    with client.session_transaction() as cookie:
        cookie['_user_id'] = '1'
        cookie['_fresh'] = True
    payload = client.get('/api/premium/usage').get_json()
    assert payload['by_day'] == [{'day': '2026-10-18', 'cost_usd': 2.0}]
    assert {row['source'] for row in payload['by_model']} == {'platform', 'byok'}
    assert 'sqlalchemy' not in app.extensions


def test_native_credit_storage_failure_is_retryable(environment, monkeypatch):
    app, store, repo = environment
    def fail(*args, **kwargs):
        raise RuntimeError('Synthetic database failure')
    monkeypatch.setattr(store, 'transaction', fail)
    with app.app_context(), pytest.raises(RuntimeError):
        premium_glue.grant_credit(1, 100, stripe_ref='synthetic-retry')
    assert not repo.has_credit(1, 'synthetic-retry')


def test_invalid_credit_reference_is_not_silently_truncated(environment):
    app, store, repo = environment
    with app.app_context():
        with pytest.raises(ValueError):
            premium_glue.grant_credit(1, 100, stripe_ref='x' * 129)
        assert not premium_glue.grant_credit(1, True)
        assert not premium_glue.revoke_credit(1, 100, stripe_ref='')
    assert store.database.ai_credit_grants.count_documents({}) == 0
