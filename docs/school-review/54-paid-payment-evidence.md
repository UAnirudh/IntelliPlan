# Paid entitlement evidence before launch

Checkout completion previously granted a rolling 31-day subscription window
without payment confirmation, and top-ups could be credited while payment
was pending. Subscriptions now require a paid invoice with a configured,
non-prorated subscription price and its actual service-period endpoint.
Checkout alone grants no subscription time. Legacy and current invoice
parent/pricing shapes are supported; one-off and proration lines are ignored.
Invalid periods, plan/price mismatches, ambiguous configured prices and
truncated or ambiguous invoice evidence cannot invent access.

Confirmed payment-mode top-ups support completed and asynchronous-success
events. The same session reference credits only once across both event types.
A failed credit write now causes webhook 500 for retry rather than silently
acknowledging a lost purchase. Current Stripe SDK resources are normalized
to dictionaries after signature verification; actual SDK signed-event tests
exposed the old handler's incompatible dict assumptions.

Two nullable User columns record the latest Stripe paid-period endpoint and
event creation timestamp independently of referral-extended access. Invoice
handlers lock and refresh the owner row before comparing these values, so
older retries cannot replace a newer tier or customer. Expired invoices are
ignored; contradictory equal timestamps fail for retry. Existing paid access
is never shortened by these invoice writes. This serializes invoice handlers,
not concurrent legacy referral writers. Accounts without a historical
watermark still require reconciliation before billing is enabled.

Schema changes use the existing additive startup path. Disposable legacy
SQLite migration tests preserve original user data and repeat successfully.
Injected failure of either new column leaves release health unready; both
columns are now included in users_schema_ok. Account-deletion tests also pass.

Final focused verification passed 138 billing/growth/Premium/deletion/release
cases, including actual SDK verification of synthetic signed invoice and
top-up requests, a failed write followed by successful retry/duplicate,
modern/legacy invoices, unpaid rejection, malformed periods, price ambiguity,
older/same-period replay and sequential referral preservation. Independent
Python/security and specification reviews corrected price, replay and schema
readiness findings. Full local and hosted checks are separate release gates.
No live Stripe requests, real payments or production students were used.

This follows Stripe's [subscription webhook](https://docs.stripe.com/billing/subscriptions/webhooks)
and [fulfillment](https://docs.stripe.com/checkout/fulfillment.md?payment-ui=stripe-hosted)
contracts, and the [SDK resource conversion guidance](https://github.com/stripe/stripe-python#working-with-api-resources).

Billing remains disabled by default and was not enabled here. Stripe account
verification, live signed delivery, checkout/portal transport, historical
reconciliation, refunds/disputes/cancellation, prorated plan changes, every
event ordering, amount/currency agreement and concurrency with referral
writes remain launch work. This slice does not certify paid-launch readiness
or complete the original acceptance ledger.

Release: PR105 merged at 89fcf5c8d63981e294506805cb461496c34c51fa.
Hosted checks passed 4,783 unit tests (16 skipped), 130 public browser tests
(1 skipped), all component/device workflows and Security. Exact public
revision and schema readiness passed. Railway plugin confirmed web deployment
7883cb86-7d43-4710-a6ec-e7bd7e5f264a and newsletter
4a1d77e0-f03b-4a32-8a12-2fd19feb6965 SUCCESS with no issues. A bounded
filtered startup error scan returned zero entries. This verifies deployment
and required schema presence, not live Stripe transactions. Post-merge
Tests 38856297854 remains running at this record's creation.
