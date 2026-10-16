# Prerequisite urgency at exact clock deadlines

An independent clock-slot benchmark reproduced a scheduling defect: an
assignment due tomorrow could be required by work due this morning, yet
unrelated evening work took the morning slots before that prerequisite.
Day-level dependency bounds were satisfied, but valid work was deferred.

Same-day ordering now propagates the earliest dependent urgency backward
through its prerequisite chain, then chooses ready work in that order.
Both tentative deadline feasibility and final plan materialization use this
shared ordering. Original assignment deadlines, priorities and dependency
metadata are preserved. No inferred deadline replaces the source deadline.
Cycles retain the existing bounded fallback and placement checks.

The independent oracle enumerates permutations of four atomic 30-minute
tasks in two 90-minute windows on each of two days. Slots include the required
five-minute transition. Seed 93817 generates 500 instances: 309 are feasible in
that domain; the baseline misses 16 of them. The new implementation schedules
all 309. Every instance independently checks actual timestamps, source deadline
bounds, window bounds, at least five minutes between consecutive work blocks,
prerequisite completion/order and minute conservation. No-witness instances
must retain deferred work. The oracle does not call production constraints or
clock helpers. The tested pipeline uses real capacity construction, allocation,
calendar-gap sizing and final clock rendering.

A named morning-deadline fixture runs in all 24 input permutations and retains
the parent's original later deadline. Before the change all 25 new tests fail;
afterward all 25 pass. A broader 903-case planner/service/deadline/repair run
passes. Independent Python/standards and spec reviews completed; the spec
review's transition/infeasibility evidence gap was corrected and rerun.
Scoped Ruff and diff checks pass. Full-suite, hosted and release evidence
follow separately; this report initially describes local work.

This is a bounded clock-order correction, not a proof of general optimality,
arbitrary variable-duration repairs, inferred prerequisites or improved real
student outcomes. The original ledger, physical-device/study-signal evidence,
live tutor/Fish behavior, full paid launch and country/language work remain open.
