# Phase 6 — Implementation Roadmap

Six milestones. Each is independently shippable and behind the feature flag `command_center_enabled` (uses the existing `FeatureFlag` table — no flag, no exposure).

Each milestone ends with: code, tests, manual smoke on `/command-center?ff=on`, and a commit on `feat/command-center`. Nothing on the existing dashboard is touched.

---

## M1 — Foundation (domain types + repos + new tables)

**Goal:** the data plumbing exists. Nothing user-visible yet.

- New package `intelliplan/` with `__init__.py`.
- `intelliplan/domain/assignment.py` — `Assignment` frozen dataclass: title, course, due_date, points_possible, status, est_minutes, source, source_ref.
- `intelliplan/domain/plan.py` — `PlannedTask`, `PriorityScore`, `ReasonChip`, `TodayPayload`, `BriefingText`, `DayLoad`, `HealthScore`.
- `intelliplan/repositories/assignments.py` — `AssignmentRepository.for_user(user_id, today)` reads `ManualTask`, `ImportedGrade`, and the unified-tasks pipeline, returns `list[Assignment]`.
- `intelliplan/models/command_center.py` — `BriefingCache`, `HealthSnapshot`, `StudentSignal` ORM models.
- `App.py` imports the models at module load (so `db.create_all()` picks them up); calls a new `apply_command_center_migrations()` idempotent function next to `apply_study_schema_migrations()`.
- Unit tests in `tests/intelliplan/` for the dataclasses + repo (using SQLite + the existing `test_intelliplan.py` style).

**Done when:** `pytest tests/intelliplan` is green and `flask shell` can `AssignmentRepository.for_user(1, date.today())` against a seeded DB.

---

## M2 — Intelligence engine (pure functions)

**Goal:** deterministic priority, workload, health — no AI, no DB.

- `intelliplan/intelligence/priority.py` — `compute(assignment, context) → PriorityScore` per the algorithm in Phase 1.
- `intelliplan/intelligence/workload.py` — `forecast(assignments, availability, today) → list[DayLoad]`.
- `intelliplan/intelligence/health.py` — `compute(snapshot) → HealthScore`.
- Each module is pure: inputs are dataclasses, outputs are dataclasses, no `db.session`, no `os.getenv`, no `datetime.utcnow()` *inside* the function (caller passes `today`).
- Property-based tests: priority is monotone in urgency; total score in [0, 100]; same input → same output; explainer covers all components; clamping works.

**Done when:** `pytest tests/intelliplan/intelligence` ≥95% coverage and the engines are usable from `flask shell`.

---

## M3 — Narrator + `BriefingService` + cache

**Goal:** AI layer is wired and cached.

- `intelliplan/intelligence/narrator.py` — `brief(payload) → BriefingText`. Uses `ai_provider.chat_json` with a strict response schema. Falls back to a templated rendering when AI is unavailable.
- `intelliplan/services/briefing.py` — `BriefingService.get_or_refresh(user, today_payload)` — hashes the payload, reads `BriefingCache`, refreshes if stale.
- `intelliplan/services/today.py` — `TodayService.build(user, now)` — assembles plan, forecast, health, then calls `BriefingService`, returns `TodayPayload`.
- All signal-emitting points (refresh, dismiss, complete) write `StudentSignal` rows via `intelliplan/repositories/signals.py`.

**Done when:** `TodayService.build` returns a complete payload in a Flask test client request, and the second call within 6 hours skips the AI call (verified via mock).

---

## M4 — API endpoints

**Goal:** `/api/today`, `/api/today/explain`, `/api/today/refresh`, `/cron/refresh-briefings`.

- New blueprint `intelliplan/api/command_center.py` (`command_center_bp`).
- Registered in `App.py` next to `auth_bp` and `chatbot_bp`.
- Honors the existing auth helpers (`is_logged_in`, `current_user`, extension token path).
- Feature-flag gate: returns 404 if `feature_enabled("command_center_enabled")` is False — completely invisible until flipped on.
- Per-route rate limits via `flask-limiter` as specified in Phase 3.

**Done when:** all four endpoints respond correctly under integration tests, including auth, rate limits, and the AI-down 503 fallback.

---

## M5 — Command Center UI

**Goal:** `/command-center` page renders the full surface.

- New route `command_center_page()` in the blueprint.
- New template `Main_Project/templates/command_center.html` extending the existing base.
- New stylesheet `static/css/command_center.css`.
- HTMX 1.9.x + Alpine.js 3.x loaded from `static/vendor/` (self-host, no CDN — CSP-friendly).
- `[why?]` popovers fire HTMX requests against `/api/today/explain`.
- Refresh button fires `POST /api/today/refresh` and swaps the briefing card.
- Service worker entry added for stale-while-revalidate.

**Done when:** the page renders correctly for a seeded user, mobile Lighthouse score ≥ 90 on Performance and Accessibility, and the surface gracefully degrades when AI is unavailable.

---

## M6 — Launch + measurement

**Goal:** controlled rollout and learning loop in place.

- Feature flag flipped to True for the founder account first.
- After 1 day of dogfooding, flipped on for all logged-in users.
- New cron Railway schedule: `*/30 * * * *` → `POST /cron/refresh-briefings`.
- Analytics events: `command_center.opened`, `briefing.refreshed`, `task.priority_overridden`, `briefing.dismissed`. All flow into `student_signals`.
- A new section on `/admin` shows: cache hit ratio, average payload build time, AI fallback rate, top "why?" components — operability without standing up new dashboards.

**Done when:** Command Center is live, the cron is refreshing caches, and a week of `student_signals` is on disk to start designing the v2 derived-profile work.

---

## Sequencing

```
M1 ──► M2 ──► M3 ──► M4 ──► M5 ──► M6
       └────────────► (M2 unblocks rich tests for M3)
                      (M3 unblocks API contract for M4)
```

No parallelism inside this branch — each milestone is small enough (1–2 days each) and the layering is strict.

## What is explicitly deferred and tracked

Each item is filed as a candidate background task (`mcp__ccd_session__spawn_task` equivalent) once the MVP ships:

- Extract the 33 ORM models out of `App.py` into `intelliplan/models/`.
- Replace boot-time DDL (`apply_study_schema_migrations`) with Alembic.
- Encrypt `LinkedAccount.credentials` (Fernet, key from env).
- Delete `AppStore-Launch/IntelliPlan-Full-Package/Source-Code/` after confirming nothing references it.
- Shared OAuth state helper.
- Redis + RQ for AI generation and LMS sync.
- Replace synchronous LMS pulls inside the dashboard request path.
- Derived-profile (Tier 2 memory) aggregator.

## Definition of done for the whole branch

- All six milestones shipped and behind the feature flag.
- Command Center page passes the five-second comprehension test on a fresh seeded account.
- No existing route or template touched.
- `pytest` green; coverage on `intelliplan/` ≥ 85%.
- Founder dogfooded for 7 days.
- A short post-mortem appended to `docs/command-center/99-post-launch-notes.md`.
