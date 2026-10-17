# Preserve paid access during unrelated billing failures

Audit of the recent billing lifecycle change reproduced two entitlement-loss
defects. A failure or cancellation for a later service period could end the
already-paid current window and advance its watermark. A refund, dispute or
invoice failure without a service period could revoke access based on customer
identity alone. One customer can have multiple invoices or purchases.

`growth_glue.py:apply_billing_event` now changes an existing Stripe entitlement
only when the supplied revocation period exactly matches the recorded paid
period and the event is not older. Different periods are acknowledged without
changing access or payment watermarks. Missing periods raise a fixed error
before account mutation; the signed webhook returns a retryable 500. Matching
revocations, duplicate handling and earned referral extensions are preserved.
No schema migration or billing activation is introduced.

Acceptance and evidence:

| Requirement | Classification | Evidence |
| --- | --- | --- |
| Different-period failure preserves paid access and watermark | Verified Working locally | Six cases, including failed renewal followed by an older-created successful payment |
| Missing period does not revoke access | Verified Working locally | Six missing-period cases |
| Retryable signed webhook preserves account state | Verified Working locally | Actual Stripe SDK signature verification with synthetic refund; missing evidence returns 500, synthetic matching evidence returns 200 and duplicates are idempotent |
| Matching revocations and referral extensions | Verified Working locally | Existing billing lifecycle cases retained |
| Live refund/dispute-to-invoice reconciliation | Not Implemented | Period-end metadata is not generally present on real Charge/Dispute objects; provider-backed linking remains a paid-launch blocker |

All twelve initial regressions failed against the previous source. The broader
billing/growth/Premium run also exposed a test-order leak: the pure-rule hook
fixture removed the application's metering hooks on teardown. It now restores
the prior resolver/gate, so a later application test still enforces its quota.
Independent Python/standards and specification reviews found no issues in this
scope; the fixture correction was independently re-reviewed. Scoped Ruff and
whitespace checks pass. Final suite and release results follow separately.

The reconciliation fixture deliberately supplies new synthetic evidence; it
does not prove that an unchanged Stripe retry can discover missing evidence.
Endpoint equality also does not uniquely identify a subscription or invoice.
Persisted transaction identity and provider-backed relationship verification,
real webhook delivery, historical reconciliation and broader event-ordering
coverage remain required before billing launch. Billing remains disabled.

Provider contracts: [subscription webhook events](https://docs.stripe.com/billing/subscriptions/webhooks),
[Charge object](https://docs.stripe.com/api/charges/object) and
[Dispute object](https://docs.stripe.com/api/disputes/object).
The original product transformation remains incomplete.

Released as PR111 at e9e795ff9c66069e7735c64cdcd8eedb0ef86e01. Both final-head
hosted Tests and Security passed, followed by postmerge Tests39006460080 and
Security39006460139. Public exact-revision/schema checks pass. Railway plugin
confirms web51956d06-f556-4f2f-975d-a89220602381 and
newsletter3ee73018-b1f0-4066-9838-0c60d38ac0ec SUCCESS, one running web replica
and no issues/pending work. A bounded filtered runtime-error scan returned zero
entries. This establishes release health, not live Stripe or reconciliation proof.
