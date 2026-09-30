# A goal the tutor can actually teach toward

The student needs to see how current courses, work and demonstrated skills connect to a desired outcome. The education plan adds a durable target, starting-point description, weekly time budget and optional target date. The same plan appears in the tutor's context on subsequent lessons. It does not imply that IntelliPlan has access to every part of a student's education.

## Data and teaching loop

An explicit build refreshes available connected grade summaries, active assignments and scored Quick checks for the current account. It records a dated snapshot with source availability and limits; it does not pull arbitrary assignment files. Selected Canvas files continue through the existing bounded schoolwork reader. The existing established-age, parent-consent and AI-personalization gates apply before school context is read or sent to the model.

The AI proposes three to seven prerequisite-ordered milestones. Each must include a learning action, a diagnostic question and an observable independent demonstration. It can associate a milestone only with an allowed question-bank skill in the student's current or prior grade. Unsupported IDs are removed. The student can rebuild the route after changing the goal, starting point, time budget or deadline. The proposal is not an expert-reviewed curriculum, a guarantee of reaching a grade, or a validated assessment.

The student starts a lesson from a milestone. Plani receives the active goal, dated education snapshot, next milestone and progress state. A student-reported difficulty requests a smaller example and repair. Matching scored checks provide actual attempt counts; sparse evidence requests diagnosis and sufficiently successful independent practice requests a transfer question. Every new answer still needs a teaching response, rather than a fixed sequence of explanations. Checked practice is read fresh when a plan loads, so a new result changes the next teaching context without rebuilding the plan.

Students may report a milestone as practiced, stuck, completed or not started. Those states are labeled as their reports. AI prose cannot mark a milestone complete or turn course grades into mastery. A mapped milestone offers a targeted server-scored Quick check; the existing owner, grade, expiry, hint and replay rules still apply.

## Storage and boundaries

`tutor_education_plan` holds one plan per account, its bounded snapshot, milestones, reported progress and a monotonic revision. Conditional updates reject stale tabs. Failed generation preserves the saved plan. Deletion removes the plan and snapshot, including when AI personalization has been disabled; account deletion clears the table too. The tutor rechecks consent before adding stored context to a prompt. Parent summaries do not expose this plan, school snapshot or private tutor conversation.

The UI shows source coverage and the snapshot date so the learner can correct gaps through their profile, connected school account or stated starting point. A refresh is explicit. Current connector helpers sometimes return an empty list on upstream failure, so `no_data` means no data was available for this snapshot, not that the student has no work or grades.

## Verification

Tests cover Family access on both hosts, goal validation, account ownership, opt-out suppression, progress conflict handling, invalid generation preservation, direct skill-check grade limits, prompt integration and deletion. Local browser verification uses real login and plan persistence with a deterministic model stub; model-generated curriculum quality still needs educator evaluation against real student goals.
