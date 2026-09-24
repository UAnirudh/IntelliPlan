# The Follow-Through Engine

Companion to [01-architecture-audit.md](01-architecture-audit.md) and
[02-implementation.md](02-implementation.md). Those documents describe a
scheduler that builds *feasible* plans. This one describes the layer that makes
plans the student **actually follows**, and keeps them working when the week
doesn't go to plan.

## 0. The one-paragraph version

Every study planner optimises the same thing: *does the work fit before the
deadlines?* IntelliPlan now optimises a different thing: **how much of the work
will actually get done, and on time, by this particular student.** It learns
from each student's own history when they really follow through: time of day,
weekday, sitting length, and how full the day already is. It places work where
that student is most likely to do it. It simulates the plan a few hundred times
to put an honest on-time probability on every deadline. When life happens ("I
can't study tonight"), it repairs the plan by moving only what has to move, and
shows the consequence before anything is committed.

## 1. What was wrong before

The audit and the code agreed on four gaps:

| Gap | Symptom a student feels |
|---|---|
| The planner optimised *placed* minutes, not *completed* ones. The learned completion model (`behavior.py`) never touched placement; "weak days" was a hard-coded `quality=0.75`. | The plan keeps putting Chemistry on Friday nights, and it never gets done. |
| Every replan rebuilt the fortnight from scratch. | Miss one evening and the whole week reshuffles. |
| The FAQ promised "the AI adapts your entire schedule whenever you make a change". Drags were applied literally; there was no "can't study tonight". | Rescheduling is manual, block by block. |
| Deadline risk was a heuristic (`slack <= 1 day → risky`). | No honest answer to "am I going to make it?" |

## 2. The pipeline

```
history ─► Follow-Through model ─┐
                                 ▼
tasks ─► estimation ─► planner (cost + follow-through + stability) ─► plan
                                 ▲                                      │
                                 │                                      ▼
                     risk loop: simulate ◄──── Monte Carlo risk ◄───────┤
                     extra buffer where it helps                        │
                                                                        ▼
intent ("can't study today") ─► snapshot of saved plan ─► repair ─► preview ─► apply
```

| Module | Job |
|---|---|
| `intelligence/followthrough.py` | P(a planned sitting gets done): Bayesian logistic regression |
| `intelligence/risk.py` | P(each assignment lands on time): Monte Carlo simulation |
| `intelligence/robust.py` | Size deadline buffers from simulation, not heuristics |
| `intelligence/planner.py` | + follow-through cost, stability anchors, pins, pushes, sitting hints |
| `intelligence/rescheduling.py` | Intents → minimal-disruption repair, with consequences |
| `services/scheduling.py` | Composition: fit, plan, adjust, forecast |
| `api/adjust.py`, `followthrough_glue.py` | HTTP + ORM seam |
| `static/js/followthrough.js` | "Life happened?" bar, preview sheet, forecast |

## 3. The Follow-Through model

**Question.** For a planned sitting — this course, this length, this day, this
time of day, after this much work already done today — what is the probability
it actually happens?

**Model.** Logistic regression over a sparse feature vector:

| Feature | Encoding | Why |
|---|---|---|
| time of day | morning / afternoon share (evening = reference) | people have reliable hours |
| weekday | 7 one-hot | Fridays are real |
| length | `ln(minutes / stamina)` | sittings past your focus length fail more |
| fatigue | hours already on the day | the 4th block of a day is not the 1st |
| deadline proximity | `1 / (1 + days_to_due)` | procrastination is real, and predictable |
| difficulty | hard / easy | |
| kind | memory (exam/test) / deep (project/lab) | |
| course | random effect, top 12 courses with ≥3 rows | "Chemistry sessions get skipped" |
| source | 1 for Active-study sittings | those are conditional on *starting*, so they get their own offset |

**Fitting.** MAP estimate under a Gaussian prior, by Newton's method with
step-halving (the log-posterior is strictly concave, so there's a unique
optimum). Sparse rows make it O(n · nnz²): about 20 ms for 400 rows in pure
Python, with no numerical dependency added to the deploy.

**Why Bayesian.** One student's history is small. Every coefficient is shrunk
toward the **population prior**, so a new student gets the population's
answer, a student with a semester of data gets their own, and nothing in
between needs a special case. Recency is handled by a 45-day half-life on
observation weights, the same as the other models, so they agree about what
"recent" means.

**Uncertainty.** A Laplace approximation (inverse Hessian at the mode) gives
every coefficient a posterior sd. An **insight is only shown when an effect is
≥ 1.5 sd from zero and backed by ≥ 12 effective observations.** The model
never presents the population prior as something it learned about *you*.

**Evaluation.** `evaluate_holdout` fits on the older 80% of a student's history
and scores the newest 20% (out-of-*time*, never a random split) against the
base-rate baseline. On synthetic data with planted effects it beats the
baseline by ~13% log-loss. `/api/schedule/forecast` returns this per student
once there are 30+ rows.

### 3.1 Training data (and what is deliberately excluded)

| Source | Label | Notes |
|---|---|---|
| **Plan outcomes**: every block on a past day of a saved plan | ticked / not ticked | Exactly the quantity the planner needs. Harvested live, and logged durably (`student_signals`, kind `plan_outcomes`) before any plan is overwritten. |
| Active-study sittings | finished / abandoned | Carries `source_session=1` so its higher base rate doesn't contaminate the plan-level rate. |
| ~~`TaskFeedback`~~ | — | **Excluded.** A feedback row only exists if the task was done, so all rows are successes; fitting on them teaches "everything gets done". |

Leak guards:
- Sitting length is the *planned* length. An abandoned sitting's actual length is short *because* it was abandoned.
- A superseded plan's blocks after it was replaced aren't failures; each saved plan is only harvested for the days it was actually the student's plan.
- Outcome rows contain **no assignment titles**: dates, minutes, course, kind, difficulty, and the label only. Same privacy rule as the audit tables.

### 3.2 The population prior: the compounding part

`fit_population_prior` pools every student's outcomes (≤200 most recent rows
each, so power users don't *become* the population; course effects excluded,
because "Chemistry" means something different at every school). The result is
stored in `model_priors` and read by every new student's model.

**Run it on a timer:** `POST /cron/refit-followthrough-prior` with
`X-Cron-Secret`. Daily is plenty. Until 5+ students have data it keeps the
hand-set default, which is stated, small, and directionally uncontroversial.

This is the defensible part of the system. The algorithm is documented here
and could be re-implemented. **The fitted prior can't be**: it's a function of
IntelliPlan's users' real follow-through, and it improves with every week of
use.

## 4. Completion-aware planning

The planner's cost function gains one term:

```
cost += w_follow_through · (0.5 + 0.5 · priority/100) · (1 − P(done | task, day, load))
```

This is the expected value lost to a skipped sitting. Only *differences between
days* matter to the optimiser, so a student with a flat profile is untouched,
and a student who never does Friday evenings sees work drift off Friday
evenings.

**Deliberately excluded from placement: deadline proximity.** Many students
really are likelier to do work the night before, and the model learns that so
the *forecast* is honest. Letting the planner act on it would mean planning
everything for the night before. **We model procrastination to predict it,
not to plan for it.**

When the model has real weekday signal, the old hard-coded weak-day penalty
is switched off, so the same evidence isn't counted twice.

## 5. Deadline risk: simulating the plan

`risk.simulate` runs each plan ~300 times. Each run draws:

- **Task durations**: log-normal around the estimate, with the spread the
  estimation model measured, plus a shared "this week" factor (ρ = 0.35),
  because running slow on one assignment usually means running slow on all of
  them.
- **Which sittings happen**: Bernoulli with the Follow-Through probability,
  plus a 6% chance per day that the **whole day is lost** (illness, a game ran
  late). Clustered failures are what actually sink deadlines; independent
  failures would badly understate risk.
- **Catch-up**: free time is used for catching up with probability
  `0.75 × the student's own follow-through`. Deliberately *below* follow-through
  on planned blocks: work with a time and a place gets done more than work
  that is merely owed. A skipped block can be caught up the same evening,
  unless the whole day was lost.
- **Crunch**: up to 60 unplanned minutes on each of the last two days before a
  deadline, at the same catch-up rate. Real students stay up; leaving this out
  made every busy week look hopeless.

Work is then settled earliest-deadline-first against what's left. Output:
per-assignment P(on time), status (`on_track` ≥ 80%, `watch` ≥ 60%,
`at_risk`), the main cause (`not_planned` / `follow_through` / `overrun`),
expected missed deadlines, and a priority-weighted on-time rate. Stages of one
assignment roll up to the assignment.

**Common random numbers.** Comparisons (before / do-nothing / after) reuse one
seed, so both plans face the same sampled futures. At 300 samples, independent
runs would produce noise as large as the effect being measured.

Cost: 8 ms for a normal week, ~75 ms for 40 tasks.

## 6. Buffers sized by simulation

`robust.plan_with_risk_control`: build → simulate → give any assignment below
80% (priority ≥ 35, risk that buffer can fix) one extra finish-early day →
re-solve → keep the new plan **only if** it's measurably better on the same
futures (≥ +0.5 points weighted on-time, no increase in expected misses) →
stop at the first round that doesn't help (max 2).

Two things it never does. It doesn't invent capacity: "doesn't fit" is the
student's decision, surfaced as a deferral. And it doesn't touch priorities:
a bumped priority would be saved into the plan and bumped again on the next
replan, a slow drift nobody chose.

## 7. Rescheduling by intent

| Intent | UI | What happens |
|---|---|---|
| `skip_day` | Can't study today | The day's capacity goes to 0; its sittings move, **keeping their sizes** |
| `limit_day` | Less time today | Capacity capped; overflow moves |
| `add_time` | I have extra time | Real clock windows extended (never past 11 PM, never in the past) |
| `catch_up` | I'm behind | Missed work gets new days; everything else stays put |
| `push` | Not today (block menu) | No sitting before the chosen day; past the deadline → reported, not hidden |
| `done` / `progress` | Already done (block menu) | Credit; dependent stages unblocked |
| `pin` | Drag to another day → "Rebalance?" | That sitting is fixed; the rest re-plans around it |

### 7.1 Minimal disruption

The saved plan is reconstructed (`snapshot_from_schedule`) with each task's
**remaining** minutes (ticked blocks credited), marked `calibrated` so the
estimation model's bias isn't applied twice, and each surviving sitting kept
with **its own size and its own anchor day**. Moving a sitting off its anchor
costs `w_stability · (0.6 + 0.4 · min(distance, 3)/3) · nearness`. Moving
tomorrow's block is felt; moving one twelve days out barely is.

Per-sitting anchors matter. Per-task day sets let a 40-minute chunk take the
slot of an 80-minute one, and every mismatch showed up as churn. With exact
anchors, **re-solving an unchanged plan reproduces it exactly** (tested), and
clearing a day in a normal week moves only that day's sittings.

### 7.2 Do no harm

Every intent is solved twice. The **minimal-change** plan uses anchors weighted
20×. The **rebalanced** plan uses normal weights plus the risk loop. The
rebalanced plan ships only if simulation shows it's measurably safer (+1 point
weighted on-time, or 0.05 fewer expected misses); otherwise the student keeps
the week they already know. This rule caught a real defect: extra time on
Saturday once load-balanced an essay *later* and raised its risk.

### 7.3 Consequences before commitment

`preview: true` returns the proposed plan without saving it. The sheet shows:
- a deterministic headline built only from numbers the result carries ("Cleared Thu Sep 24. 2 sessions moved.")
- the risk status, naming "tight" and "at risk" assignments; it never says "on track" when one isn't
- per-assignment odds **before this change → after**, which is the comparison a student can act on
- how much rebalancing protects compared with just skipping, when that's meaningful

### 7.4 It remembers

`skip_day` / `limit_day` / `add_time` are stored on the plan as
`capacity_overrides` and honoured by every later adjustment, forecast,
recovery, and drag-reflow. Without this, "can't study today" followed by "push
this one" put work straight back on the cleared day.

### 7.5 Progress follows the right blocks

Block ids used to be positional (`d1-b1`), so any replan that shifted days
made browser-stored ticks light up different work. Adjusted plans get fresh
ids with a per-adjustment prefix. Today's already-finished blocks are carried
across, still ticked, and progress is replaced rather than merged.

## 8. API

| Route | Notes |
|---|---|
| `POST /api/schedule/adjust` | `{action, day?, minutes?, task_id?, from_day?, preview?}` → `{data, progress, headline, detail, changes[], moved_sittings, kept_sittings, strategy, risk{before, if_you_do_nothing, after}}`. Works for guests with a saved plan. 120/h. |
| `GET /api/schedule/forecast` | `{risk{tasks[], expected_missed, weighted_on_time}, insights[], model{…}, evaluation, missed_minutes}`. 240/h. |
| `POST /cron/refit-followthrough-prior` | `CRON_SECRET`-guarded. |

Generation (`/generate_schedule`) and recovery (`/schedule/recover`) use the
engine too: completion-aware placement, simulated buffers, `forecast` on the
plan, and anchors so recovery stops reshuffling untouched work.

## 9. Rollout and rollback

Flag `followthrough_engine`: **kill switch, default on.** Off, every new route
404s, the UI bar hides itself, and generation/recovery run exactly as before
(the service's `follow_through` defaults to `False`). One admin-panel toggle,
no deploy. New table `model_priors` is additive (`create_all`).

## 10. Performance

| Operation | Measured |
|---|---|
| Fit Follow-Through model, 400 rows | ~20 ms |
| Simulate a normal week / 40 tasks | 8 ms / 75 ms |
| `POST /api/schedule/adjust` end to end (preview, SQLite) | ~60 ms |
| `GET /api/schedule/forecast` | ~17 ms |

## 11. Known limits

Stated rather than hidden:

- **The simulator's constants are defensible, not fitted.** The lost-day rate (6%), week correlation (0.35), crunch (60 min / 2 days), and catch-up discount (0.75) are reasoned choices. The outcome log now records what happened. **Next step: a calibration job that compares forecast P(on time) with actual on-time completion** and fits these four numbers. Until then, treat the percentages as well-ordered rather than exact.
- **Between-student variances are hand-set.** The population fit learns the prior *means*; the variances (how fast a student departs from the population) are fixed. A hierarchical fit (per-student fits → empirical spread) is the principled next step once there are enough students.
- **Placement is day-level.** The model knows each day's mix of morning/afternoon/evening free time, not which exact window a block lands in. Good enough for day allocation; a slot-aware clock placer is future work.
- **Profile choice is still global.** The four counterfactual profiles are now scored with real follow-through odds, but *which* profile suits a given student isn't learned yet. `schedule_versions` + outcomes are the data a per-student bandit would need.
- **Overrides live on the plan.** Generating a brand-new plan starts clean; "can't study Saturday" doesn't carry into a regenerated week.
