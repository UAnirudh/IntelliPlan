# Paid-offer amount agreement diagnostic

The read-only launch diagnostic previously accepted any positive USD amount,
even when Stripe's configured Price disagreed with the amount displayed in
IntelliPlan. It also omitted one-time top-ups.

The diagnostic now compares every configured recurring offer with its shared
catalog amount and interval: Pro monthly/yearly, Premium monthly/yearly and
Premium BYOK monthly/yearly. Configured top-ups must be one-time fixed USD
prices matching their displayed amount. Duplicate Price IDs are rejected
across recurring offers and top-ups. A top-up alone cannot satisfy recurring
plan readiness.

Decimal comparison requires exact positive whole cents without rounding a
subcent display amount. Boolean/float unit amounts, inconsistent decimal
amounts, tiered prices, customer-adjustable prices and quantity transforms
cannot pass as fixed displayed prices. Existing live/active/currency/interval/
licensed-use checks remain in place. Reports contain allowlisted results,
not provider objects, keys, account data or configured Price IDs.

Shared catalog constructors accept an optional explicit environment snapshot.
Ordinary application calls keep using their process environment. Diagnostics
use only the supplied snapshot so unrelated local Premium price settings
cannot silently affect a target-environment check.

Evidence: 38 focused readiness tests pass, including all six offers, one-cent
mismatches, custom snapshot amounts, subcent rejection, unsupported pricing
shapes and top-ups. A broader 147-case billing/growth/Premium/entitlement run
passes. Independent Python/standards and spec reviews found no remaining
issues; the spec reviewer independently reran the 38 readiness cases. Scoped
Ruff and diff checks pass. Full-suite, hosted and release evidence follow
separately; this checkpoint is initially local.

This is a diagnostic only. It does not guard runtime Checkout, change billing
flags, create payments, assess profitability or prove the final amount after
taxes, discounts and fees. Unconfigured offers remain outside the check.
Stripe's amount/pricing-shape fields are described in the
[Price object reference](https://docs.stripe.com/api/prices/object).

A bounded attempt to run the existing read-only Stripe diagnostic using the
local Railway CLI returned Unauthorized before the child process ran. No
Stripe request was made by that attempt. The Railway plugin remains able to
verify releases, but live account/Price reads and live Fish synthesis/playback
remain unverified. No new service or credential workaround was created.
The original acceptance ledger and broader paid-launch requirements remain open.
