# Phase 5 — AI Memory Architecture (Foundation Only)

Not fully implemented in the MVP. This document records the architecture we are building *toward* so today's decisions don't paint us into a corner.

## What we want the AI to eventually remember

- Preferred study times of day (chronotype).
- Subjects the student struggles with.
- Subjects the student excels at.
- Procrastination patterns (which kinds of tasks slip).
- Successful study methods (what produced the best retention via `StudyMastery`).
- Long-term goals from `UserIdentity.goals`.
- Communication style preference (short/blunt vs. encouraging/long).

## Two-tier memory model

**Tier 1 — episodic signal log (`student_signals` table, ships in MVP).**

Append-only events. Cheap to write, cheap to query for the last N. Examples:

- `task_completed { course, estimated_min, actual_min, time_of_day }`
- `task_skipped { course, est_priority_score }`
- `session_finished { duration_min, subject, perceived_difficulty }`
- `priority_overridden { task_id, original_rank, new_rank }`
- `briefing_dismissed { briefing_hash }`

**Tier 2 — derived semantic profile (`student_profile_facts`, ships in v2).**

A small, dense, human-readable JSON document built by a daily aggregator from the signal log. Examples of fields:

```jsonc
{
  "productive_windows":    ["19:00-22:00", "weekend mornings"],
  "weakest_subjects":      ["Chemistry", "Spanish"],
  "strongest_subjects":    ["History", "English"],
  "avg_estimate_ratio":    1.28,        // actual / estimated, calibration factor
  "preferred_session_min": 50,
  "procrastination_tendency": "high-stakes-late-start",
  "communication_style":   "direct-encouraging",
  "last_updated":          "2026-06-11T00:00:00Z"
}
```

The aggregator is deterministic code, not an LLM. The LLM consumes the resulting document.

**Why two tiers:** raw events are unbounded and useless to prompts; the dense profile is small, stable, and cacheable. Inject only the profile into AI prompts. Keep events for analytics and re-derivation.

## v1 commitments that protect the v2 architecture

1. **All Command Center interactions write `student_signals`.** Every plan view, refresh, dismissal, completion, and override emits one row. This guarantees we have data to derive Tier 2 from when we ship it.
2. **`Assignment` is a frozen dataclass.** Tier 2 derivation will diff plans over time — comparing dicts is brittle, comparing canonical objects is not.
3. **Priority scoring components are stable keys.** `urgency`, `importance`, `grade_impact`, `effort`, `dependency`. Renaming later breaks history.
4. **No LLM judgment is stored.** We only store facts. LLM outputs (briefings) are cached but not treated as ground truth — we can always rebuild them from facts.
5. **Calibration factor is computed once, applied everywhere.** When `avg_estimate_ratio` ships, every `est_minutes` displayed multiplies by it. The single source of truth is the profile document.

## What we explicitly defer

- Vector memory / embeddings. Not needed at this scale.
- A "memory editor" UI. Not until users ask.
- Memory export / portability. Belongs with the existing `/api/snapshot`.
- Cross-user pattern learning. Privacy nightmare; do not start.

## Privacy posture

- `student_signals` is per-user, never cross-joined.
- A future "Forget me" button purges signals + profile + cache. Already implied by `_account_delete_impl` existing.
- The Tier 2 profile is opt-out via the existing `ai_personalization_opt_in` switch — when off, the profile is computed but never injected into prompts.
