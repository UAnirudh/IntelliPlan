# Reporting to an operator hub

Two read-only endpoints (`orbit_export.py`) that let an operator's hub pull this
instance's adoption numbers on a schedule, so "how is IntelliPlan doing" is
answerable next to every other product the operator runs.

## Endpoints

```
GET /api/orbit/people?since=<iso>&page=<n>&limit=<n>
GET /api/orbit/events?since=<iso>&page=<n>&limit=<n>

Authorization: Bearer $ORBIT_PULL_KEY
```

Both return `{"data": [...], "page": n, "limit": n}`. Ordering is
`(created_at, id)` — a non-unique sort key would let a row shift between page 1
and page 2 and be skipped by the sync entirely. `limit` is capped at 500.

An unparseable `since` is treated as absent rather than rejected: a full page
instead of an incremental one is slower and still correct, while failing the
request would stop the sync over a formatting detail.

## What is exported

**People** — `id`, `email`, `name`, `created_at`, `role`, `plan` (`paid`/`free`),
`canvas_connected`, `streak_days`, `longest_streak`, `school_domain`.

**Events** — consented `ProductEvent` rows belonging to a signed-in user:
`id`, `user_id`, `type`, `occurred_at`, `channel`.

## What is deliberately not exported

This is most of the point. IntelliPlan's database holds data about minors, and
the export sits one HTTP request away from all of it.

| Not exported | Why |
|---|---|
| `birth_year`, `parent_email`, `phone` | COPPA-gated fields about children. No adoption question needs them. |
| Grades, test marks, imported marks | Academic records. Not analytics. |
| `stripe_customer_id`, amounts | Whether somebody pays is a number; how they pay is a payment record. |
| `ProductEvent.props` | A free-form bag whose contents vary by call site — exactly the field that will one day carry something nobody meant to send. |
| `ProductEvent.rule` | Exported as its first path segment only (`view.flashcards`), so page views stay countable without shipping internal route structure. |
| Essays, tutor conversations, notes | Student writing. |
| Events with no signed-in user | Pre-signup visitor rows are not people yet. |

The field list is an **allow-list**, not a denylist. A column added to `users`
next year must not start flowing somewhere new because nobody remembered to
exclude it.

## Configuration

```bash
ORBIT_PULL_KEY="<a long random string>"      # openssl rand -base64 32
```

**Both endpoints refuse every request when this is unset**, rather than running
open until somebody remembers to configure them. The comparison uses
`hmac.compare_digest`, and a key that is a prefix of the real one is rejected.

For pushing rather than waiting for the hourly pull, `emit_to_orbit()` in the
same module signs a payload and sends it on a background thread — fire and
forget, silent on failure, because analytics must never be able to fail a
student's request:

```bash
ORBIT_URL="https://your-orbit"
ORBIT_INGEST_SECRET="<the secret the hub printed when it onboarded this app>"
```

## Tests

```bash
pytest tests/test_orbit_export.py
```

Fourteen tests, and the ones that matter assert the boundary: closed without a
key, a near-miss key rejected, the people payload's key set exactly equal to the
allow-list, and a planted phone number, birth year, parent address, Stripe id
and essay draft all absent from the response.
