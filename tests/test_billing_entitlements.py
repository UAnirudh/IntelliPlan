"""Verified payment evidence, actual service periods, and webhook retries."""
import hashlib
import hmac
import json
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

import App
import growth_glue
import premium_glue
from intelliplan.premium.billing_events import paid_period
from time_utils import utcnow

NOW = datetime(2026, 10, 19, 12)
END = int(datetime(2026, 11, 19, 12, tzinfo=timezone.utc).timestamp())


def test_additive_watermark_migration_preserves_existing_user_and_can_repeat(monkeypatch):
    from types import SimpleNamespace
    from sqlalchemy import create_engine, inspect, text
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, '
                                'password_hash TEXT, paid_until TIMESTAMP, plan_tier TEXT)'))
        connection.execute(text("INSERT INTO users VALUES (1, 'synthetic@example.test', 'preserved-hash', "
                                "'2027-01-01', 'pro')"))
    monkeypatch.setattr(App, 'db', SimpleNamespace(engine=engine, session=App.db.session))
    try:
        App._migrate_user_columns()
        App._migrate_user_columns()
        columns = {column['name']: column for column in inspect(engine).get_columns('users')}
        assert columns['stripe_paid_until']['nullable']
        assert columns['stripe_event_created']['nullable']
        with engine.connect() as connection:
            row = connection.execute(text('SELECT email, password_hash, paid_until, plan_tier, '
                                          'stripe_paid_until, stripe_event_created FROM users')).one()
        assert tuple(row) == ('synthetic@example.test', 'preserved-hash', '2027-01-01', 'pro', None, None)
    finally:
        engine.dispose()


@pytest.mark.parametrize('missing', ['stripe_paid_until', 'stripe_event_created'])
def test_release_health_rejects_missing_payment_watermark(monkeypatch, missing):
    from types import SimpleNamespace
    from sqlalchemy import create_engine, event, inspect, text
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, password_hash TEXT)'))
        connection.execute(text("INSERT INTO users VALUES (1, 'synthetic@example.test', 'preserved-hash')"))
    def fail_one_column(connection, cursor, statement, parameters, context, many):
        if statement.startswith(f'ALTER TABLE users ADD COLUMN {missing} '):
            raise RuntimeError('Synthetic DDL failure')
    event.listen(engine, 'before_cursor_execute', fail_one_column)
    monkeypatch.setattr(App, 'db', SimpleNamespace(engine=engine, session=App.db.session))
    try:
        App._migrate_user_columns()
        assert missing not in {column['name'] for column in inspect(engine).get_columns('users')}
        with App.app.test_request_context('/health'):
            assert App.health_check().get_json()['users_schema_ok'] is False
        with engine.connect() as connection:
            assert connection.execute(text('SELECT email, password_hash FROM users')).one() == (
                'synthetic@example.test', 'preserved-hash')
    finally:
        engine.dispose()


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setenv('STRIPE_PRICE_ID', 'price_pro_test')
    monkeypatch.setenv('STRIPE_PRICE_ID_PREMIUM', 'price_premium_test')
    monkeypatch.setenv('STRIPE_PRICE_ID_PREMIUM_YEARLY', 'price_premium_year_test')
    with App.app.app_context():
        App.db.create_all()
        user = App.User(email=f'billing-{uuid4().hex}@example.test', password_hash='x', birth_year=2000)
        App.db.session.add(user)
        App.db.session.commit()
        uid = user.id
        yield uid
        App.db.session.rollback()
        App.AICreditGrant.query.filter_by(user_id=uid).delete()
        App.User.query.filter_by(id=uid).delete()
        App.db.session.commit()


def invoice(uid, modern=False):
    line = {'period': {'end': END}, 'metadata': {'user_id': str(uid), 'plan': 'premium'}}
    meta = {'metadata': {'user_id': str(uid), 'plan': 'premium'}}
    obj = {'status': 'paid', 'customer': 'cus_synthetic', 'lines': {'data': [line], 'has_more': False}}
    if modern:
        obj['parent'] = {'type': 'subscription_details', 'subscription_details': meta}
        line.update(parent={'type': 'subscription_item_details',
                            'subscription_item_details': {'subscription': 'sub_synthetic', 'proration': False}},
                    pricing={'type': 'price_details', 'price_details': {'price': 'price_premium_test'}})
    else:
        obj['subscription_details'] = meta
        line.update(type='subscription', price={'id': 'price_premium_test'}, proration=False)
    return {'id': 'evt_synthetic_invoice', 'type': 'invoice.paid', 'data': {'object': obj}}


def topup(uid, status='paid', kind='checkout.session.completed'):
    return {'id': 'evt_synthetic_topup', 'type': kind, 'data': {'object': {
        'id': 'cs_synthetic_topup', 'mode': 'payment', 'payment_status': status,
        'metadata': {'user_id': str(uid), 'kind': 'ai_topup'}}}}


@pytest.mark.parametrize('status', ['paid', 'unpaid', 'no_payment_required', None])
def test_checkout_alone_never_invents_subscription_access(owner, status):
    event = {'type': 'checkout.session.completed', 'data': {'object': {
        'client_reference_id': str(owner), 'payment_status': status,
        'metadata': {'user_id': str(owner), 'plan': 'premium'}}}}
    assert growth_glue.apply_billing_event(event, now=NOW) is False
    user = App.db.session.get(App.User, owner)
    assert user.paid_until is None
    assert user.plan_tier in (None, 'free')


@pytest.mark.parametrize('status', ['unpaid', 'no_payment_required', None])
def test_pending_or_unknown_payment_cannot_grant_topup(owner, status):
    assert not growth_glue.apply_billing_event(topup(owner, status))
    assert not App.AICreditGrant.query.filter_by(user_id=owner).count()


@pytest.mark.parametrize('modern', [False, True])
def test_paid_invoice_uses_actual_period_and_duplicate_never_adds_time(owner, modern):
    event = invoice(owner, modern)
    assert growth_glue.apply_billing_event(event, now=NOW)
    assert growth_glue.apply_billing_event(event, now=NOW + timedelta(days=10))
    user = App.db.session.get(App.User, owner)
    assert user.paid_until == datetime(2026, 11, 19, 12)
    assert user.plan_tier == 'premium'
    assert user.stripe_customer_id == 'cus_synthetic'


def test_async_paid_topup_credits_once_across_both_event_types(owner):
    assert growth_glue.apply_billing_event(topup(owner))
    assert growth_glue.apply_billing_event(topup(owner, kind='checkout.session.async_payment_succeeded'))
    assert App.AICreditGrant.query.filter_by(user_id=owner).count() == 1
    assert App.db.session.get(App.User, owner).paid_until is None


@pytest.mark.parametrize('field,value', [('id', None), ('mode', 'subscription')])
def test_topup_requires_session_identity_and_payment_mode(owner, field, value):
    event = topup(owner)
    event['data']['object'][field] = value
    assert not growth_glue.apply_billing_event(event)
    assert not App.AICreditGrant.query.filter_by(user_id=owner).count()


@pytest.mark.parametrize('end', [None, 0, -1, True, 'not-a-time', 10**30])
def test_invalid_service_period_never_falls_back_to_a_free_month(owner, end):
    event = invoice(owner)
    event['data']['object']['lines']['data'][0]['period']['end'] = end
    with pytest.raises(ValueError, match='valid owner or service period'):
        growth_glue.apply_billing_event(event)
    assert App.db.session.get(App.User, owner).paid_until is None


def test_proration_and_one_off_lines_do_not_override_subscription_period(owner):
    event = invoice(owner, modern=True)
    lines = event['data']['object']['lines']['data']
    unrelated = {'type': 'invoiceitem', 'period': {'end': END + 365 * 86400},
                 'price': {'id': 'price_premium_test'}}
    proration = deepcopy(lines[0])
    proration['parent']['subscription_item_details']['proration'] = True
    proration['period']['end'] = END + 365 * 86400
    lines[:0] = [unrelated, proration]
    assert paid_period(event['data']['object']).end == datetime(2026, 11, 19, 12)


def test_paid_invoice_rejects_plan_price_mismatch(owner):
    event = invoice(owner)
    event['data']['object']['lines']['data'][0]['price']['id'] = 'price_pro_test'
    with pytest.raises(ValueError, match='does not match'):
        growth_glue.apply_billing_event(event)


def test_duplicate_configured_prices_cannot_choose_a_tier(owner, monkeypatch):
    monkeypatch.setenv('STRIPE_PRICE_ID', 'price_premium_test')
    obj = invoice(owner)['data']['object']
    del obj['subscription_details']['metadata']['plan']
    with pytest.raises(ValueError, match='configuration is ambiguous'):
        paid_period(obj)


def test_older_paid_invoice_cannot_restore_an_old_tier_or_customer(owner):
    old = invoice(owner)
    old['created'] = 100
    current = invoice(owner)
    current['created'] = 200
    obj = current['data']['object']
    obj['subscription_details']['metadata']['plan'] = 'pro'
    obj['lines']['data'][0]['price']['id'] = 'price_pro_test'
    obj['lines']['data'][0]['period']['end'] = END + 30 * 86400
    obj['customer'] = 'cus_current'
    assert growth_glue.apply_billing_event(current, now=NOW)
    assert growth_glue.apply_billing_event(old, now=NOW)
    user = App.db.session.get(App.User, owner)
    assert user.plan_tier == 'pro'
    assert user.stripe_customer_id == 'cus_current'
    assert user.stripe_paid_until == datetime(2026, 12, 19, 12)


def test_same_period_newer_change_survives_older_replay_and_referral_time(owner):
    old = invoice(owner)
    old['created'] = 100
    current = invoice(owner)
    current['created'] = 200
    current['data']['object']['subscription_details']['metadata']['plan'] = 'pro'
    current['data']['object']['lines']['data'][0]['price']['id'] = 'price_pro_test'
    user = App.db.session.get(App.User, owner)
    user.paid_until = NOW + timedelta(days=365)
    App.db.session.commit()
    assert growth_glue.apply_billing_event(current, now=NOW)
    assert growth_glue.apply_billing_event(old, now=NOW)
    user = App.db.session.get(App.User, owner)
    assert user.plan_tier == 'pro'
    assert user.paid_until == NOW + timedelta(days=365)


def test_expired_invoice_cannot_change_an_existing_paid_tier(owner):
    user = App.db.session.get(App.User, owner)
    user.plan_tier = 'pro'
    user.paid_until = NOW + timedelta(days=365)
    App.db.session.commit()
    event = invoice(owner)
    assert not growth_glue.apply_billing_event(event, now=datetime(2027, 1, 1))
    assert App.db.session.get(App.User, owner).plan_tier == 'pro'


@pytest.mark.parametrize('mutation', ['truncated', 'ambiguous'])
def test_ambiguous_or_truncated_invoice_cannot_grant(owner, mutation):
    obj = invoice(owner)['data']['object']
    if mutation == 'truncated':
        obj['lines']['has_more'] = True
    else:
        obj['lines']['data'].append(deepcopy(obj['lines']['data'][0]))
    with pytest.raises(ValueError):
        paid_period(obj)


def test_real_signed_webhook_retries_failed_credit_then_acknowledges_duplicate(owner, monkeypatch):
    # Actual Stripe signature verification; all keys/users/events are synthetic.
    monkeypatch.setenv('BILLING_ENABLED', '1')
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_test_synthetic_no_external_requests')
    secret = 'whsec_synthetic_loopback_only'
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', secret)
    App.app.config['TESTING'] = True
    body = json.dumps(topup(owner)).encode()
    stamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), stamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
    headers = {'Stripe-Signature': f't={stamp},v1={signature}'}
    real_grant = premium_glue.grant_credit
    monkeypatch.setattr(premium_glue, 'grant_credit', lambda *a, **kw: False)
    with App.app.test_client() as client:
        assert client.post('/api/billing/webhook', data=body, headers=headers).status_code == 500
        assert not App.AICreditGrant.query.filter_by(user_id=owner).count()
        monkeypatch.setattr(premium_glue, 'grant_credit', real_grant)
        assert client.post('/api/billing/webhook', data=body, headers=headers).status_code == 200
        assert client.post('/api/billing/webhook', data=body, headers=headers).status_code == 200
    assert App.AICreditGrant.query.filter_by(user_id=owner).count() == 1


@pytest.mark.parametrize('modern', [False, True])
def test_actual_sdk_parses_signed_invoice_and_persists_its_period(owner, monkeypatch, modern):
    monkeypatch.setenv('BILLING_ENABLED', '1')
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_test_synthetic_no_external_requests')
    secret = 'whsec_synthetic_invoice_only'
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', secret)
    App.app.config['TESTING'] = True
    body = json.dumps(invoice(owner, modern)).encode()
    stamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), stamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
    with App.app.test_client() as client:
        response = client.post('/api/billing/webhook', data=body,
                               headers={'Stripe-Signature': f't={stamp},v1={signature}'})
    assert response.status_code == 200
    assert App.db.session.get(App.User, owner).paid_until == datetime(2026, 11, 19, 12)


@pytest.mark.parametrize('kind', [
    'customer.subscription.deleted',
    'invoice.payment_failed',
    'invoice.marked_uncollectible',
    'invoice.voided',
    'charge.refunded',
    'charge.dispute.created',
    'charge.dispute.funds_withdrawn',
])
def test_subscription_revocation_ends_stripe_access_and_preserves_referral_bonus(owner, kind):
    paid = invoice(owner)
    paid['created'] = 100
    assert growth_glue.apply_billing_event(paid, now=NOW)

    # First verify revocation without extra referral time ends access immediately.
    if kind.startswith('invoice.'):
        rev = deepcopy(invoice(owner))
        rev['type'] = kind
        rev['created'] = 200
        rev['data']['object']['status'] = 'uncollectible'
    elif kind == 'customer.subscription.deleted':
        rev = {'type': kind, 'created': 200, 'data': {'object': {
            'customer': 'cus_synthetic', 'current_period_end': END,
            'metadata': {'user_id': str(owner), 'plan': 'premium'},
        }}}
    else:
        rev = {'type': kind, 'created': 200, 'data': {'object': {
            'customer': 'cus_synthetic', 'refunded': True, 'amount': 2000, 'amount_refunded': 2000,
            'metadata': {'user_id': str(owner), 'period_end': END},
        }}}
    assert growth_glue.apply_billing_event(rev, now=NOW)
    user = App.db.session.get(App.User, owner)
    assert user.paid_until == NOW
    assert user.stripe_paid_until == datetime(2026, 11, 19, 12)
    assert user.plan_tier is None
    assert user.stripe_event_created == 200

    # Replay of the earlier invoice (created=100) must not restore revoked access.
    assert growth_glue.apply_billing_event(paid, now=NOW)
    user = App.db.session.get(App.User, owner)
    assert user.paid_until == NOW
    assert user.plan_tier is None

    # Now grant a newer invoice + earned referral time, then revoke: referral time stays Pro.
    newer = invoice(owner)
    newer['created'] = 300
    newer['data']['object']['lines']['data'][0]['period']['end'] = END + 30 * 86400
    assert growth_glue.apply_billing_event(newer, now=NOW)
    user = App.db.session.get(App.User, owner)
    referral_end = datetime(2027, 2, 1, 12)
    user.paid_until = referral_end
    App.db.session.commit()

    rev2 = deepcopy(rev)
    rev2['created'] = 400
    if 'current_period_end' in rev2['data']['object']:
        rev2['data']['object']['current_period_end'] = END + 30 * 86400
    elif 'lines' in rev2['data']['object']:
        rev2['data']['object']['lines']['data'][0]['period']['end'] = END + 30 * 86400
    else:
        rev2['data']['object']['metadata']['period_end'] = END + 30 * 86400
    assert growth_glue.apply_billing_event(rev2, now=NOW)
    user = App.db.session.get(App.User, owner)
    assert user.paid_until == referral_end
    assert user.stripe_paid_until == datetime(2026, 12, 19, 12)
    assert user.plan_tier is None


def test_stale_revocation_for_prior_period_does_not_cancel_renewed_subscription(owner):
    renewed = invoice(owner)
    renewed['created'] = 300
    renewed['data']['object']['lines']['data'][0]['period']['end'] = END + 30 * 86400
    assert growth_glue.apply_billing_event(renewed, now=NOW)

    stale_void = invoice(owner)
    stale_void['type'] = 'invoice.voided'
    stale_void['created'] = 350
    stale_void['data']['object']['status'] = 'void'
    # stale_void has period.end == END < renewed stripe_paid_until (END + 30d)
    assert growth_glue.apply_billing_event(stale_void, now=NOW)
    user = App.db.session.get(App.User, owner)
    assert user.plan_tier == 'premium'
    assert user.stripe_paid_until == datetime(2026, 12, 19, 12)


@pytest.mark.parametrize('kind', [
    'invoice.payment_failed', 'invoice.marked_uncollectible', 'invoice.voided',
    'customer.subscription.deleted', 'charge.refunded', 'charge.dispute.created',
])
def test_other_period_revocation_preserves_paid_access_and_later_payment(owner, kind):
    paid = invoice(owner)
    paid['created'] = 100
    assert growth_glue.apply_billing_event(paid, now=NOW)
    if kind.startswith('invoice.'):
        rev = deepcopy(paid)
        rev['data']['object']['status'] = 'open'
        rev['data']['object']['lines']['data'][0]['period']['end'] = END + 30 * 86400
    else:
        rev = {'data': {'object': {
            'customer': 'cus_synthetic', 'current_period_end': END + 30 * 86400,
            'refunded': True,
            'metadata': {'user_id': str(owner), 'period_end': END + 30 * 86400},
        }}}
    rev.update(type=kind, created=300)
    assert growth_glue.apply_billing_event(rev, now=NOW)
    user = App.db.session.get(App.User, owner)
    assert (user.paid_until, user.stripe_paid_until, user.plan_tier, user.stripe_event_created) == (
        datetime(2026, 11, 19, 12), datetime(2026, 11, 19, 12), 'premium', 100)

    # Payment succeeds after an earlier attempt failed. Its older event must
    # still apply: an unrelated failure must not advance the paid watermark.
    renewal = deepcopy(paid)
    renewal['created'] = 200
    renewal['data']['object']['lines']['data'][0]['period']['end'] = END + 30 * 86400
    assert growth_glue.apply_billing_event(renewal, now=NOW)
    assert App.db.session.get(App.User, owner).paid_until == datetime(2026, 12, 19, 12)


@pytest.mark.parametrize('kind', [
    'invoice.payment_failed', 'invoice.marked_uncollectible', 'invoice.voided',
    'customer.subscription.deleted', 'charge.refunded', 'charge.dispute.created',
])
def test_missing_revocation_period_retries_without_changing_entitlement(owner, kind):
    paid = invoice(owner)
    paid['created'] = 100
    assert growth_glue.apply_billing_event(paid, now=NOW)
    rev = {'type': kind, 'created': 200, 'data': {'object': {
        'customer': 'cus_synthetic', 'refunded': True,
        'metadata': {'user_id': str(owner)},
    }}}
    with pytest.raises(ValueError, match='service period'):
        growth_glue.apply_billing_event(rev, now=NOW)
    user = App.db.session.get(App.User, owner)
    assert (user.paid_until, user.plan_tier, user.stripe_event_created) == (
        datetime(2026, 11, 19, 12), 'premium', 100)


def test_signed_unresolved_refund_retries_then_period_reconciliation_can_revoke(owner, monkeypatch):
    paid = invoice(owner)
    paid['created'] = 100
    assert growth_glue.apply_billing_event(paid, now=NOW)
    monkeypatch.setenv('BILLING_ENABLED', '1')
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_test_synthetic_no_external_requests')
    secret = 'whsec_synthetic_refund_retry'
    monkeypatch.setenv('STRIPE_WEBHOOK_SECRET', secret)
    monkeypatch.setattr(growth_glue, 'utcnow', lambda: NOW)
    event = {'type': 'charge.refunded', 'created': 200, 'data': {'object': {
        'id': 'ch_synthetic_unresolved', 'customer': 'cus_synthetic', 'refunded': True,
        'metadata': {'user_id': str(owner)},
    }}}

    def deliver(client):
        body = json.dumps(event).encode()
        stamp = str(int(time.time()))
        signature = hmac.new(secret.encode(), stamp.encode() + b'.' + body, hashlib.sha256).hexdigest()
        return client.post('/api/billing/webhook', data=body,
                           headers={'Stripe-Signature': f't={stamp},v1={signature}'})

    with App.app.test_client() as client:
        response = deliver(client)
        assert response.status_code == 500
        assert response.get_json() == {'status': 'error'}
        user = App.db.session.get(App.User, owner)
        assert (user.paid_until, user.plan_tier, user.stripe_event_created) == (
            datetime(2026, 11, 19, 12), 'premium', 100)
        # Declared synthetic evidence, not a live Stripe reconciliation claim.
        event['data']['object']['metadata']['period_end'] = END
        assert deliver(client).status_code == 200
        assert deliver(client).status_code == 200
    user = App.db.session.get(App.User, owner)
    assert (user.paid_until, user.plan_tier, user.stripe_event_created) == (NOW, None, 200)


def test_partial_refund_does_not_revoke_subscription_or_topup(owner):
    paid = invoice(owner)
    paid['created'] = 100
    assert growth_glue.apply_billing_event(paid, now=NOW)
    assert growth_glue.apply_billing_event(topup(owner), now=NOW)

    partial_sub = {'type': 'charge.refunded', 'created': 200, 'data': {'object': {
        'customer': 'cus_synthetic', 'refunded': False, 'amount': 2000, 'amount_refunded': 500,
        'metadata': {'user_id': str(owner)},
    }}}
    partial_topup = {'type': 'charge.refunded', 'created': 200, 'data': {'object': {
        'id': 'ch_topup_1', 'refunded': False, 'amount': 500, 'amount_refunded': 100,
        'metadata': {'user_id': str(owner), 'kind': 'ai_topup'},
    }}}
    assert not growth_glue.apply_billing_event(partial_sub, now=NOW)
    assert not growth_glue.apply_billing_event(partial_topup, now=NOW)
    user = App.db.session.get(App.User, owner)
    assert user.plan_tier == 'premium'
    assert premium_glue.granted_micro(owner, NOW.strftime('%Y-%m')) == 4_000_000


@pytest.mark.parametrize('kind', ['charge.refunded', 'charge.dispute.created', 'charge.dispute.funds_withdrawn'])
def test_topup_refund_or_dispute_claws_back_credit_idempotently(owner, kind):
    assert growth_glue.apply_billing_event(topup(owner), now=NOW)
    period = utcnow().strftime('%Y-%m')
    assert premium_glue.granted_micro(owner, period) == 4_000_000

    rev = {'type': kind, 'created': 200, 'data': {'object': {
        'id': 'ch_topup_rev', 'payment_intent': 'pi_topup_1',
        'refunded': True, 'amount': 500, 'amount_refunded': 500,
        'metadata': {'user_id': str(owner), 'kind': 'ai_topup'},
    }}}
    assert growth_glue.apply_billing_event(rev, now=NOW)
    assert growth_glue.apply_billing_event(rev, now=NOW)
    assert premium_glue.granted_micro(owner, period) == 0


def test_referral_settlement_locks_and_preserves_concurrent_invoice_grant(owner):
    inviter = App.db.session.get(App.User, owner)
    referee = App.User(email=f'ref-{uuid4().hex}@example.test', password_hash='x',
                       birth_year=2000, referred_by_id=owner, created_at=NOW - timedelta(days=1))
    App.db.session.add(referee)
    App.db.session.commit()
    App.db.session.add(App.SavedSchedule(user_id=referee.id, schedule_data='{}', created_at=NOW))
    App.db.session.commit()

    # Simulate an invoice arriving and updating the DB row while caller holds an older ORM instance.
    paid = invoice(owner)
    paid['created'] = 250
    assert growth_glue.apply_billing_event(paid, now=NOW)

    try:
        granted = growth_glue.settle_referral_rewards(inviter, now=NOW)
        assert granted == 1
        refreshed = App.db.session.get(App.User, owner)
        assert refreshed.plan_tier == 'premium'
        assert refreshed.stripe_paid_until == datetime(2026, 11, 19, 12)
        assert refreshed.paid_until == datetime(2026, 11, 19, 12) + timedelta(days=30)
    finally:
        App.SavedSchedule.query.filter_by(user_id=referee.id).delete()
        App.User.query.filter_by(id=referee.id).delete()
        App.db.session.commit()
