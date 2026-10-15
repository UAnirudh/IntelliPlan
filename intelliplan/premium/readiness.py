"""Read-only paid-launch diagnostics. Never enables billing or creates payments."""
from __future__ import annotations

from urllib.parse import quote, urlsplit

from intelliplan.premium import catalog


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

    offers = [('pro_monthly', 'STRIPE_PRICE_ID', 'month'),
              ('pro_yearly', 'STRIPE_PRICE_ID_YEARLY', 'year')]
    for plan in catalog.plans().values():
        for interval, variable in plan.stripe_price_env.items():
            offers.append((f'{plan.id}_{interval}', variable, 'year' if interval == catalog.YEARLY else 'month'))
    configured = 0
    price_ids = set()
    for name, variable, interval in offers:
        value = (env.get(variable) or '').strip()
        if not value:
            continue  # Unconfigured offers are not sold by the existing app.
        configured += 1
        check(name + '_unique', value not in price_ids, 'Each offer must use its own Stripe Price.')
        price_ids.add(value)
        try:
            price = retrieve('/v1/prices/' + quote(value, safe=''))
            recurring = price.get('recurring') or {}
            valid = (price.get('active') is True and price.get('livemode') is True
                     and price.get('type') == 'recurring' and price.get('currency') == 'usd'
                     and isinstance(price.get('unit_amount'), int) and price['unit_amount'] > 0
                     and recurring.get('interval') == interval
                     and recurring.get('interval_count') == 1
                     and recurring.get('usage_type') == 'licensed')
            check(name, valid, 'Price must be active, live, positive USD and use the advertised recurring interval.')
        except Exception:
            check(name, False, 'Could not read this offer; inspect its configured Stripe Price.')
    check('configured_offer', configured > 0, 'Configure at least one recurring paid offer.')
    return {'configuration_ready': all(row['passed'] for row in checks),
            'billing_enabled': billing_enabled, 'checks': checks,
            'limitations': ['Does not enable billing or create any payment.',
                            'Does not prove webhook delivery, checkout, entitlement correctness, tax or legal readiness.',
                            'Does not validate displayed price amounts against Stripe amounts or profitability.',
                            'Unconfigured offers and top-ups are not assessed.']}
