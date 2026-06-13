# Phase 0 — Architecture Audit

> Captured 2026-06-11 on branch `feat/command-center`.
> Authoritative record of the state of the codebase before the AI Daily
> Command Center work begins.

## Stack

| Layer | Implementation |
|---|---|
| Web | Flask, gunicorn (4 workers, `--max-requests 500`) |
| ORM | SQLAlchemy 2 + flask-sqlalchemy |
| Auth | flask-login + flask-bcrypt + custom JWT (`auth_api.verify_token`) for extension |
| Sessions | flask-session w/ SQLAlchemy backend (survives Railway restarts) |
| AI | `ai_provider.py` — Gemini primary, Groq fallback, tiered (standard / fast / vision) + Whisper |
| Sources | StudentVue (SOAP), Canvas (OAuth + token), Schoology, Google Classroom, Blackboard, Moodle, Notion, Google Calendar, manual + CSV import |
| Frontend | Server-rendered Jinja templates in `Main_Project/templates/`, service worker, Chrome extension |
| Deploy | Railway, Postgres via `DATABASE_URL` (SQLite local) |
| Ops | Sentry, flask-limiter, Stripe, web-push (VAPID), Twilio + SMS-over-email gateway |

## Module shape

```
App.py                10,569 lines  ← models + 230 routes + business logic + AI prompts + helpers
chatbot_api.py         1,046 lines  blueprint
auth_api.py              446 lines  blueprint
studentvue_helper.py     528 lines  SOAP + categorical priority compute
canvas_helper.py         296 lines
canvas_oauth.py / canvas_routes.py
google_calendar_helper.py / notion_helper.py / schoology_helper.py
ai_provider.py           316 lines  unified Gemini/Groq facade — clean
intelliplan_api.py       253 lines  public API
intelliplan_mcp.py       206 lines  MCP server
```

## Strengths

1. **`ai_provider.py` is already clean** — tiered models, vendor swap via env. Only piece of the codebase that looks "founding engineer." Keep.
2. **Priority compute is already partially deterministic** (`studentvue_helper._compute_priority`). Right instinct, wrong shape (categorical, 4 magic thresholds).
3. **`/api/snapshot` is the embryo of the Command Center** — already unifies `nextEvent | deadlines | reviewQueue | streak | todayGoal`. Will be extended, not rebuilt.
4. **`UserIdentity` already collects** grade, focus areas, availability, weekly commitments, class schedule.
5. **AI personalization is opt-in by default** (`ai_personalization_opt_in=False`).
6. **Multi-LMS coverage is real** — the hard integration work is done.
7. **Ops basics in place** — Sentry, gunicorn recycle, rate limiting, ProxyFix, secure cookies, COPPA gating.

## Critical issues

- **`App.py` is a 10,569-line monolith.** Models, 230 route handlers, OAuth flows for 7 providers, gamification rules, AI prompt construction, SMS gateway, indexnow SEO, admin, and the Lotus snapshot builder all in one file. Blueprints exist for 2 modules only.
- **No Alembic.** Schema evolves via `apply_study_schema_migrations()` and `ALTER TABLE … IF NOT EXISTS` strings at boot. Unsafe under 4 concurrent Railway workers on Postgres.
- **`AppStore-Launch/IntelliPlan-Full-Package/Source-Code/` is a duplicate of the entire codebase.** Two sources of truth, drifting.
- **No domain types.** Assignments arrive as untyped dicts from 8 upstream shapes. Every helper re-parses raw dicts and re-derives `due_date`, `points_possible`, `course`. Major bug surface — and the #1 reason a deterministic priority score is hard to build.
- **No unified "today" endpoint.** Dashboard fan-out to ~10 routes per page load.
- **AI / code separation is leaky.** Scheduling and time prediction reach for the LLM where arithmetic over `ManualTask`, `StudySession`, and `availability` would be deterministic and free.
- **No memory / personalization store.** `build_student_context` is a prompt serializer, not a learning record.

## Security flags (pre-implementation only — not exhaustive)

1. `SECRET_KEY` defaults to `"intelliplan-dev-key"` if env unset (`App.py:111`). Should refuse boot in prod.
2. Each of the 7 OAuth callbacks re-implements state generation/validation. No shared helper.
3. `ExtensionToken.token` is long-lived bearer in plain text.
4. **`LinkedAccount.credentials` stores StudentVue passwords as a JSON string column.** Highest-risk piece of data in the system. Encrypt at rest.
5. CSV + smart-paste import take free-form text that feeds AI prompts. Prompt-injection surface.
6. No CSP visible.
7. Assignment titles come from teachers — sanitize before LLM injection.

## Scalability / performance

- Every gunicorn worker imports Stripe, genai, google-api, notion at boot.
- Hand-rolled boot DDL races between workers.
- Synchronous LMS fetches inside request handlers → dashboard latency = slowest LMS.
- No caching layer (no Redis, no `flask-caching`). Every dashboard render re-hits upstream LMS.
- AI calls synchronous in request path. Even Gemini Flash adds 800ms–3s.

## Recommendations carried into Phase 1

The Command Center delivery includes, as additive work that does not touch the existing routes:

1. New `intelliplan/` package — domain types, intelligence engine, repositories, narrator, today service.
2. Deterministic `PriorityEngine` returning 0–100 score with structured rationale.
3. `WorkloadForecaster` and `AcademicHealthScore` as pure functions.
4. `BriefingCache` table + refresh cron, so AI is never on the request path.
5. New `/api/today` returning the full Command Center payload in one call.
6. New `/command-center` Jinja page + HTMX + Alpine.js — premium, mobile-first, no React build pipeline.

Deferred but tracked:
- Alembic migrations (required before next destructive schema change).
- Extract 33 models out of `App.py`.
- Delete `AppStore-Launch` duplicate copy.
- Encrypt `LinkedAccount.credentials`.
- Background worker (RQ / Redis) once cron-only refresh shows strain.
