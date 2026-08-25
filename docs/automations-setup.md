# Automations setup — reminders and lifecycle email

Everything scheduled in IntelliPlan runs off HTTP endpoints guarded by a
shared secret. There is no background scheduler in the app: something
external has to call them on a timetable. Until that exists, nothing sends
— silently, because there is no error to see when nobody asks.

## 1. Environment

| Variable | Required for | Notes |
|---|---|---|
| `CRON_SECRET` | every endpoint below | Any long random string. **Unset means every cron endpoint returns 503 and nothing sends.** |
| `RESEND_API_KEY` | all email | Without it (and without SMTP) `_send_email` logs the message and reports failure. |
| `RESEND_FROM` | all email | e.g. `IntelliPlan <noreply@intelliplan.tech>`. The domain must be verified in Resend. |
| `MARKETING_POSTAL_ADDRESS` | **feedback + newsletter only** | CAN-SPAM. The marketing gate *refuses to send* without it. Welcome and onboarding are transactional and unaffected. |
| `MARKETING_REPLY_TO` | all email | Must be a mailbox that receives. Replies to a no-reply From go nowhere. |
| `SUPPORT_EMAIL` | all email | Falls back to `MARKETING_REPLY_TO`. |
| `FEEDBACK_FORM_URL` | feedback email | Defaults to the Fillout form. |
| `FEEDBACK_AFTER_DAYS` | feedback email | Defaults to `7`. |
| `APP_BASE_URL` | links in email | Must be the public https URL, or every link in every email points at localhost. |

Generate a secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## 2. Check it before wiring the schedule

There is a preflight endpoint that answers "can this actually send right
now", including asking Resend whether the sending domain is verified. Sign
in as an admin and open:

```
/api/admin/email/preflight
```

Every check returns `ok`, `warn`, or `fail` plus the single action that
fixes it. Get to no `fail` before scheduling anything.

## 3. The endpoints

All accept `GET` or `POST`, and all read the same `CRON_SECRET`. **The
header name differs by endpoint** — this is easy to get wrong and the
failure looks like an auth problem rather than a typo:

| Endpoint | Header |
|---|---|
| `/cron/notifications` | `X-Cron-Token` (403 when wrong) |
| everything else | `X-Cron-Secret` (401 when wrong) |

All of them also accept `?secret=…` as a fallback. Prefer the header —
query strings end up in access logs.

| Endpoint | Cadence | What it does |
|---|---|---|
| `/cron/notifications` | every 5–15 min | Sweeps for due events and delivers queued reminders (push, SMS, email). |
| `/cron/send-reminders` | every 15–30 min | The direct assignment-reminder path. |
| `/cron/lifecycle-emails` | daily | Welcome backstop, onboarding day 2/4/7, the one-week feedback ask, and weekly draft nudges. |
| `/cron/weekly-newsletter` | weekly, optional | Sends to the full marketing list with no human review. Leave unscheduled unless you want that. |

All four are safe to run more often than listed. Each owns its own
deduplication key, so a double fire sends nothing extra.

### `/cron/lifecycle-emails` in detail

One call runs four sweeps in order:

1. **welcome** — once per account, ever. A backstop only: the welcome now
   fires at signup, so this catches accounts created while the mailer was
   down (their ledger row is left `failed`, which is what makes them
   candidates again).
2. **onboarding** — at most one step per account per run. Day 2 "connect
   your school", day 4 "make a plan", day 7 "run a session". Each step
   checks the account's real state first and is dropped, not deferred, if
   its goal is already met.
3. **feedback** — at the one-week mark, and only for accounts with real
   activity. Points at `FEEDBACK_FORM_URL`.
4. **drafts** — at most one per account per ISO week, only when that
   account has unfinished work.

One sweep failing does not stop the others.

## 4. Firing one by hand (Windows / PowerShell)

Do this first, before scheduling anything — it proves the secret and the
header are right.

Two things bite here, and they bite together:

**`curl` is not curl.** In PowerShell `curl` is an alias for
`Invoke-WebRequest`, whose `-H` takes a hashtable, not a string. A bash
curl line pasted into PowerShell fails at parameter binding before any
request is sent:

```
Invoke-WebRequest : Cannot bind parameter 'Headers'. Cannot convert the
"X-Cron-Token: " value of type "System.String" to type "System.Collections.IDictionary".
```

Use `curl.exe` — the real binary — or a native cmdlet.

**`$CRON_SECRET` is empty on your machine.** `.env` is read by the app at
runtime; it is not exported into your shell. In PowerShell the prefix is
`$env:`, and even then it is only set if you set it. Note the empty value
after the colon in the error above — that is this, not a header problem.

The secret lives on the host (Railway/Render), so for a local test either
paste the literal value or set it for the session first:

```powershell
$env:CRON_SECRET = "paste-the-real-secret-here"
```

Then either of these works:

```powershell
curl.exe -fsS -X POST -H "X-Cron-Token: $env:CRON_SECRET" https://intelliplan.tech/cron/notifications
```

```powershell
Invoke-RestMethod -Method Post -Uri "https://intelliplan.tech/cron/notifications" -Headers @{ "X-Cron-Token" = $env:CRON_SECRET }
```

`Invoke-RestMethod` is the better one on Windows: it parses the JSON reply
into an object instead of printing a blob.

Remember the header differs per endpoint:

```powershell
curl.exe -fsS -X POST -H "X-Cron-Secret: $env:CRON_SECRET" https://intelliplan.tech/cron/lifecycle-emails
```

A healthy notifications reply looks like:

```json
{"status":"ok","swept":{"queued":0,"users":2},
 "delivered":{"sent":0,"skipped":0,"failed":0,"expired":0,"dead":0}}
```

## 5. Scheduling it

### Railway

Project → **Settings → Cron Jobs**. Railway cron runs the command in a
Linux container, so the bash form is correct there — the PowerShell caveats
above apply only to your own machine:

```bash
curl -fsS -X POST -H "X-Cron-Secret: $CRON_SECRET" https://intelliplan.tech/cron/lifecycle-emails
```

```bash
curl -fsS -X POST -H "X-Cron-Token: $CRON_SECRET" https://intelliplan.tech/cron/notifications
```

Schedules: `*/10 * * * *` for notifications, `*/20 * * * *` for reminders,
`0 14 * * *` for lifecycle emails (14:00 UTC — pick an hour that lands
mid-afternoon for most of your users, not 3am).

### Render

**Cron Jobs** → new job, same curl command, same schedules.

### Anything else (cron-job.org, EasyCron, GitHub Actions, system crontab)

Any scheduler that can make an authenticated HTTP request works. With
system cron:

```bash
*/10 * * * * curl -fsS -X POST -H "X-Cron-Token: REDACTED"  https://intelliplan.tech/cron/notifications
*/20 * * * * curl -fsS -X POST -H "X-Cron-Secret: REDACTED" https://intelliplan.tech/cron/send-reminders
0 14 * * *   curl -fsS -X POST -H "X-Cron-Secret: REDACTED" https://intelliplan.tech/cron/lifecycle-emails
```

## 6. Verifying

Each endpoint returns a JSON summary of what it did:

```json
{"status":"ok","welcome":{"sent":0,"skipped":3,"failed":0,"reasons":{"already_sent":3}},
 "onboarding":{"sent":1,...},"feedback":{"sent":0,"skipped_inactive":2,...}}
```

`reasons` is where to look when something did not send. Common ones:

| Reason | Meaning |
|---|---|
| `already_sent` | The ledger has it. Working as intended. |
| `unknown_age` | No birth year on the account — the gate treats unknown age as a minor and refuses. |
| `under_13_no_parental_consent` | Waiting on the COPPA consent link. |
| `no_marketing_consent` | Feedback and newsletter only. The user never opted in. |
| `no_postal_address` | `MARKETING_POSTAL_ADDRESS` is unset. |
| `suppressed` | The user unsubscribed or a send hard-bounced. |
| `provider_failed` | Resend rejected it or is unreachable. The row stays `failed` and the next run retries. |

A 503 means `CRON_SECRET` is unset. A 401 (or 403 on `/cron/notifications`)
means the secret or the header name does not match — see the table above.
