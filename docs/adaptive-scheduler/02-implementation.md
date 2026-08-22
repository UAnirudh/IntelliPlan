# Adaptive Study Scheduler — Implementation

Companion to [01-architecture-audit.md](01-architecture-audit.md), which
records what the scheduler was before this work. This document records what
was added, why each piece exists, and what is deliberately still missing.

Tests: **1588 passing** (baseline was 1295 — 293 new, zero regressions).

## 1. What was added, and what was left alone

The audit found that IntelliPlan already had a deterministic two-stage
scheduler, a hierarchical duration model, a five-component priority engine,
and a real recovery path. None of that was rewritten. The work here fills the
seven gaps the audit named.

| Gap | Module | Kind |
|---|---|---|
| Completion behaviour | `intelligence/behavior.py` | new engine |
| Feasibility as an answer | `intelligence/constraints.py` | new engine |
| Counterfactual plans | `intelligence/counterfactual.py` | new engine |
| Next-Best-Action | `intelligence/nba.py` | new engine |
| Method selection | `intelligence/methods.py` | new engine |
| Overrides with consequences | `intelligence/overrides.py` | new engine |
| Reasoning layer | `intelligence/reasoning.py` | new engine |
| Student-model types | `domain/student.py` | new types |
| Composition root | `services/next_action.py` | new service |
| Audit trail | `models/scheduler_decisions.py`, `repositories/schedule_audit.py` | new tables |
| API | `api/next_action.py`, `next_action_glue.py` | new blueprint |
| Today card | `static/css/next_action.css`, `static/js/next_action.js` | new UI |

Changes to existing files are three lines of blueprint registration, one
migration call, one flag entry, `feature_enabled_for_user`, a
`plan_candidates` method on `SchedulingService`, and two hooks in
`_build_planner_schedule` / the recovery route.

## 2. The behavioural model

`behavior.py` answers *will this student actually finish this sitting?* —
the half of the question `estimation.py` does not.

Beta-Binomial over decay-weighted history, with one non-obvious detail that
is load-bearing: the parent an estimate shrinks toward is the global tally
**with the child slice subtracted out**. A course's sittings are also in the
global tally, so shrinking toward the raw global estimate counts the same
sittings twice. An earlier version of this module did exactly that, and a
single abandoned session dropped a student's odds from 62% to 40%. There is a
regression test (`test_one_observation_nudges_rather_than_declares`).

Everything it exposes carries an `Evidence` record — source (`measured` /
`partial` / `population` / `stated`), effective sample count, and confidence.
Nothing in the product may present a population prior as a measurement.

Sitting length is a **learned** multiplier, not a hardcoded penalty: it is
`p(bucket) / p(everything else)`, which is 1.0 by construction when there is
no evidence.

## 3. Next-Best-Action

Two separable halves, so each is testable alone:

* **Generation** — deliberately heterogeneous. Planned blocks, overdue work
  the plan missed, weak concepts before an assessment, decaying material, a
  break. A generator that can only produce "do more work" cannot recommend a
  break when a break is right.
* **Scoring** — one documented linear objective (`NBAWeights`) over
  normalised components. Every contribution rides along on the result, so
  "Why this?" shows the arithmetic rather than a story told afterwards.

Weights are a constructor argument, not module constants, so they can be
personalised later without touching the engine.

Reason codes are stable keys; the sentences live in exactly one table
(`nba.REASON_LABELS`) and nothing downstream writes its own version. A test
asserts every code a decision can produce has a label.

## 4. Counterfactuals

Four objective profiles (balanced / deadline-safe / retention-first / lighter
days), each a full planner run, each scored on the *same* metrics
independently of the weights that produced it. The winner is the best
**feasible** candidate — vetoed on hard violations only, because a soft
violation is a plan that is merely worse and refusing those would mean
shipping nothing in an overloaded week.

Cost: ~36 ms for four candidates over 40 tasks and 14 days, against ~8 ms for
one. Measured, not assumed.

## 5. Feasibility

`constraints.check_schedule` re-asserts the constraints over a *finished*
plan — overlaps, work after its own deadline, calendar conflicts, work
outside stated availability, date mismatches, unbroken runs, over-capacity
days, blocks in the past. Hard violations mean the plan cannot be executed;
soft ones mean it should not be.

It never repairs anything. A checker that silently fixes what it finds is a
checker whose findings you never see.

## 6. Overrides

Applied **literally** — the move the student asked for, not a
re-optimisation. Re-solving the horizon after "move this to tomorrow"
produces a plan the student did not ask for and makes the consequence report
describe the optimiser's changes rather than theirs.

The report always has both a gain list and a cost list. One with only costs
reads as the app arguing with the student; one with only gains is not a
report. `accepted_possible` is always `True` — the student is never blocked,
only told the price.

## 7. The LLM boundary

`reasoning.py` is the only new module that talks to a model, and it never
chooses, scores, or judges anything. It receives the decision and its reason
codes and rephrases them.

Enforced in code, not in prompt wording:

* the state handed to the model is an **allow-list** (`build_state`) — a test
  asserts the exact key set, so a field added to the decision object fails
  the test instead of quietly shipping to a third party;
* a decision below 0.6 confidence does not hand over its probability at all;
* returned reason codes are validated against the ones supplied, and
  hallucinated ones are dropped;
* an over-long answer is rejected rather than truncated into looking
  compliant;
* every failure path — no opt-in, no key, bad JSON, invented codes — lands on
  the deterministic sentence built from the same reason codes.

The AI narration is a **separate endpoint** from the recommendation. The card
renders from deterministic data immediately; the narration arrives after, and
if it never does the student still has the grounded reason list.

## 8. Versioning and observability

`schedule_versions` records every plan a student has been shown, what
triggered it, which objective won, how many candidates were compared, the
selected metrics, feasibility, and what moved. `schedule_decisions` records
every recommendation *and every runner-up* with its rank, plus whether the
student took it.

`accepted` is tri-state via nullability: `None` means "shown, not yet
answered", which is not the same as "rejected". Resolution is time-scoped so
Thursday's action cannot retroactively accept Monday's card.

**Privacy:** neither table stores assignment titles, descriptions, or grades
— task ids, course names (already on every screen), and numbers. The plan
itself already lives in `saved_schedules` under the same access controls, and
this audit trail must not become a second, less guarded copy of it.

## 9. Rollout and rollback

Flag `adaptive_scheduler`, seeded **enabled at 10%** via `_FLAG_OVERRIDES`.
`feature_enabled_for_user` buckets by a deterministic hash of
`(key, user_id)`, so a student stays on the same side across sessions.

* Outside the cohort every new route 404s, and `_choose_plan` falls straight
  through to the existing single-plan path.
* Widening or killing it is one admin-panel edit; no deploy.
* Removing the blueprint entirely would leave the scheduler, Command Center,
  and recovery flow behaving exactly as they did before.

Migrations are additive `create_all` only — no existing table is altered and
no production data is touched.

## 10. Performance

Measured on a heavy-but-realistic week (40 tasks, 14 days, 120 sittings of
history):

| Operation | Measured | Budget |
|---|---|---|
| Fit behavioural model | 3.0 ms | — |
| Next-best-action | 2.1 ms | 1000 ms |
| Single plan | 7.9 ms | 2000 ms |
| Four candidates, scored | 35.9 ms | 2000 ms |

The engines are nowhere near the budget; the cost in production is loading
the student's data, which is why every load sits behind an injectable
provider the caller can cache.

## 10a. Accessibility

The card is covered by [../accessibility/POLICY.md](../accessibility/POLICY.md)
and by the contracts in `tests/test_accessibility_contract.py`, which this
work extended from 4 checks to 8 — adding button accessible names, live
regions that must not contain controls, `aria-controls` targets that must
exist, and disclosure buttons that must declare `aria-expanded`.

Those new contracts immediately found three pre-existing defects, all fixed:
an icon-only "New conversation" button with only a `title`, and two
`role="alert"` containers (Command Center's error banner, the scheduler's
generate error) that wrapped their retry buttons — so a screen reader
announced the button label as prose and focus never moved there. In both the
role moved onto the message span.

Contrast was measured rather than eyeballed, which found two failures in the
new card: `--warn-text` at 0.68rem was 3.73:1 on the light card, and white on
`var(--accent)` was 3.03:1 in dark mode. Both fixed. The second is a
**pre-existing app-wide pattern** — `color: #fff` on the accent is hardcoded
at ~20 other call sites and fails in all six dark themes, bottoming out at
1.74:1. `next_action.css` pairs `--accent` with `--bg-card` instead, which
measures ≥5.02:1 across all 13 shipped themes; the sweep is logged as open in
the policy.

## 10b. What driving it in a browser found

The engines were unit-tested and the API was covered before any of this ran
in a real page. Running it anyway found five defects that tests had not,
which is the argument for doing it:

1. **The same assignment appeared twice** — as the headline and as the first
   "next" item. The plan block and the assignment row are the same work with
   ids minted by different ingest paths, and the dedupe matched only on id.
   The unit test used the same id on both sides, so it passed.
2. **Reason ordering was arbitrary** — "there is not much of today's study
   time left" led, ahead of "this is due tomorrow", because the scorer
   appends reasons in the order it computes them. Ordering by how the
   arithmetic runs is not ordering. There is now an explicit rank table, and
   a test that every reason code appears in it.
3. **"Not now" did nothing.** Declined work came straight back. Two causes,
   found one after the other: dismissals were filtered before deduping (so
   the plan candidate was removed and the assignment candidate survived),
   and the lookup compared a *local* midnight against a *naive UTC* column.
   The machine is UTC+5:30, so every dismissal made before 05:30 local was
   silently excluded — a bug that would have shipped broken for every user
   east of UTC and worked fine for everyone testing west of it.
4. **The card rendered 34px tall.** The left rail is a flex column with a
   `max-height` and `overflow: hidden`; a default flex item gets shrunk and
   then clipped. All 342 characters of content were in the DOM and none of
   it was visible.
5. **A dangling possessive** — "45 min less on today's" — from stripping
   " workload" off a label and lowercasing the rest. Phrasing is now derived
   from the date in the key.

The identity problem behind (1) and (3) is fixed properly rather than
patched twice: `domain.student.identity_key` hashes (title, course) into a
stable key that both the dedupe and the dismissal lookup use, so the same
work is recognised across ingest paths. It is hashed, not stored as text,
so the audit table still holds no readable assignment title.

## 11. Known limits

Stated rather than hidden:

* **Overrides do not re-optimise automatically.** By design (§6). The gap
  that mattered — a literal move can leave a day nobody can do, and the
  product said nothing — is closed: `overrides.rebalance_worth_offering`
  decides whether re-solving would actually help, and the card offers it
  with the reason. It deliberately stays quiet when the move was harmless,
  because prompting every time trains people to dismiss the prompt. What is
  still missing is an in-place rebalance endpoint: the offer currently routes
  to the scheduler, where regeneration already lives.
* **Mastery decay is a heuristic.** `next_action_glue._mastery` applies the
  row's own `decay_rate` with a floor. It is defensible and it is not fitted
  — there is not yet enough labelled review data to fit it.
* **Assessment detection is title-based.** `_assessment_days_by_course`
  matches "exam / test / quiz / midterm / final" in the assignment list. It
  reads the same list the rest of the product does, so it cannot invent
  urgency that exists nowhere else on screen, but it will miss an oddly named
  assessment.
* **`ObjectiveWeights` and `NBAWeights` are global.** The architecture allows
  per-student weights; nothing learns them yet. `schedule_decisions` is the
  data collection that would make that possible.
* **The four profiles are hand-authored.** They are four defensible plans,
  not a search over the weight space.
* **No offline evaluation harness yet.** "Demonstrably better than sorting by
  deadline" is asserted by unit tests on constructed cases, not yet measured
  against production outcomes. Acceptance rate per reason code, from
  `schedule_decisions`, is the first real metric and it needs cohort data to
  exist.
