# Dependency scheduling against an independent feasibility oracle

A reproducible benchmark found that a valid schedule existed for some small
assignment sets, but the planner still deferred work. The preferred finish-early
buffer had become a prerequisite's hard deadline. Absolute dependency layers
also let unrelated, later-due work consume time needed by a ready dependent.

Prerequisite hard bounds now use the dependent's actual deadline day. Hard
deadlines propagate from descendants to ancestors in reverse topological
order; release bounds propagate forward. Allocation and deferred retries
choose the most urgent ready sitting through a heap, releasing dependent
tasks only after all prerequisite sittings have been considered. Actual
placement checks still refuse dependents whose required work did not fit.
Existing pinned intent, clock placement, exact-time checks and honest
deferrals remain in effect. Unknown prerequisite IDs are not inferred.

The new test oracle exhaustively enumerates every day assignment for five
atomic 30-minute tasks across three days. It uses only the fixture's capacities,
release days, deadlines and declared acyclic dependencies, independently of
the production allocator, cost function and verifier. Seed 81037 generates
2,000 cases: 261 are feasible and 1,739 are infeasible in that domain.

Loading the exact baseline planner from commit 3dbe828 reproduced 71 missed
feasible schedules among those 261. The new allocator fits all 261. For every
generated case, the regression also independently checks minute conservation,
daily capacity, release/deadline bounds, prerequisite completion/order and
reasoned deferrals. Infeasible cases retain unscheduled work. A larger local
exploratory 20,000-case run found 2,792 feasible cases with no missed feasible
schedules. This is bounded algorithm evidence, not a claim of general optimality.

Clocked service tests reproduce "skip today" and "limit today" repairs with
reading before writing, conserved work and valid final times. A review-found
three-step-chain regression is checked in all 24 input permutations, proving
that an early descendant deadline reaches every ancestor in that example.
The final core run passed 130 cases after this fix. An earlier broader run
passed 1,074 planner/service/repair/deadline/endpoint cases before the final
propagation correction. A complete local run passed 4,727 tests (33 skipped);
it started before that final correction, so exact-head hosted checks remain
the release gate. Independent Python and spec reviews completed, with the
chain finding corrected and rechecked. Hosted and release evidence follow
separately; this report initially describes local work.

Limits: the oracle does not model variable task sizes, wall-clock breaks,
calendar fragmentation, exact timestamp propagation through arbitrary chains,
stochastic completion, every repair intent or the full cost objective. No real
student outcome improvement, universal dependency inference or optimality is
claimed. Physical study signals, live tutor/Fish behavior, paid-launch evidence,
course quality and the rest of the original acceptance ledger remain open.

Release: PR104 merged at 884cd3b0b5dcebf008a8c80cbe863549c35d4989.
Hosted exact-head checks passed 4,751 unit tests (16 skipped), 130 public
browser tests (1 skipped), all tutor/camera workflows and Security. Public
exact-revision/schema verification passed. Railway plugin confirmed web
f9f012fb-c82b-4cdb-a546-cab7e84eeb8c and newsletter
795cec1a-45f6-426b-a02c-8b45ad70ea0d SUCCESS, one web replica, no issues.
The bounded filtered web startup error scan returned zero entries.
Post-merge Tests 38830046439 is still running at this record's creation.
