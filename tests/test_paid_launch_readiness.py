"""Read-only configuration checks without live Stripe requests."""
import pytest

from intelliplan.premium.readiness import check_launch


def setup():
    env = {'STRIPE_SECRET_KEY': 'sk_live_fixture', 'STRIPE_WEBHOOK_SECRET': 'whsec_fixture',
           'APP_BASE_URL': 'https://example.test', 'STRIPE_PRICE_ID': 'price_fixture'}
    account = {'charges_enabled': True, 'payouts_enabled': True, 'details_submitted': True}
    price = {'active': True, 'livemode': True, 'type': 'recurring', 'currency': 'usd', 'unit_amount': 800,
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
