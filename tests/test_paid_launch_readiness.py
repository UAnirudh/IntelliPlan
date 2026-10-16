"""Read-only configuration checks and runtime Checkout offer validation without live Stripe requests."""
from types import SimpleNamespace

import pytest

import App
import growth_glue
from intelliplan.premium.readiness import check_launch, validate_checkout_offer


def setup():
    env = {'STRIPE_SECRET_KEY': 'sk_live_fixture', 'STRIPE_WEBHOOK_SECRET': 'whsec_fixture',
           'APP_BASE_URL': 'https://example.test', 'STRIPE_PRICE_ID': 'price_fixture'}
    account = {'charges_enabled': True, 'payouts_enabled': True, 'details_submitted': True}
    price = {'active': True, 'livemode': True, 'type': 'recurring', 'currency': 'usd', 'unit_amount': 500,
             'billing_scheme': 'per_unit',
             'recurring': {'interval': 'month', 'interval_count': 1, 'usage_type': 'licensed'}}
    calls = []

    def retrieve(path):
        calls.append(path)
        return account if path == '/v1/account' else price

    return env, account, price, calls, retrieve


def test_ready_configuration_leaves_billing_disabled_and_reads_only():
    env, _, _, calls, retrieve = setup()
    report = check_launch(env, retrieve)
    assert report['configuration_ready'] is True
    assert report['billing_enabled'] is False
    assert 'BILLING_ENABLED' not in env
    assert calls == ['/v1/account', '/v1/prices/price_fixture']
    assert 'sk_live_fixture' not in str(report)


@pytest.mark.parametrize('field', ['charges_enabled', 'payouts_enabled', 'details_submitted'])
def test_unverified_account_is_not_ready(field):
    env, account, _, _, retrieve = setup()
    account[field] = False
    assert not check_launch(env, retrieve)['configuration_ready']


@pytest.mark.parametrize(('field', 'value'), [('active', False), ('livemode', False),
                         ('currency', 'eur'), ('unit_amount', 0), ('type', 'one_time')])
def test_unsuitable_price_is_not_ready(field, value):
    env, _, price, _, retrieve = setup()
    price[field] = value
    assert not check_launch(env, retrieve)['configuration_ready']


@pytest.mark.parametrize(('field', 'value'), [('interval', 'year'), ('interval_count', 3),
                                             ('usage_type', 'metered')])
def test_wrong_recurrence_is_not_ready(field, value):
    env, _, price, _, retrieve = setup()
    price['recurring'][field] = value
    assert not check_launch(env, retrieve)['configuration_ready']


def test_provider_errors_do_not_leak_secrets_or_account_data():
    env, _, _, _, _ = setup()
    def fail(path):
        raise RuntimeError('sk_live_fixture owner@example.test')
    report = check_launch(env, fail)
    assert not report['configuration_ready']
    assert 'sk_live_fixture' not in str(report)
    assert 'owner@example.test' not in str(report)


def test_missing_key_never_calls_provider():
    def fail(path):
        raise AssertionError('must not call')
    assert not check_launch({}, fail)['configuration_ready']


def test_duplicate_offers_fail_closed():
    env, _, _, _, retrieve = setup()
    env['STRIPE_PRICE_ID_PREMIUM'] = env['STRIPE_PRICE_ID']
    assert not check_launch(env, retrieve)['configuration_ready']


@pytest.mark.parametrize('url', ['https://', 'http://example.test', 'https://bad host',
                                 'https://example.test:bad', 'https://user:pass@example.test',
                                 'https://example.test?wrong=1'])
def test_invalid_site_url_is_not_ready(url):
    env, _, _, _, retrieve = setup()
    env['APP_BASE_URL'] = url
    assert not check_launch(env, retrieve)['configuration_ready']


def test_price_that_disagrees_with_displayed_pro_amount_is_not_ready():
    env, _, price, _, retrieve = setup()
    price['unit_amount'] = 800
    assert not check_launch(env, retrieve)['configuration_ready']


@pytest.mark.parametrize(('variable', 'cents', 'interval'), [
    ('STRIPE_PRICE_ID', 500, 'month'), ('STRIPE_PRICE_ID_YEARLY', 4800, 'year'),
    ('STRIPE_PRICE_ID_PREMIUM', 2000, 'month'), ('STRIPE_PRICE_ID_PREMIUM_YEARLY', 19200, 'year'),
    ('STRIPE_PRICE_ID_PREMIUM_BYOK', 800, 'month'), ('STRIPE_PRICE_ID_PREMIUM_BYOK_YEARLY', 7600, 'year'),
])
def test_each_advertised_offer_matches_its_own_amount(variable, cents, interval):
    env, _, price, _, retrieve = setup()
    del env['STRIPE_PRICE_ID']
    env[variable] = 'price_fixture'
    price['unit_amount'] = cents
    price['recurring']['interval'] = interval
    assert check_launch(env, retrieve)['configuration_ready']
    price['unit_amount'] += 1
    assert not check_launch(env, retrieve)['configuration_ready']


def test_explicit_environment_controls_premium_display_price_without_process_leak(monkeypatch):
    monkeypatch.setenv('PREMIUM_PRICE_USD', '99')
    env, _, price, _, retrieve = setup()
    del env['STRIPE_PRICE_ID']
    env.update(STRIPE_PRICE_ID_PREMIUM='price_fixture', PREMIUM_PRICE_USD='23.45')
    price['unit_amount'] = 2345
    assert check_launch(env, retrieve)['configuration_ready']


@pytest.mark.parametrize(('field', 'value'), [
    ('billing_scheme', 'tiered'), ('transform_quantity', {'divide_by': 10, 'round': 'up'}),
    ('custom_unit_amount', {'enabled': True}), ('unit_amount', True),
    ('unit_amount', 500.0), ('unit_amount_decimal', '500.1'),
])
def test_nonfixed_or_inconsistent_price_is_not_ready(field, value):
    env, _, price, _, retrieve = setup()
    price[field] = value
    assert not check_launch(env, retrieve)['configuration_ready']


def test_display_amount_with_subcent_precision_cannot_be_rounded_into_ready():
    env, _, price, _, retrieve = setup()
    del env['STRIPE_PRICE_ID']
    env.update(STRIPE_PRICE_ID_PREMIUM='price_fixture', PREMIUM_PRICE_USD='23.451')
    price['unit_amount'] = 2345
    assert not check_launch(env, retrieve)['configuration_ready']


def test_configured_topup_must_match_one_time_advertised_amount():
    env, account, price, calls, _ = setup()
    env['STRIPE_PRICE_ID_TOPUP'] = 'price_topup'
    topup = {**price, 'type': 'one_time', 'recurring': None}
    def retrieve(path):
        calls.append(path)
        return account if path == '/v1/account' else topup if path.endswith('price_topup') else price
    assert check_launch(env, retrieve)['configuration_ready']
    assert calls == ['/v1/account', '/v1/prices/price_fixture', '/v1/prices/price_topup']
    topup['unit_amount'] = 600
    assert not check_launch(env, retrieve)['configuration_ready']


def test_topup_alone_is_not_a_recurring_paid_plan():
    env, _, price, _, retrieve = setup()
    del env['STRIPE_PRICE_ID']
    env['STRIPE_PRICE_ID_TOPUP'] = 'price_fixture'
    price.update(type='one_time', recurring=None)
    assert not check_launch(env, retrieve)['configuration_ready']


@pytest.mark.parametrize(('plan', 'interval', 'variable', 'cents'), [
    ('pro', 'month', 'STRIPE_PRICE_ID', 500),
    ('pro', 'year', 'STRIPE_PRICE_ID_YEARLY', 4800),
    ('premium', 'month', 'STRIPE_PRICE_ID_PREMIUM', 2000),
    ('premium', 'year', 'STRIPE_PRICE_ID_PREMIUM_YEARLY', 19200),
    ('premium_byok', 'month', 'STRIPE_PRICE_ID_PREMIUM_BYOK', 800),
    ('premium_byok', 'year', 'STRIPE_PRICE_ID_PREMIUM_BYOK_YEARLY', 7600),
    ('topup', None, 'STRIPE_PRICE_ID_TOPUP', 500),
])
def test_validate_checkout_offer_supports_sdk_resources_and_rejects_mismatches(plan, interval, variable, cents):
    env = {variable: 'price_offer'}
    recurring = None if interval is None else SimpleNamespace(
        to_dict=lambda: {'interval': interval, 'interval_count': 1, 'usage_type': 'licensed'})
    raw = {'active': True, 'livemode': True, 'type': 'one_time' if interval is None else 'recurring',
           'currency': 'usd', 'unit_amount': cents, 'billing_scheme': 'per_unit', 'recurring': recurring}
    sdk_price = SimpleNamespace(to_dict=lambda: raw)
    assert validate_checkout_offer(env, lambda pid: sdk_price, plan=plan, interval=interval)
    raw['unit_amount'] = cents + 1
    assert not validate_checkout_offer(env, lambda pid: sdk_price, plan=plan, interval=interval)
    raw['unit_amount'] = cents
    env['STRIPE_PRICE_ID' if variable != 'STRIPE_PRICE_ID' else 'STRIPE_PRICE_ID_YEARLY'] = 'price_offer'
    assert not validate_checkout_offer(env, lambda pid: sdk_price, plan=plan, interval=interval)
    assert not validate_checkout_offer(env, lambda pid: sdk_price, plan='unknown', interval=interval)


@pytest.fixture
def checkout_harness(monkeypatch):
    import sys

    state = {'sessions': [], 'retrieved': [], 'price': {
        'active': True, 'livemode': True, 'type': 'recurring', 'currency': 'usd',
        'unit_amount': 500, 'billing_scheme': 'per_unit',
        'recurring': {'interval': 'month', 'interval_count': 1, 'usage_type': 'licensed'},
    }}

    class _Price:
        @staticmethod
        def retrieve(price_id):
            state['retrieved'].append(price_id)
            if isinstance(state['price'], Exception):
                raise state['price']
            return state['price']

    class _Session:
        @staticmethod
        def create(**params):
            state['sessions'].append(params)
            return SimpleNamespace(url='https://checkout.test/session')

    fake_stripe = SimpleNamespace(api_key='', Price=_Price, checkout=SimpleNamespace(Session=_Session))
    monkeypatch.setitem(sys.modules, 'stripe', fake_stripe)
    monkeypatch.setenv('BILLING_ENABLED', '1')
    monkeypatch.setenv('STRIPE_SECRET_KEY', 'sk_live_fixture')
    monkeypatch.setenv('STRIPE_PRICE_ID', 'price_pro_m')
    monkeypatch.setenv('STRIPE_PRICE_ID_YEARLY', 'price_pro_y')
    monkeypatch.setenv('STRIPE_PRICE_ID_PREMIUM', 'price_prem_m')
    monkeypatch.setenv('STRIPE_PRICE_ID_PREMIUM_YEARLY', 'price_prem_y')
    monkeypatch.setenv('STRIPE_PRICE_ID_PREMIUM_BYOK', 'price_byok_m')
    monkeypatch.setenv('STRIPE_PRICE_ID_PREMIUM_BYOK_YEARLY', 'price_byok_y')
    monkeypatch.setenv('STRIPE_PRICE_ID_TOPUP', 'price_topup')

    App.app.config['TESTING'] = True
    App.limiter.enabled = False
    with App.app.test_client() as client:
        with App.app.app_context():
            App.db.create_all()
            user = App.User(email='checkout-guard@example.test', password_hash='x', birth_year=2000,
                            plan_tier='premium')
            from datetime import timedelta
            from time_utils import utcnow
            user.paid_until = utcnow() + timedelta(days=20)
            App.db.session.add(user)
            App.db.session.commit()
            uid = user.id
            token = growth_glue.make_pay_token(uid)
        with client.session_transaction() as sess:
            sess['_user_id'] = str(uid)
            sess['_fresh'] = True
        yield client, uid, token, state
        with App.app.app_context():
            App.User.query.filter_by(id=uid).delete()
            App.db.session.commit()
    App.limiter.enabled = True


@pytest.mark.parametrize(('plan', 'interval', 'cents', 'price_id'), [
    ('pro', 'month', 500, 'price_pro_m'),
    ('pro', 'year', 4800, 'price_pro_y'),
    ('premium', 'month', 2000, 'price_prem_m'),
    ('premium', 'year', 19200, 'price_prem_y'),
    ('premium_byok', 'month', 800, 'price_byok_m'),
    ('premium_byok', 'year', 7600, 'price_byok_y'),
])
def test_runtime_subscription_and_parent_checkout_validate_before_creating_session(
    checkout_harness, plan, interval, cents, price_id
):
    client, _, token, state = checkout_harness
    state['price']['unit_amount'] = cents
    state['price']['recurring']['interval'] = interval
    res = client.post('/api/billing/checkout', json={'plan': plan, 'interval': interval})
    assert res.status_code == 200
    assert state['retrieved'] == [price_id]
    assert len(state['sessions']) == 1

    # Mismatched price fails closed before Session.create on both student and parent routes.
    state['sessions'].clear()
    state['retrieved'].clear()
    state['price']['unit_amount'] = cents + 100
    res_bad = client.post('/api/billing/checkout', json={'plan': plan, 'interval': interval})
    assert res_bad.status_code == 502
    assert state['sessions'] == []

    parent_bad = client.post(f'/pay/{token}', data={'plan': plan, 'interval': interval})
    assert parent_bad.status_code == 503
    assert state['sessions'] == []


@pytest.mark.parametrize(('mutation', 'value'), [
    ('active', False),
    ('livemode', False),
    ('currency', 'eur'),
    ('billing_scheme', 'tiered'),
    ('custom_unit_amount', {'enabled': True}),
    ('transform_quantity', {'divide_by': 2, 'round': 'up'}),
    ('provider_error', RuntimeError('stripe outage')),
])
def test_runtime_checkout_and_topup_fail_closed_without_session_create(checkout_harness, mutation, value):
    client, _, _, state = checkout_harness
    if mutation == 'provider_error':
        state['price'] = value
    else:
        state['price'][mutation] = value
    assert client.post('/api/billing/checkout', json={'plan': 'pro', 'interval': 'month'}).status_code == 502
    assert state['sessions'] == []

    if not isinstance(state['price'], Exception):
        state['price'].update(type='one_time', recurring=None)
    assert client.post('/api/premium/topup', json={}).status_code == 502
    assert state['sessions'] == []


def test_runtime_topup_validates_one_time_offer_and_rejects_duplicate_price_ids(checkout_harness, monkeypatch):
    client, _, _, state = checkout_harness
    state['price'].update(type='one_time', recurring=None, unit_amount=500)
    res = client.post('/api/premium/topup', json={})
    assert res.status_code == 200
    assert state['retrieved'] == ['price_topup']
    assert len(state['sessions']) == 1

    state['sessions'].clear()
    monkeypatch.setenv('STRIPE_PRICE_ID', 'price_topup')
    dup = client.post('/api/premium/topup', json={})
    assert dup.status_code == 502
    assert state['sessions'] == []
