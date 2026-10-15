# Read-only paid-launch diagnostics

The new standalone diagnostic checks live-key configuration, webhook-secret
presence, HTTPS site configuration, Stripe's charges/payouts/details flags,
and configured recurring prices. Prices must be unique across offers, active,
live, positive USD subscriptions with the advertised month/year interval,
interval count1 and licensed usage. Missing offers are not assumed on sale;
at least one configured recurring offer is required.

Only Stripe GET requests are used. The diagnostic does not turn on billing,
alter any account, create checkout sessions or charge anyone. Output includes
only named checks and booleans; provider exceptions/bodies, keys and account
objects are not printed. Requests time out and failures produce an incomplete
result. It is separate from the production checkout flow and adds no new
public API or account-data surface.

Local evidence:21 mocked tests cover ready-but-disabled configuration,
unverified account flags, inactive/test/zero/non-USD/nonrecurring prices,
incorrect intervals/usage, duplicates, secret-bearing provider failures and
missing credentials making zero requests. Review caught an incomplete HTTPS
URL passing the original prefix check; parsed host/port/credential checks and
six URL regressions now cover that case. Scoped Ruff passes. Running the CLI
locally reports missing Stripe credentials and no provider requests, consistent
with this development environment. No live Stripe call was made; production
account verification remains unknown in this checkpoint.

This does not establish full launch readiness. It cannot verify a signing
secret's value, webhook delivery, real checkout/entitlement/cancellation paths,
displayed amount agreement, taxes or sustainable margins. Those remain in the
original acceptance ledger, along with live provider checks and user-facing
plan evidence. Documentation describes exactly these limits.
