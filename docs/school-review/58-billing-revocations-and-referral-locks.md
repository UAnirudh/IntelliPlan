# Billing revocations and referral row locks

Subscription webhooks previously handled only `invoice.paid` and one-time top-up
completions. Cancellations, failed/voided/uncollectible invoices, refunds and
disputes did not revoke active Stripe entitlements or claw back top-up budget,
and referral reward settlement did not lock the inviter and referee rows before
extending `paid_until`.

`intelliplan/premium/billing_events.py:revoked_period` and
`growth_glue.py:apply_billing_event` now process verified revocation events:
- `customer.subscription.deleted`
- `invoice.payment_failed`, `invoice.marked_uncollectible`, `invoice.voided`
- `charge.refunded` (full refunds only), `charge.dispute.created`,
  `charge.dispute.funds_withdrawn`

For subscription revocations, the owner row is locked with `with_for_update()`.
Stale revocations for an older billing period or older event timestamp are
ignored so a late cancellation/void webhook cannot erase a newer renewal.
When the current Stripe period is revoked:
- `plan_tier` is reset to `None` (removing Premium/Pro Stripe tier status).
- If `paid_until` is within the revoked Stripe period, `paid_until` is ended at
  `now`. If the student has earned referral time extending beyond
  `stripe_paid_until`, that extra referral window is preserved as Pro.
- `stripe_paid_until` and `stripe_event_created` watermarks are retained/updated
  so an older replay of the original `invoice.paid` event cannot restore the
  revoked subscription, while a subsequent newer renewal still applies.

For AI budget top-ups, `premium_glue.py:api_premium_topup` attaches
`payment_intent_data.metadata` (`user_id`, `kind: ai_topup`), and
`premium_glue.py:apply_billing_event` handles full `charge.refunded` and
`charge.dispute.*` events by recording an idempotent negative `AICreditGrant`
(`rev:<payment_intent|charge|id>`), failing with 500 for retry if persistence
fails.

`growth_glue.py:grant_signup_reward` and `settle_referral_rewards` now lock the
target user and pending referee rows via `with_for_update().populate_existing()`
before computing `paid_until`, preventing a stale in-memory user instance from
overwriting a concurrent webhook entitlement update.

Evidence: 38 entitlement/revocation/referral tests in
`tests/test_billing_entitlements.py` and 181 combined tests across readiness,
entitlements, growth and Premium suites pass. Billing remains disabled by
default; live Stripe account/webhook verification and historical reconciliation
remain open.
