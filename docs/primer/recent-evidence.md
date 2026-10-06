# Teaching that notices recovery

## Product change

The next lesson should respond when a student improves. Lifetime totals remain useful history, but must not trap a learner in repair after several independent successes. Extra practice before a scheduled review must also leave that review date intact. A later check should test what the student recalls after a gap.

Students will see recent independent answers, a plain-language explanation of the next teaching move, and the next review date in their learning plan and practice dashboard. Hinted answers remain assisted. Returning after a long absence calls for a fresh check rather than an old ability label.

## Architecture and rules

A pure evidence reducer consumes server-scored attempts in chronological order. It retains twelve recent outcomes per skill, lifetime counters, and a review stage. No new personal data or database table is needed. One streamed, owner-filtered query builds evidence for the plan, tutor and practice selector; the UI displays the same server decision.

- The last six attempts describe recent practice. Compare two complete six-attempt windows only when both exist; a difference of at least two independent successes is a descriptive trend, not statistical confidence.
- A latest missed answer requests repair; a hinted success requests an independent attempt. Three consecutive independent successes can request a transfer question even after older failures. Sparse or mixed evidence requests diagnosis.
- Independent success establishes a one-day review. Correct independent answers at or after the due date move the interval through 3, 7 and 14 days. Early extra successes neither advance nor postpone it. Wrong or assisted answers restart the cadence.
- A due review takes precedence over transfer. After thirty days without a check, request a fresh review instead of using an old failure as current evidence.
- A student explicitly reporting that they are stuck still receives repair. Student-reported completion remains separate from checked evidence.

These are inspectable practice heuristics. They do not estimate a validated mastery probability or establish learning gains. Existing age, consent, ownership, hint, replay and deletion boundaries continue to apply.

## Verification

Replay synthetic histories covering recovery, recent difficulty, hints, early repetitions, scheduled reviews and long absences. Exercise the real answer API through plan and tutor decisions, including another account's isolation. Check the mobile UI, then run the repository tests and production smoke checks before delivery.
