# Phase 2 — Database Design

Three new tables. All additive. No changes to existing tables.

## `briefing_cache`

Stores the rendered AI briefing per user so AI is never on the request path.

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `user_id` | int FK → users.id, indexed | one row per user (UNIQUE) |
| `payload_hash` | varchar(64) | sha256 of the canonical TodayPayload — refresh trigger |
| `text_json` | text | `{ "headline": str, "body": str, "tone": str }` |
| `model` | varchar(64) | which AI model produced it (telemetry) |
| `generated_at` | datetime | UTC |
| `expires_at` | datetime | UTC, hard-cap 6h after `generated_at` |

Indexes: `UNIQUE (user_id)`, `(expires_at)` for the cron sweeper.

Lifecycle:
- Written by `BriefingService.refresh(user)`.
- Read by `/api/today` and `/command-center`.
- Invalidated when `payload_hash` no longer matches a freshly built `TodayPayload`.
- Background cron `/cron/refresh-briefings` refreshes any row with `expires_at < now()`.

## `health_snapshots`

Daily denormalized record of each component of the Academic Health Score. Drives day-over-day deltas without recomputing yesterday's state.

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `user_id` | int FK → users.id, indexed | |
| `snapshot_date` | date | UTC, `(user_id, snapshot_date)` UNIQUE |
| `score` | int | 0..100 |
| `overdue_count` | int | |
| `high_stakes_soon_count` | int | due in ≤3d, no progress |
| `failing_courses_count` | int | |
| `declining_courses_count` | int | trend over last 3 grades |
| `completion_rate_7d` | float | 0..1 |
| `schedule_balance_index` | float | 0..1 |
| `components_json` | text | full structured breakdown for the explainer |
| `created_at` | datetime | |

Indexes: `UNIQUE (user_id, snapshot_date)`, `(user_id, snapshot_date DESC)` for "latest 7" lookups.

## `student_signals`

Append-only event log for future personalization (procrastination tendencies, productive time-of-day, completion velocity). Foundation for the AI memory system.

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `user_id` | int FK → users.id, indexed | |
| `kind` | varchar(48) | enum-like: `task_completed`, `task_skipped`, `session_finished`, `briefing_seen`, `briefing_dismissed`, `priority_overridden`, `late_submit`, … |
| `subject_type` | varchar(32) | `assignment` \| `session` \| `briefing` \| `course` |
| `subject_ref` | varchar(255) | natural key (assignment title hash, session id, etc.) |
| `value_json` | text | small JSON payload (estimated vs. actual minutes, course, etc.) |
| `occurred_at` | datetime | UTC, indexed |

Indexes: `(user_id, occurred_at DESC)`, `(user_id, kind, occurred_at DESC)`.

Retention: never delete in v1 — events are small. We can age out >1y later via cron.

## Why these three and not more

- **No new "Assignment" table.** Assignments are read live from the existing upstream tables (`ManualTask`, `ImportedGrade`, LMS caches via `_compute_priority`'s siblings). The new `AssignmentRepository` *unifies* them in code, not in storage. Premature centralization would conflict with the existing sync paths.
- **No new "Plan" table.** A day's plan is derived from assignments + signals at read time. Caching the plan would make priority changes invisible until the next refresh.
- **No new "Productivity" table.** `StudySession`, `TaskFeedback`, `StudyMastery` already exist. The new `ProductivityRepo` reads them.

## Migration safety

Schema additions follow the existing pattern: `db.create_all()` at boot will create the three tables. The new `apply_command_center_migrations()` function does a defensive `CREATE TABLE IF NOT EXISTS` for the rare case where `db.create_all()` is skipped — runs once per boot, idempotent.

**Tracked debt:** before any future destructive change, replace the boot-time DDL pattern with Alembic. Recorded in `99-roadmap.md`.
