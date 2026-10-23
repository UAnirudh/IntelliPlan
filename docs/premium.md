# Premium: Claude tutor, model routing, linked AI accounts

Code: `intelliplan/premium/` (pure rules), `premium_glue.py` (database, routes, hooks),
`Main_Project/templates/premium_settings.html` (`/settings/ai`). Tests: `tests/test_premium.py`.

## How a tutor turn works

1. **Route.** Groq (`GROQ_FAST_MODEL`) reads the question and returns JSON: intent, subject,
   difficulty 1–5, a tier, and whether the question is too vague to answer. Without Groq,
   Gemini is used if `gemini_available()`; without either, a keyword heuristic decides. The
   router's own cost is logged as `source="router"` and never charged to a student.
2. **Clarify.** If the question is too vague (and the student hasn't turned clarifying off,
   and the previous turn wasn't already a clarification), the student gets up to three
   questions instead of an answer. Nothing expensive has run. "Just answer" skips it.
3. **Answer.** In order:
   - the student's linked keys (Anthropic, OpenAI, Gemini, Groq), unmetered;
   - Claude on IntelliPlan's key, within the month's budget.
   If the budget can't cover the chosen tier's worst case, the router steps down
   (Opus → Sonnet → Haiku). If nothing fits, the tutor answers on the free models and says so.
4. **Charge.** The provider's reported usage (input, output, cache read, cache write) is
   priced (`intelliplan/premium/pricing.py`) and written to `ai_spend_events`.

| Tier | Default model | Effort | Max output |
|---|---|---|---|
| quick | `claude-haiku-5-5` | low | 1,500 |
| balanced | `claude-sonnet-5-5` | medium | 4,000 |
| deep | `claude-opus-5-5` | high | 8,000 |

The tutor prompt is sent first with a cache breakpoint, so later turns read it at a tenth of
the input price. Opus and Sonnet calls opt into server-side refusal fallbacks
(`fallbacks: "default"`); a refusal shows the student a redirect, not the raw stop.

Premium students' *other* AI features (plans, flashcards, …) also run on Claude via
`ai_provider`. Those calls are metered against the same budget, and Claude drops out of their
model chain once the budget is spent.

## Turning it on

Before enabling billing, run `python scripts/check_paid_launch.py` in the
configured environment. It makes only read-only Stripe requests and exits
nonzero for incomplete account verification or unsuitable configured recurring
prices. It prints allowlisted results, without keys or account details, and
leaves `BILLING_ENABLED` unchanged. It can assess configuration while selling
is still disabled. See [Stripe account retrieval](https://docs.stripe.com/api/accounts/retrieve)
and [price retrieval](https://docs.stripe.com/api/prices/retrieve).

This is a configuration diagnostic, not launch certification: it does not
verify webhook signing-secret correctness/delivery, checkout, entitlements,
displayed amounts, tax, profitability or legal readiness. Unconfigured offers
and top-ups are not assessed. Continue the acceptance ledger before launch.

| Variable | What it does |
|---|---|
| `ANTHROPIC_API_KEY` | IntelliPlan's Claude key. Without it Premium answers only on linked keys. |
| `GROQ_API_KEY` | The router. Strongly recommended; the heuristic is much cruder. |
| `BILLING_ENABLED=1`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | Existing billing switches. |
| `STRIPE_PRICE_ID_PREMIUM`, `STRIPE_PRICE_ID_PREMIUM_YEARLY` | Stripe Prices for Premium. |
| `STRIPE_PRICE_ID_PREMIUM_BYOK`, `STRIPE_PRICE_ID_PREMIUM_BYOK_YEARLY` | Prices for the linked-account plan. |
| `STRIPE_PRICE_ID_TOPUP` | One-time Price for a budget top-up. |
| `BYOK_ENABLED=1` | Allows linking AI accounts. **Update the Privacy Policy first** (below). |
| `DATA_ENCRYPTION_KEY` | Required for linking; keys are never stored unencrypted. |

Plan numbers (all optional, defaults shown):

| Variable | Default |
|---|---|
| `PREMIUM_PRICE_USD` / `_YEARLY` | 20 / 192 |
| `PREMIUM_AI_BUDGET_USD` | 20 |
| `PREMIUM_BYOK_PRICE_USD` / `_YEARLY` | 8 / 76 |
| `PREMIUM_BYOK_AI_BUDGET_USD` | 1 |
| `PREMIUM_TOPUP_PRICE_USD` / `PREMIUM_TOPUP_CREDIT_USD` | 5 / 4 |
| `PREMIUM_MODEL_QUICK` / `_BALANCED` / `_DEEP` | see table above |
| `PREMIUM_EFFORT_QUICK` / `_BALANCED` / `_DEEP` | low / medium / high |
| `PREMIUM_PRICE_OVERRIDES` | JSON per-model price overrides |

The Stripe webhook must send `checkout.session.completed` and `invoice.paid`. Checkout sets
`metadata.plan`; the webhook sets `users.plan_tier` from it (or from the invoice's Price).

## Before selling it

- **Margin.** A $20 plan with a $20 budget loses money on every student who uses it all:
  Stripe takes about $0.88 of the $20, and hosting and the router are on top. Most students
  won't spend the whole budget, but price for the ones who do. `PREMIUM_AI_BUDGET_USD=12`
  is a safer start, and can be raised later without a deploy.
- **Policies.** The Terms (§6a) describe only Pro. The Privacy Policy doesn't describe
  linked accounts (prompts sent to a provider the student picks, under their key). Ship a
  Terms and Privacy version through `policy_versions.py` before `BILLING_ENABLED` sells
  Premium and before `BYOK_ENABLED` is on. The in-app notice will then prompt everyone.
- **Minors.** Checkout already refuses students under 18 and sends them to the parent pay
  link, which now offers Premium too. Linking a key is refused under 18: a provider API
  account and its billing have to be in the holder's own name.
- **Provider terms for student users.** `docs/compliance/2026-10-02-policy-audit.md` item 5
  applies to Anthropic as much as to Gemini: confirm the API terms permit the audience
  before sending under-18 students' prompts to Claude on IntelliPlan's key.

## What "link your AI account" can and can't mean

The implemented linked-account route accepts **developer API keys**, billed separately
by the provider. It does not spend a ChatGPT subscription.

As checked on October 22, 2026, OpenAI supports optional eligible ChatGPT plan usage
through Sign in with ChatGPT. The public open-source flow covers open-source and
locally hosted apps. A paid or remotely hosted app such as hosted IntelliPlan needs
OpenAI's approval; it cannot reuse another application's OAuth client registration.
The AI settings page distinguishes these connections and does not offer a connection
that has not been configured and approved.

- [Plan usage availability](https://developers.openai.com/siwc/token-sharing-open-source)
- [Hosted website integration](https://developers.openai.com/siwc/website)
- [Request hosted integration access](https://openai.com/form/sign-in-with-chatgpt-interest/)

After approval, add a separate subscription adapter rather than treating OAuth tokens
as BYOK keys. The adapter needs an owned client registration and exact callback,
single-use state/nonce/PKCE bound to the signed-in IntelliPlan account, verified ID
tokens, encrypted per-user credentials, explicit granted inference scopes, revocation,
refresh, and a user-visible disconnect. Route requests through the same student,
institution, budget and safety checks as other Plani providers. Verify the real
authorization callback and a completed eligible Responses API request before enabling
the product connection. The website identity flow alone is not proof of plan usage.

An MCP server can expose authorized IntelliPlan tools to an AI host; creating one does
not itself grant access to a user's ChatGPT plan or replace the required provider grant.
