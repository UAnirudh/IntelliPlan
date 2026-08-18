# Scheduler audit — workload intelligence

Written 2026-08-16, against `main` @ d9b967e. 960 tests green at baseline.

The question this audit asks is not "does the scheduler work" — it does — but
"is the intelligence real, and is it actually reaching the student". Several
of the strongest pieces of this codebase are wired up in a way that cancels
them out.

## 1. Current architecture

### The layering that exists

```
Canvas / StudentVue / Schoology / Classroom / Blackboard / Moodle / Notion / manual
        │
        ├─ legacy per-source helpers            canvas_helper.py, studentvue_helper.py, …
        │  (each does its own normalisation, its own estimate, its own priority)
        │
        └─ intelliplan/integrations/lms/*       typed connectors (Blackboard, Moodle,
                                                 PowerSchool, Classroom) + registry
        ▼
   ── two normalisation layers, in parallel ──────────────────────────────
   A. loose dicts  ──────────────────────────►  App._planner_task_rows()
      {title, course, due_date, estimated_time,       ▼
       difficulty, priority: "High", points}    intelliplan/intelligence/planner.py
                                                (deterministic day allocation)
                                                       ▼
                                                scheduler_engine.place_day_blocks
                                                (clock placement, breaks)
                                                       ▼
                                                schedule_data → scheduler.html

   B. typed Assignment  ─────────────────────►  intelligence/priority.py (0..100 + chips)
      repositories/assignments.py                intelligence/workload.py (7-day stress)
                                                 intelligence/health.py
                                                       ▼
                                                 services/today.py → Command Center
```

### What is genuinely good

- **`intelligence/planner.py`** is a real optimizer, not a sort. Cost function
  over (session → day) with a hard capacity ceiling and priced preferences:
  convex day load, urgency, deadline buffer, spacing of a task's own sittings,
  course switching, day quality, concept stacking, fragmentation. Greedy seed →
  local search → retry → value-based triage → coalesce → renumber. Deadlines
  are hard constraints (`math.inf`). Overload is reported, not hidden
  (`Plan.overloaded`, `Plan.deferred`, per-task deferral rows).
- **`intelligence/estimation.py`** is a real learning model. Multiplicative
  bias in log space, hierarchically pooled global → kind → course →
  (course, kind), shrunk by effective sample count, 45-day recency half-life,
  ±ln(4) outlier clamp. Reports uncertainty; the planner spends that
  uncertainty as deadline buffer (`buffer_days_for`). Stamina measured from
  sittings only.
- **Capacity is honest.** `services/scheduling.usable_minutes()` discounts raw
  window minutes for the gaps and breaks placement will actually spend (~74%),
  per window rather than per day. This is the difference between a plan and a
  plan-shaped object.
- **Sessions feed the model.** `/active` sittings mirror into `TaskFeedback`
  and re-fit the estimator on finish (`active_glue._on_session_finished`).
- **Every session carries reasons** (`_reasons_for`), so the UI can explain
  placement without exposing model internals.

So: requirements 4 (optimization), 7 (learning), 12 (realism), and most of 2
already exist in the intelligence layer. The problems are at the seams.

## 2. Major weaknesses

Ordered by how much they cost the student, not by how hard they are.

### W1 — Estimates are corrected twice (correctness bug)

`App.generate_schedule` runs every assignment through
`StudyDNA.adjust_estimate()` (line ~7547) *before* the planner sees it. The
planner then applies `EstimationModel.predict()`, which multiplies by its own
learned ratio. **Both models are fit on the same `TaskFeedback` rows.** A
student who reliably runs 1.5× over gets sized at ~2.25×. The comment above
the loop says the estimates reaching the prompt are already corrected — true
for the AI fallback, wrong for the deterministic path that now serves every
request.

### W2 — Three signals the planner prices are never populated

The cost function reads them; nothing supplies them.

| Signal | Planner uses it for | Actual value at runtime |
|---|---|---|
| `DayCapacity.quality` | avoid historically weak days | always `1.0` — `StudentContext.weak_days` is never passed in `_build_planner_schedule` |
| `PlannerTask.concepts` | detect a day stacking shaky material | always `()` — no row sets `concepts`, so `concept_mastery` is loaded and then unused |
| `PlannerTask.depends_on` | prerequisite ordering | always `()` — nothing in the product can express a dependency |

`_apply_dependencies` and the `concept_stack` weight are dead code in
production. Requirement 6 (prerequisite awareness) is unimplemented at the
data level despite being implemented in the engine.

### W3 — `hours_per_day` is ignored

`_build_planner_schedule` builds a bare `PlannerConfig()` under a comment
claiming the student's stated hours-per-day sets how full days get before the
optimizer charges for it. It does not; `target_utilisation` stays at 0.8 of
whatever the availability windows happen to be. A student who says "1 hour a
day" and has evening availability marked gets planned at ~2.4h/day.

### W4 — Enrichment overwrites planner output

`enrich_schedule_data` looks assignments up by `block["assignment"]`, which on
the v2 path is `session.label` — `"APUSH essay (part 2 of 3)"`. Every split
block misses the lookup and gets `priority = "Medium"`, a re-inferred
difficulty, and no real due date, silently discarding the planner's integer
priority and the deadline the clock placer orders by. `block["parent_title"]`
is right there and unused.

### W5 — Duration estimation has one input signal

The *base* estimate before learning is `points_possible × 1.5`, rounded to
30 minutes, duplicated in ~8 places (`canvas_helper.py:97`,
`studentvue_helper.py:209`, `App.py:4429/4582/4704/8245/8398/11366`), with a
hardcoded `60` when points are missing. Nothing reads:

- word count / page count in the description ("2,000-word essay")
- question or problem count ("problems 12–18", "10 questions")
- rubric row count (Canvas exposes rubrics)
- submission type (`online_upload` vs `online_quiz`)
- description length or structure at all

This is exactly the "10 algebra problems vs 2,000-word researched essay"
failure in the brief. The learning layer corrects *bias*, but it cannot
recover information the base estimate never had — a course-level ratio cannot
distinguish two assignments inside the same course.

### W6 — Decomposition is arithmetic, not semantic

`split_into_sessions` divides minutes into equal sittings: "part 3 of 5". It
respects focus length and spacing correctly, but a research paper's sittings
are not interchangeable — research must precede outlining, drafting precedes
revision. No stage vocabulary exists (grep for outline/draft/revise finds only
tutor content). Requirement 5 is unmet, and it is the natural source of the
real `depends_on` edges W2 needs.

### W7 — Rescheduling on failure is never triggered

`planner.reschedule()` and `SchedulingService.replan()` are implemented,
tested, and called from nowhere but tests. Missing a session does not
re-optimize anything. What exists in production is `reflow_schedule()`, which
re-places blocks on the clock after a drag — a different job. So requirement 8
is engine-complete and product-absent: the student who skips Monday still
sees Monday's plan.

### W8 — Two priority engines that disagree

`intelligence/priority.py` computes a 0..100 score from five capped components
(urgency 40, importance 25, grade impact 20, effort 10, dependency 5) and emits
reason chips. The scheduler does not use it. It uses
`App._priority_score_for`: `{high: 80, medium: 50, low: 30}` plus a points
bump. Same product, two answers, and the better one is wired only to the
Command Center. Same split for assignment shape: typed `Assignment` for
Today, loose dicts for the scheduler.

### W9 — Availability is coarse; real calendars are not consulted

`windows_for_date` resolves availability to three fixed slot windows
(morning/afternoon/evening) minus free-text weekly commitments parsed by
regex. Not represented: dated one-off events, class periods, anything from
Google Calendar. `google_calendar_helper.get_free_busy()` exists and is never
called from the scheduling path — the integration is push-only (schedule →
calendar). Requirement 13 holds for recurring text commitments and fails for
everything on the student's actual calendar.

### W10 — Canvas/StudentVue metadata is thrown away at the boundary

Canvas assignment payloads are read for title/due/points and discarded.
Descriptions are fetched *on demand for display* (`/assignment/description`)
and never used for sizing. Rubrics, modules, submission types, and submission
state are not requested. StudentVue's SOAP surface genuinely gives less
(title, points, type, description via gradebook) — that limit is real and
should be stated, not papered over.

## 3. Prioritized plan

Phase ordering follows the brief's, collapsed onto what this codebase actually
needs. Each phase is independently shippable and testable.

**P0 — Fix what cancels the existing intelligence** (small diffs, high value)
1. W1: stop double-correcting estimates on the planner path.
2. W2a: pass `weak_days` into `StudentContext`.
3. W3: honour `hours_per_day` as a soft daily ceiling.
4. W4: match enrichment on `parent_title`, never clobber planner fields.

**P1 — Give estimation more than one input** (W5, W10)
5. New `intelligence/sizing.py`: pure `base_estimate(metadata) → (minutes,
   signals)` reading word/page/question/rubric counts, submission type, and
   description shape. One implementation, replacing the eight copies of
   `points × 1.5`.
6. Extend the Canvas fetch to carry description, submission types, rubric, and
   submission state into the assignment dict; document what StudentVue can and
   cannot supply.

**P2 — Semantic decomposition and real dependencies** (W6, W2c)
7. `intelligence/decomposition.py`: stage templates per kind (essay/research →
   understand → research → outline → draft → revise → proofread; exam → learn →
   practice → review mistakes), applied only when size and kind justify it.
   Stages emit `depends_on` edges, which `_apply_dependencies` already honours.

**P3 — Unify the model** (W8, W2b)
8. Make the scheduler consume typed `Assignment` + `intelligence/priority.py`,
   retiring `_priority_score_for`. Attach concepts from the learning graph so
   `concept_stack` and `weak_concept_penalty` become live.

**P4 — Recovery** (W7)
9. Detect missed/abandoned sittings, build `Reality`, call `replan`, and show
   the student what moved and why. This is the requirement-8 loop.

**P5 — Real availability** (W9)
10. Dated commitments + Google Calendar free/busy subtracted from windows;
    finer-grained availability than three slots.

**P6 — Explanation surface**
11. Surface deferrals, overload, and per-session reasons in the UI as
    first-class content rather than a note string.

Verification gate for every phase: `pytest tests -q` green, new unit tests for
the new behaviour, and a manual check that Canvas sync, StudentVue sync, and
saved-schedule reload still work.

## 4. Progress

### P0 — done (2026-08-16)

- W1: the pre-correction loop moved inside the AI-fallback branch. The planner
  receives raw estimates and applies its own model once.
- W2a: `weak_days` flows into `StudentContext`; the `day_quality` weight is
  live.
- W3: `DayCapacity.comfort_minutes` + `soft_limit()`. `hours_per_day` is now a
  *priced* daily ceiling — a deadline still overrides it, and the availability
  window is still the hard limit.
- W4: enrichment matches `parent_title` before `assignment`, and the planner's
  0..100 score survives as `priority_score` alongside the UI's bucket.

### P1 — done (2026-08-16)

`intelliplan/intelligence/sizing.py` is the single implementation of "how big
is this piece of work". It reads word counts, page ranges (distinguishing
"pages 120–145" from "3–5 pages"), problem and question ranges (inclusive —
"problems 12–18" is seven), chapters, rubric criteria, quiz question counts,
submission types, quiz time limits, and instruction length. Signals for
different work add; signals for the same work take the maximum. Weak evidence
(a rubric, long instructions) may only raise the kind/points prior, never lower
it; direct measurements may do both.

Wired into nine call sites that each carried their own copy of
`points_possible × 1.5`: `canvas_helper`, `studentvue_helper`, and seven inside
`App.py` (Google Classroom, Blackboard, Moodle, and four Canvas paths).
Precedence at the planner boundary is student-supplied → metadata → upstream
guess; a duration the student typed into the clarify prompt is never overruled,
and saved custom descriptions are read as sizing input for the first time.

Two related fixes came out of the same work:

- `MAX_PREDICTED_MINUTES` raised from 300 to 1200. It clamps *whole-task*
  effort, and at five hours it silently told every term project it was a long
  afternoon. Sitting length is bounded separately by `SESSION_CAP_BOUNDS`.
- Canvas assignment fetches now pass `per_page=100`. Canvas defaults to ten, so
  a course with thirty assignments reported the first ten and the planner
  scheduled a week that was missing two thirds of the work. Four fetch sites
  were affected.

Effect on the brief's example, both worth 50 points:

| | before | after |
|---|---|---|
| "Complete problems 12–18" | 75 min | 28 min |
| "Write a 2,000-word researched essay" | 75 min | 600 min |

### P2 — done (2026-08-16)

`intelliplan/intelligence/decomposition.py`: stage templates for research
paper, essay, exam, presentation, lab report, project, and reading, with
effort shares, prerequisite edges, and notes. Work earns stages by being over
150 minutes *and* by being a kind whose phases are different activities;
repetitive work is never staged, and every refusal carries a reason.

The stages exposed a dependency bug. `_apply_dependencies` only tightened
windows, which orders nothing — two stages of one essay can have overlapping
windows, and the optimizer duly placed "write the draft" before "research".
Prerequisite edges had existed in `PlannerTask` since the planner was written
and nothing ever populated them, so it had never been exercised. Ordering is
now enforced against real placements: `_dependency_layers` places shallowest
first, `_dep_bounds` clamps candidate days to where an item's prerequisites and
dependents actually sit (both directions), local search re-checks per item, and
`stage_index` orders the one day that holds two stages of the same work.

### P3 — done (2026-08-16)

`intelliplan/services/prioritisation.py` retires `App._priority_score_for`. The
scheduler now scores with the same five-component engine as the Command Center,
over the whole list at once because the dependency component is a property of
the set. The student's High/Medium/Low label nudges the score by 12 rather than
replacing it — enough that marking something high visibly pulls it earlier, not
enough to let a "Low" label bury an overdue midterm.

`PlannerTask.concepts` is populated for the first time, matched from the title
and description against the student's own tracked concept vocabulary on word
boundaries. `concept_stack` and `weak_concept_penalty` stop being dead code.

### P4 — done (2026-08-16)

`intelliplan/services/recovery.py` + `POST /schedule/recover`. `build_reality`
reads the saved plan and its progress blob; only days strictly before today
count as missed, and a task is finished only when every sitting it was given is
checked off. Completed and abandoned minutes come from both the checkboxes and
Active-study sittings, taking the larger per task so overlapping evidence
credits the work once. The whole remaining horizon is then re-optimised under
the same cost function, and `summarise_changes` reports what moved and why.
Returns `changed: false` when nothing slipped, and saves in place when
something did.

### P5 — done (2026-08-16)

`windows_for_date` takes `busy_by_date` and subtracts real calendar events
alongside typed commitments. `google_calendar_helper.busy_minutes_by_date`
reads the whole horizon in one free/busy query, converts UTC to the student's
local minute-of-day, and splits events that span midnight across both days.
Every failure path degrades to "no busy time known" rather than costing the
student a plan.

### Still open

- **P6** — the explanation surface. Deferrals, overload, per-session reasons,
  stage labels, priority reasons and the recovery diff all exist in the payload
  and are not yet first-class in the UI. `/schedule/recover` has no button.
- Availability is still three fixed slots per day. Calendar events now carve
  into them, but a student whose free time is 16:20–17:35 cannot say so.
- `MIN_DECOMPOSE_MINUTES`, the sizing rates, and `LABEL_NUDGE` are priors
  chosen with reasons, not measured. They are the obvious candidates for
  fitting against real completion data once there is enough of it.

### What StudentVue can and cannot supply

Stated plainly so nobody builds on data that is not there. The Gradebook SOAP
response gives, per assignment: `Measure` (title), `MeasureDescription` and
`Notes` (free text — now read for sizing), `Type` (category, e.g. Homework /
Test — now used as the kind), `Points`, `DueDate`, `Date`, `DisplayScore`,
`Score`, `HasDropBox`, `DropStartDate`, `DropEndDate`.

There is **no** rubric, **no** word count, **no** submission type, **no**
module or unit structure, and **no** per-assignment attachment list. Anything
richer than the list above would be invention. Canvas is where the deeper
metadata comes from, and integrations should degrade to the points prior on
StudentVue rather than pretend parity.
