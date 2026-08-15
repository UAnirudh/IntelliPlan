# Claude Code Prompt — IntelliPlan Lifecycle Email System

> Paste everything below the line into Claude Code from the repo root
> (`C:\Users\uanir\StudioProjects\IntelliPlan`).

---

## Context

You are working in the IntelliPlan repo — a Flask + SQLAlchemy app deployed on Railway,
serving a student study planner at intelliplan.tech. Read `README.md` and
`docs/` before starting.

I want to build a **lifecycle email system**: three transactional/marketing emails
(welcome, feedback request, newsletter) that go out to users automatically, with
proper consent gating, unsubscribe handling, and send-deduplication.

**Important:** a lot of the plumbing already exists. Do not rebuild it. Read these
first and build on top of them:

| What exists | Where | Notes |
|---|---|---|
| `_send_email(to_addr, subject, body)` | `App.py` ~line 12813 | Resend-first, SMTP fallback. **Plain text only — no HTML support.** This is the main gap. |
| `_send_email_via_resend(...)` | `App.py` ~line 12777 | Posts `{"from","to","subject","text"}` to `api.resend.com/emails`. Needs an `html` field. |
| `User.marketing_emails_opt_in` | `App.py` ~line 502 | Boolean, default `False`. Already exists, currently unused. |
| `User.marketing_opt_in_at` | `App.py` ~line 506 | DateTime, nullable. Already exists, currently unused. |
| `User.email_reminders_opt_in` | `App.py` ~line 468 | **Transactional. Do NOT reuse this for marketing.** The comment above it explains why — respect that boundary. |
| `User.birth_year`, `parent_consent_granted` | `App.py` ~line 479-481 | COPPA gating. Critical — see the compliance section. |
| Cron auth pattern | `App.py` ~line 13668 `/cron/send-reminders` | `CRON_SECRET` env var, `X-Cron-Secret` header, `hmac.compare_digest`. **Copy this pattern exactly.** |
| Notification dispatcher | `intelliplan/notifications/`, `notifications_glue.py` | Existing channel abstraction. Read it — reuse its error taxonomy if it fits. |
| Idempotent migration pattern | `intelliplan/migrations.py` | Boot-time DDL, `inspect(db.engine)` guard. Follow this exactly. No Alembic. |
| `itsdangerous` | `requirements.txt` line 43 | Already a dependency. Use it for signed unsubscribe tokens — do not add a new lib. |
| Resend config | `.env.example` lines 85-90 | `RESEND_API_KEY`, `RESEND_FROM`. Already documented. |

Three email designs already exist as standalone HTML in `templates/emails/source/`
(`welcome.html`, `feedback.html`, `newsletter.html`). They are complete, inline-styled,
table-based, and email-client-safe. **Use them as the design source of truth** — your job
is to tokenize them, not redesign them.

---

## Non-negotiable constraints

Read this section twice. Getting these wrong creates legal exposure, not just bugs.

### 1. COPPA / under-13 gating — highest priority

IntelliPlan collects `birth_year` and has a parental-consent flow. **Marketing email to a
user under 13 without verifiable parental consent is a COPPA violation.** Additionally,
IntelliPlan is currently in a Digital Resource Review with a school district; a marketing
email landing in a district student's inbox is a materially bad outcome for that review.

Implement a single hard gate that every marketing send must pass:

```python
def is_marketing_eligible(user, now=None) -> tuple[bool, str]:
    """Return (eligible, reason). Reason is for logging when False."""
```

It must return `False` when **any** of these hold:
- `marketing_emails_opt_in` is not `True`
- `marketing_opt_in_at` is `None` (opted in with no timestamp = unevidenced consent)
- `birth_year` is `None` (unknown age → treat as a minor, do not send)
- age derived from `birth_year` is under 13 **and** `parent_consent_granted` is not `True`
- `email` is empty, malformed, or on the suppression list (see §3)
- `role` is not `"student"` — do not market to `teacher` / `parent` roles in v1

Write unit tests for this function **first**, before any sending code exists. Cover every
branch including the `birth_year is None` case. This is the function that must not have bugs.

### 2. Transactional vs marketing separation

`email_reminders_opt_in` is transactional consent (deadline reminders the student asked
for). `marketing_emails_opt_in` is marketing consent. **Never** treat one as implying the
other, in either direction. An unsubscribe from the newsletter must not stop deadline
reminders. Opting into reminders must not enroll anyone in the newsletter.

The welcome email is a grey zone. Treat it as **transactional** (it explains a service the
user just signed up for and drives setup) — but still suppress it for under-13 users
without consent, and still include an unsubscribe link.

### 3. Unsubscribe — must work before the first send

- New table `email_suppressions`: `email` (unique, indexed), `reason`, `created_at`.
  Suppression is keyed on **email address**, not user id, so a deleted-and-recreated
  account stays suppressed.
- Route `GET /email/unsubscribe/<token>` — token is an `itsdangerous.URLSafeSerializer`
  payload of `{"email": ..., "scope": "marketing"}`, signed with `SECRET_KEY`, **no
  expiry** (CAN-SPAM requires unsubscribe links to work for at least 30 days; simplest
  correct answer is forever).
- The route must work **without login**. A user who can't log in must still be able to
  unsubscribe. One click, no confirmation form, no "are you sure".
- On success: set `marketing_emails_opt_in = False`, insert into `email_suppressions`,
  render a plain confirmation page.
- Every marketing email must include a `List-Unsubscribe` header **and** a
  `List-Unsubscribe-Post: List-Unsubscribe=One-Click` header. Gmail and Yahoo require
  this for bulk senders. Extend the Resend payload with a `headers` dict to carry them.
- Physical mailing address in the footer is a CAN-SPAM requirement. Add a
  `MARKETING_POSTAL_ADDRESS` env var and render it in the footer of the newsletter and
  feedback emails. If it's unset, **refuse to send the newsletter** and log loudly —
  do not send a non-compliant email.

### 4. Send deduplication

New table `email_sends`: `user_id`, `email_key` (e.g. `"welcome"`, `"feedback_v1"`,
`"newsletter_2026_08"`), `sent_at`, `status`, `provider_message_id`.
Unique constraint on `(user_id, email_key)`.

Check-then-insert must be atomic enough that a double cron fire cannot double-send.
Insert the row **before** calling the provider, then update status on the result. A
crashed send that leaves a stale `pending` row is far better than a user getting the
same email twice.

---

## What to build

### Phase 1 — HTML email capability (foundation)

1. Extend `_send_email_via_resend(to_addr, subject, body, html=None, headers=None)`.
   Add `"html"` to the payload only when `html` is truthy. Add `"headers"` when provided.
2. Extend `_send_email(to_addr, subject, body, html=None, headers=None)` with the same
   signature. **Keep positional compatibility** — `notifications_glue.py` line ~113 calls
   `_send_email(address, row.title, body)` positionally and must keep working unchanged.
3. SMTP fallback path: when `html` is present, build a `multipart/alternative` message
   with the plain-text `body` as the fallback part. Use `EmailMessage.add_alternative`.
4. Add a test that asserts the existing 3-positional-arg call still works.

### Phase 2 — Template layer

Create `intelliplan/email/` as a proper package:

```
intelliplan/email/
  __init__.py
  templates.py     # load + render
  eligibility.py   # is_marketing_eligible + suppression checks
  sender.py        # send_lifecycle_email — the one public entry point
  campaigns.py     # welcome / feedback / newsletter logic
```

- Move the three HTML files to `templates/emails/` and convert hardcoded strings to
  Jinja variables. Flask already bundles Jinja2 — use `render_template`, do not add a
  templating dependency.
- Variables each template needs, at minimum:
  `user_name`, `app_url`, `unsubscribe_url`, `postal_address`, `preheader`.
  Newsletter additionally: `features` (list of `{tag, title, body}`), `tip`
  (`{title, body, action}`), `issue_label`.
- Every template must render correctly when `user_name` is `None` — fall back to
  "there" / "Hey there". Many users signed up via Google OAuth with no name set.
- Generate a plain-text alternative for each template. Do not ship HTML-only email;
  it hurts deliverability and breaks for text-only clients. Write these by hand — an
  auto-stripped HTML-to-text conversion reads badly.
- Add a **preheader** span (hidden preview text) at the top of each template body.
  This is the single highest-ROI deliverability/open-rate detail and all three source
  files are currently missing it.

### Phase 3 — Campaign logic

**Welcome email** (`email_key="welcome"`)
- Trigger: cron sweep, not inline on signup. Do not add a blocking HTTP call to the
  signup path — a Resend timeout must never fail a registration.
- Sweep selects users created in the last 24h with no `welcome` row in `email_sends`.
- Send regardless of `marketing_emails_opt_in` (it's transactional), but still run the
  under-13 and suppression gates.

**Feedback email** (`email_key="feedback_v1"`)
- Trigger: cron sweep, users whose `created_at` is 14–15 days ago.
- **Additional gate: only send to users with real activity.** Asking for feedback from
  someone who signed up and never returned produces noise and looks careless. Define
  activity as "has at least one connected integration OR has completed at least one
  study session" — inspect the existing models and pick the cheapest reliable query.
  Log the count of users skipped for inactivity; that number is itself a useful signal.
- Requires `marketing_emails_opt_in`.

**Newsletter** (`email_key="newsletter_YYYY_MM"`)
- **No automatic cron.** Admin-triggered only, from the existing admin surface
  (`ADMIN_EMAILS` / `/admin-x9k2p7` — read how that's guarded and match it).
- Content comes from a dict/JSON payload passed by the admin, not hardcoded.
- Must support `test: true` mode that sends only to `ADMIN_EMAILS` addresses.
- Must show a dry-run recipient count before sending. Never send to the full list
  without an explicit confirm step.
- Batch sends with a small delay between them and handle per-recipient failure without
  aborting the whole run.

### Phase 4 — Cron + admin wiring

- New route `POST|GET /cron/lifecycle-emails`, guarded with the **exact same**
  `CRON_SECRET` + `hmac.compare_digest` pattern as `/cron/send-reminders`. Do not
  invent a new auth scheme.
- Returns a JSON summary: `{"welcome": {"sent": n, "skipped": n}, "feedback": {...}}`.
- Add the Railway cron schedule to `README.md` (daily, ~16:00 UTC ≈ 9am PT).
- Add a settings-page toggle for `marketing_emails_opt_in` that writes
  `marketing_opt_in_at = datetime.utcnow()` when turned on and leaves it untouched
  when turned off. Find the existing notification-preferences endpoint
  (`notification_preferences` in `notifications_glue.py` ~line 339) and follow its shape.

### Phase 5 — Migration + env

- Add `apply_email_migrations(db)` to `intelliplan/migrations.py`, matching the existing
  idempotent style. Create `email_sends` and `email_suppressions`. Wire it into the same
  boot sequence the other migrations use.
- Add to `.env.example` with comments matching the file's existing tone:
  `MARKETING_POSTAL_ADDRESS`, and a note that `CRON_SECRET` now also guards
  `/cron/lifecycle-emails`.

---

## Testing requirements

Match the existing test style in `test_intelliplan.py` / `tests/`.

Required coverage:
1. `is_marketing_eligible` — every branch, especially `birth_year is None` and the
   under-13-with-consent case.
2. Unsubscribe token round-trip: generate → parse → suppress. Plus a tampered-token case
   that must be rejected.
3. Dedup: calling the welcome sweep twice sends exactly one email.
4. `_send_email` backwards compatibility with three positional args.
5. Template render with `user_name=None`.
6. A send attempt to a suppressed address is a no-op that returns cleanly.

Mock the Resend HTTP call. No test may hit the network or send a real email.

---

## Explicit non-goals for this PR

Do not build: open/click tracking pixels, A/B testing, a drip-sequence engine, a
template WYSIWYG editor, segmentation beyond the eligibility gate, or a
webhook handler for Resend bounce events. Those are all reasonable later; none of
them are needed to send three emails correctly, and each one adds surface area
that has to be maintained.

Do not touch: auth flows, the scheduler engine, AI provider code, or any
integration helper (`canvas_*`, `studentvue_*`, `schoology_*`, `google_calendar_*`).

---

## Working method

1. Start by reading the files listed in the context table. Report back what you found
   that contradicts my description above **before** writing code — I inspected this
   remotely and may have details wrong.
2. Propose the file-by-file plan and wait for my approval.
3. Build Phase 1 and 2, run the tests, show me the diff.
4. Then Phase 3–5.

Work in a branch. Do not commit to main. Do not run any command that sends real email.
