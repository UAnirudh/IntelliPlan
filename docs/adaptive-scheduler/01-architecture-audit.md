# Adaptive Study Scheduler — Phase 1: Architecture Audit

Baseline commit: `55e7fcd`. Test baseline: **1295 passed** (`python -m pytest tests/`, 211s).

This document maps what IntelliPlan's scheduler actually is today, before any
change. It exists so the phases that follow can be judged against reality
rather than against a greenfield sketch.

## 1. Shape of the application

Flask monolith (`App.py`, ~16k lines) plus a clean, pure-Python package
(`intelliplan/`, 73 modules) that App.py delegates to. The package imports
nothing from Flask or the ORM in its `domain/`, `intelligence/`, and most of
`services/` layers — dependencies arrive injected through *glue* modules at
the repo root (`command_center_glue.py`, `active_glue.py`,
`learning_graph_glue.py`, `notifications_glue.py`).

| Layer | Location | Purity |
|---|---|---|
| Domain types | `intelliplan/domain/` | pure, frozen dataclasses |
| Engines | `intelliplan/intelligence/` | pure, no clock, no I/O |
| Services | `intelliplan/services/` | pure logic + injected providers |
| Repositories | `intelliplan/repositories/` | ORM-model-injected, failure-swallowing |
| Blueprints | `intelliplan/api/` | Flask, deps injected via a `*Deps` dataclass |
| Glue | `*_glue.py` (repo root) | lazy `from App import …` inside functions |
| Models | `intelliplan/models/*.py` | `register(db)` factory, idempotent |
| Migrations | `intelliplan/migrations.py` | idempotent `create_all` + `ALTER TABLE` |

**Integration rule derived from this**: new work goes in the package, is
injected through a new glue module, and is registered as a blueprint at the
bottom of `App.py` next to the others. Nothing new goes into `App.py` beyond
three lines of registration.

## 2. The scheduler as it exists

Scheduling is **already two-stage and already deterministic**. The LLM is off
the day-allocation path.

```
assignments ─► _planner_task_rows (App.py)
                   │
                   ▼
        intelligence/planner.build_plan       ← STAGE 1: which day
        (cost function + greedy seed + local search)
                   │
                   ▼
        scheduler_engine.place_day_blocks     ← STAGE 2: which clock time
        (real availability windows minus commitments, breaks, splits)
                   │
                   ▼
        services/scheduling.plan_to_schedule_data → schedule_data JSON
```

### 2.1 `scheduler_engine.py` (1252 lines) — the constraint layer

Already enforces: parsed weekly commitments, per-day availability windows,
minimum window length (`MIN_WINDOW_MINUTES = 20`), break insertion
(`LONG_BREAK_AFTER_MINUTES = 90`), oversized-block splitting, per-sitting
stamina caps derived from history (`StudyDNA`), and spill reporting when a day
cannot hold what was assigned to it.

`StudyDNA` (`build_study_dna`) is a real behavioural model already: per-slot
completion rates, weak days, estimation-bias ratio, stamina median, with
`MIN_SAMPLES_FOR_SIGNAL = 4` guarding against acting on noise.

### 2.2 `intelligence/planner.py` (1330 lines) — the optimization layer

A documented cost function (`PlannerWeights`) minimised by greedy seed plus
local search, with dependency layering (`_dependency_layers`), deadline
buffers (`buffer_days_for`), value-based triage under overload (`_triage`,
`_MARGINAL_DECAY`), and per-session `reasons` so every placement can explain
itself. `reschedule(plan, reality)` re-solves from where the student actually
is.

### 2.3 `intelligence/estimation.py` (536 lines) — the duration model

Hierarchical empirical-Bayes over log-ratios (`global → course → (course,
kind)`), 45-day half-life decay, shrinkage by sample count, and an explicit
`sigma` so every estimate carries uncertainty. This is exactly the
"learn the multiplier, don't hardcode it" requirement, and it is already
shipped.

### 2.4 Other engines already present

`priority.py` (5 capped components + reason chips), `health.py`,
`workload.py`, `sizing.py`, `decomposition.py`, `predictions.py` (GPA
trajectory, completion risk, exam readiness, burnout, Ebbinghaus forgetting),
`narrator.py` (the only AI-text path, cached in `briefing_cache`).

### 2.5 Adaptive rescheduling already exists

`services/recovery.py` + `POST /schedule/recover`: reads the saved schedule's
`progress_json`, works out what was done / abandoned / never touched
(`build_reality`), re-solves via `planner.reschedule`, diffs old vs new
(`summarise_changes`), auto-saves in place, and notifies. It explicitly does
*not* dump missed work onto the next empty slot.

## 3. Data model

Existing tables relevant to scheduling:

| Table | Purpose |
|---|---|
| `saved_schedules` | the plan JSON + `progress_json` |
| `task_feedback` | estimated vs actual minutes, difficulty, course — the estimation model's training set |
| `study_sessions` | active-study sittings |
| `student_profiles` | learning pace, strong/weak subjects, `avg_estimate_ratio`, engagement |
| `concept_mastery` | per (subject, topic, concept) mastery + confidence + decay + `last_reviewed` |
| `learning_events` | append-only learning telemetry |
| `student_signals` | append-only Command Center telemetry |
| `health_snapshots` | daily academic health |
| `briefing_cache` | cached AI text, keeps AI off the request path |
| `feature_flags` | kill switches with deterministic percentage rollout |

**No duplicate assignment representation is needed** — `AssignmentRepository`
already unifies eight upstream sources into `domain.Assignment`.

## 4. What is genuinely missing

Measured against the target ("what is the highest-value thing to do next, when,
and why"), these are the real gaps — everything else in the brief already ships:

1. **Next-Best-Action engine.** Nothing answers "what right now". The Command
   Center ranks *assignments*; it does not generate and score heterogeneous
   *actions* (study, review, practice, break, defer, continue) against the
   time actually available in this moment.
2. **Completion-probability model.** `predictions.predict_completion_risk` is
   workload-ratio based. There is no `P(complete | subject, time-of-day,
   duration, workload)` fitted per student with a population prior.
3. **Counterfactual scheduling.** One plan is produced and shipped. No
   candidate set, no comparable objective metrics across candidates.
4. **Feasibility verification as a first-class answer.** Constraints are
   enforced during construction but never re-asserted over a finished plan,
   so "is this schedule actually feasible?" has no callable answer.
5. **Schedule versioning and a decision log.** Plans are overwritten in place.
   Nothing records why a plan changed, what moved, or which objective produced it.
6. **User overrides with consequences.** No "move Physics to tomorrow → here
   is what that costs you" path.
7. **Mastery-aware method selection.** Mastery is tracked but never turned
   into "learn → guided practice → independent practice" vs "retrieval →
   targeted problems → error review".

## 5. Must-not-break list

* `/generate-schedule`, `/schedule/save`, `/schedule/update`, `/schedule/recover`
* `planner_v2` flag and its fall-back to the AI path
* `scheduler_engine.place_day_blocks` contract (Interactive View depends on the
  block dict shape)
* `/api/today` payload `schema_version: 1`
* the 1295-test suite

## 6. Safest integration points

* New engines as new modules under `intelligence/` — additive, pure, unit-testable.
* New tables via a new `register(db)` module + one idempotent migration function.
* New endpoints in a new blueprint behind a **new** flag (`adaptive_scheduler_v3`),
  registered next to the existing blueprints.
* NBA reads the *existing* planner output — it does not replace the planner.
