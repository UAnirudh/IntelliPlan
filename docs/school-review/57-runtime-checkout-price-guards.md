# Runtime Checkout price validation

Subscription and top-up Checkout previously passed the configured Stripe Price
ID directly to `stripe.checkout.Session.create` without verifying that the
Price in Stripe matched the base amount, currency, billing interval or pricing
shape displayed in IntelliPlan.

Both `growth_glue.py:_checkout_for` (covering student subscription Checkout and
parent payment links) and `premium_glue.py:api_premium_topup` (covering AI
budget top-ups) now call shared offer validation in
`intelliplan/premium/readiness.py:validate_checkout_offer` immediately before
creating a Checkout session.

For the selected offer (`pro` monthly/yearly, `premium` monthly/yearly,
`premium_byok` monthly/yearly, or `topup`), validation requires:
- A configured Price ID that is not duplicated across any other recurring or
  top-up offer in the environment.
- `stripe.Price.retrieve` returning an active, live-mode, fixed per-unit USD
  Price with no custom unit amount or quantity transform.
- Exact whole-cent agreement with the shared catalog's displayed base amount.
- Matching recurrence (`type == 'recurring'`, `interval` matching month/year,
  `interval_count == 1`, `usage_type == 'licensed'`) for subscriptions, or
  `type == 'one_time'` with `recurring is None` for top-ups.
- Normalization of both dict and Stripe SDK resource objects (`to_dict()`).

If validation fails or `stripe.Price.retrieve` raises an error, Checkout fails
closed before `stripe.checkout.Session.create` is called. `BILLING_ENABLED`,
minor/parent-link and Premium-tier gates remain unchanged.

Evidence: 59 readiness and runtime-guard tests in
`tests/test_paid_launch_readiness.py` and 168 combined tests across
readiness, entitlements, growth and Premium suites pass. Every recurring offer,
parent pay-link and top-up path is verified to call `Price.retrieve` before
`Session.create` and to make zero `Session.create` calls when the Price
disagrees, is duplicated, uses non-fixed pricing or fails provider retrieval.

Billing remains disabled by default. This change guards runtime Checkout
session creation against misconfigured Price objects; it does not verify live
Stripe account status, taxes/discounts/fees, historical reconciliation or
webhook delivery. The original acceptance ledger remains incomplete.
