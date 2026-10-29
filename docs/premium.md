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

## AI incident response

`GET /health` verifies application availability and schema state; it does not
prove that an AI provider is configured or reachable. A tutor `ai_unavailable`
response means there was no permitted provider or every provider in the allowed
fallback chain failed. A separate `ai_quota_exhausted` response means configured
providers were attempted but all reported depleted quota. Review the runtime
logs and the service's configured variable *names* in the deployment console;
never paste secret values into a terminal transcript, ticket, or chat.

For a local credential check, run the bounded synthetic-account workflow from
the repository root:

```powershell
python scripts/verify_plani_provider.py --run-live
```

It uses a disposable database and an adult synthetic account, and sends at most
three synthetic questions to providers configured in the local environment.
Use `--env-file` only with an existing protected environment file; the script
reads provider settings without printing their values. It does not contact or
diagnose the deployed production runtime. The generated report confirms a
locally configured provider response, not student-specific linked-key health,
live quota at the time of a later request, or production readiness.

The server free-model ladder needs a configured Groq key for its supported
default path. Gemini stays excluded for this student-facing service unless an
express written contractual exception and the required Vertex configuration
are in place. Do not flip `GEMINI_MINORS_CONTRACT_APPROVED` as a troubleshooting
shortcut. Claude on IntelliPlan's key is restricted to paid-plan usage; a
student's own linked provider remains separately authorized by that student.

After changing provider configuration, redeploy the web service and verify a
synthetic request, quota behavior, and the runtime fallback logs. If local
preflight returns `blocked_no_permitted_provider`, first restore the provider
credential/configuration through the owner-controlled deployment console; a
healthy `/health` response alone will not restore the tutor.

## What "link your AI account" can and can't mean

The implemented linked-account route accepts **developer API keys**, billed separately
by the provider. It does not spend a ChatGPT subscription.

As checked on October 27, 2026, OpenAI separates Sign in with ChatGPT for websites
from ChatGPT plan usage in open-source/local apps. The website flow is currently
available to selected commercial partners through a limited trial. ChatGPT plan
usage in an open-source app uses a separate user-authorized client-registration
flow; its loopback callback is not a hosted SaaS callback. Hosted IntelliPlan needs
OpenAI's approval and its own registered client. It cannot reuse another
application's OAuth client registration.

| Hosted website value | Current IntelliPlan state |
|---|---|
| Application callback URI: `https://intelliplan.tech/auth/openai/callback` | Displayed on `/settings/ai` so the owner can include the exact URL in the OpenAI application/client request; no callback route is implemented. |
| Sign-in start route: `GET /auth/openai` | Not implemented. |
| Callback route: `GET /auth/openai/callback` | Not implemented. |
| OAuth client ID, registered redirect URI, and client authentication method | Must come from OpenAI after the hosted application is accepted. |

The owner may submit this exact callback URI with the application/client request;
OpenAI's website guide says an exact registered callback URL is required for each
environment. The URI is not a working integration: IntelliPlan has not implemented
the start or callback routes, and users cannot connect. After OpenAI approves the
hosted application and issues its client configuration, implement the routes and
confirm the deployed callback is registered exactly. The requested `redirect_uri`
and the registered URL must match byte-for-byte. OpenAI's
local/open-source plan-use guide shows a loopback callback such as
`http://127.0.0.1:1455/auth/callback`; that URL belongs to a locally hosted client
and must not be substituted for IntelliPlan's hosted callback.

The AI settings page distinguishes developer API keys (which bill separately)
from eligible ChatGPT plan usage and does not offer a connection that has not been
configured and approved.

- [Open-source/local plan usage and its client flow](https://developers.openai.com/siwc/token-sharing-open-source)
- [Hosted website integration and limited partner availability](https://developers.openai.com/siwc/website)
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
