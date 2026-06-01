# IntelliPlan — Environment Variables

Every config value the app reads. In production these are set in the **Railway dashboard**;
locally they go in a `.env` file (copy `Source-Code/.env.example` → `.env` and fill in).

> ⚠️ The real values are **not** in this package on purpose. This is a reference for what each
> variable is and where to get it. Ask the owner for the actual secrets only if you need to run
> the backend.

---

## Core

| Variable | Required | What it is |
|---|---|---|
| `SECRET_KEY` | ✅ | Long random string for Flask session signing. Generate any 32+ char random string. |
| `APP_BASE_URL` | ✅ | Public base URL. Production: `https://intelliplan.tech`. Local: `http://localhost:3000`. |
| `DATABASE_URL` | ✅ | DB connection. Production: Postgres URL (Railway sets it). Local: `sqlite:///intelliplan.db`. |
| `PORT` | — | Port to serve on (default 3000). Railway injects this automatically. |

## Admin

| Variable | Required | What it is |
|---|---|---|
| `ADMIN_EMAILS` | — | Comma-separated emails allowed into the admin panel. |
| `ADMIN_PATH` | — | The (obscured) admin route path. |

## AI — Groq

| Variable | Required | What it is |
|---|---|---|
| `GROQ_API_KEY` | ✅ (for AI features) | API key from https://console.groq.com. Powers the scheduler, tutor, chatbot, and study generation. |

## Google OAuth + Calendar

| Variable | Required | What it is |
|---|---|---|
| `GOOGLE_CLIENT_ID` | — | From Google Cloud Console (OAuth credentials). |
| `GOOGLE_CLIENT_SECRET` | — | From Google Cloud Console. |
| `GOOGLE_REDIRECT_URI` | — | `https://intelliplan.tech/oauth2callback` (or local equivalent). |

## Notion OAuth

| Variable | Required | What it is |
|---|---|---|
| `NOTION_CLIENT_ID` | — | From a Notion public integration. |
| `NOTION_CLIENT_SECRET` | — | From the Notion integration. |
| `NOTION_REDIRECT_URI` | — | `https://intelliplan.tech/oauth/notion/callback`. |

## Canvas OAuth

Canvas has no global OAuth provider — each Canvas instance issues its own Developer Keys.
Register one at `https://canvas.instructure.com` (full instructions are in `.env.example`).

| Variable | Required | What it is |
|---|---|---|
| `CANVAS_CLIENT_ID` | — | Developer Key ID. |
| `CANVAS_CLIENT_SECRET` | — | Developer Key secret. |
| `CANVAS_REDIRECT_URI` | — | `https://intelliplan.tech/oauth/canvas/callback`. |
| `CANVAS_DEFAULT_BASE` | — | `https://canvas.instructure.com`. |

## Stripe (IntelliPlan Pro billing — web only)

> Note for the App Store: **no Stripe paywall is shown inside the iOS app** (Apple requires
> In-App Purchase for in-app digital goods). Billing is web-only. See the release guide.

| Variable | Required | What it is |
|---|---|---|
| `STRIPE_SECRET_KEY` | — | `sk_live_...` from the Stripe dashboard. |
| `STRIPE_PUBLISHABLE_KEY` | — | `pk_live_...`. |
| `STRIPE_PRICE_ID` | — | Recurring price ID for Pro ($4.99/mo). |
| `STRIPE_PRODUCT_ID` | — | Pro product ID. |
| `STRIPE_WEBHOOK_SECRET` | — | `whsec_...` for verifying webhook events. |

## Email — Resend (preferred)

| Variable | Required | What it is |
|---|---|---|
| `RESEND_API_KEY` | — | From https://resend.com/api-keys. |
| `RESEND_FROM` | — | Verified sender, e.g. `IntelliPlan <noreply@intelliplan.tech>`. |

## Email — SMTP fallback (optional)

Used only if `RESEND_API_KEY` is not set.

| Variable | What it is |
|---|---|
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` | Standard SMTP settings. For Gmail use an App Password. |

## SMS

No third-party SMS API. Texts are delivered by emailing the recipient's carrier gateway (the
user picks their carrier in Settings), reusing whichever email path above is configured.

## Push notifications (VAPID)

| Variable | What it is |
|---|---|
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` / `VAPID_EMAIL` | Web-push keys. Generate with `vapid.py`. |

## Error monitoring

| Variable | What it is |
|---|---|
| `SENTRY_DSN` | Optional Sentry project DSN for error tracking. |
