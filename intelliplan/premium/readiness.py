"""Read-only paid-launch diagnostics. Never enables billing or creates payments."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlsplit

from intelliplan.growth import plans
from intelliplan.premium import catalog


def _fixed_amount_matches(price, advertised):
    """Compare fixed USD base amounts without rounding a subcent price."""
    try:
        expected = Decimal(str(advertised)) * 100
        actual = price.get('unit_amount')
        decimal = price.get('unit_amount_decimal')
        return (expected.is_finite() and expected > 0 and expected == expected.to_integral_value()
                and type(actual) is int and actual == expected
                and (decimal is None or Decimal(str(decimal)) == expected)
                and price.get('billing_scheme') == 'per_unit'
                and price.get('custom_unit_amount') is None
                and price.get('transform_quantity') is None)
    except (InvalidOperation, TypeError, ValueError):
        return False


def check_launch(env, retrieve):
    """Return only allowlisted results; provider objects and keys never escape.

    ``retrieve`` receives a relative Stripe GET path. Keeping transport separate
    makes failure cases testable without using a live account or charging it.
    """
    checks = []

    def check(name, passed, message):
        checks.append({'check': name, 'passed': bool(passed), 'message': message})

    key = (env.get('STRIPE_SECRET_KEY') or '').strip()
    check('live_key', key.startswith(('sk_live_', 'rk_live_')), 'A live Stripe key is required for launch.')
    check('webhook_secret', bool((env.get('STRIPE_WEBHOOK_SECRET') or '').strip()),
          'A webhook signing secret must be configured; this check cannot verify its value.')
    try:
        site = urlsplit(env.get('APP_BASE_URL') or '')
        valid_site = (site.scheme == 'https' and bool(site.hostname)
                      and not any(char.isspace() for char in site.netloc)
                      and not site.username and not site.password and not site.query and not site.fragment)
        site.port  # Reject malformed or out-of-range ports.
    except ValueError:
        valid_site = False
    check('site_https', valid_site,
          'The public application URL must use HTTPS.')
    billing_enabled = (env.get('BILLING_ENABLED') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
    if not key:
        return {'configuration_ready': False, 'billing_enabled': billing_enabled,
                'checks': checks, 'limitations': ['No provider requests made without a key.']}
    try:
        account = retrieve('/v1/account')
        check('charges_enabled', account.get('charges_enabled') is True,
              'Stripe must allow charges on this account.')
        check('payouts_enabled', account.get('payouts_enabled') is True,
              'Stripe must allow payouts on this account.')
        check('details_submitted', account.get('details_submitted') is True,
              'Stripe account details must be submitted.')
    except Exception:
        # Provider errors may contain keys, account PII or request details.
        check('account_read', False, 'Could not read account readiness; inspect Stripe directly.')

    offers = [('pro_monthly', 'STRIPE_PRICE_ID', 'month', plans.PRO_PRICES_USD[plans.MONTHLY]),
              ('pro_yearly', 'STRIPE_PRICE_ID_YEARLY', 'year', plans.PRO_PRICES_USD[plans.YEARLY])]
    for plan in catalog.plans(env).values():
        for interval, variable in plan.stripe_price_env.items():
            offers.append((f'{plan.id}_{interval}', variable, interval, plan.price_usd[interval]))
    offers.append(('topup', 'STRIPE_PRICE_ID_TOPUP', None, catalog.topup(env).price_usd))
    configured = 0
    price_ids = set()
    for name, variable, interval, amount in offers:
        value = (env.get(variable) or '').strip()
        if not value:
            continue  # Unconfigured offers are not sold by the existing app.
        configured += int(interval is not None)
        check(name + '_unique', value not in price_ids, 'Each offer must use its own Stripe Price.')
        price_ids.add(value)
        try:
            price = retrieve('/v1/prices/' + quote(value, safe=''))
            recurring = price.get('recurring') or {}
            recurrence_ok = (price.get('type') == 'one_time' and price.get('recurring') is None
                             if interval is None else price.get('type') == 'recurring'
                             and recurring.get('interval') == interval
                             and recurring.get('interval_count') == 1
                             and recurring.get('usage_type') == 'licensed')
            valid = (price.get('active') is True and price.get('livemode') is True
                     and price.get('currency') == 'usd' and recurrence_ok
                     and _fixed_amount_matches(price, amount))
            check(name, valid, 'Price must be active, live, fixed USD matching the displayed amount and purchase interval.')
        except Exception:
            check(name, False, 'Could not read this offer; inspect its configured Stripe Price.')
    check('configured_offer', configured > 0, 'Configure at least one recurring paid offer.')
    return {'configuration_ready': all(row['passed'] for row in checks),
            'billing_enabled': billing_enabled, 'checks': checks,
            'limitations': ['Does not enable billing or create any payment.',
                            'Does not prove webhook delivery, checkout, entitlement correctness, tax or legal readiness.',
                            'Base amounts are compared before taxes, discounts and fees; profitability is not assessed.',
                            'Unconfigured offers are not assessed. This diagnostic does not guard runtime checkout.']}
