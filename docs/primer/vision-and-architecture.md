# Foundations: the path toward a living learning companion

## Why this belongs in IntelliPlan

IntelliPlan already connects plans, grades, study time, and an adult-facing tutor. Foundations gives a family an earlier entry point into that same learning loop. Its distinctive promise is continuity: a learner returns to a world that remembers choices; practice evidence changes the next skill; an adult sees what happened and gets a concrete activity to try away from the screen. The product should grow with a learner into IntelliPlan's broader planning and tutoring workspace, without treating a child as a productivity dashboard.

The ambition is a long relationship with the learner, but the current release is a carefully bounded chapter system. It does **not** deliver private-tutor quality, open-ended AI conversation with a child, original-writing evaluation, a reading-fluency assessment, or a validated measure of mastery.

## Product progression

| Horizon | Learner experience | Intelligence and evidence | Adult and teacher role | Release gate |
| --- | --- | --- | --- | --- |
| Now: branching Foundations | Four authored chapters in each of three worlds; each chapter has reading, writing, arithmetic, and a choice that changes the next scene | 36 reviewed questions, deterministic grading, one immediate repair after a miss, independent vs clue-assisted evidence, prerequisites, spaced review, replay-safe journey state | Account holder reads with the child, sees a focus skill, gets an offline activity and chapter conversation prompt | Functional, privacy, accessibility, content, and cross-device tests |
| Next: deeper instruction | More authored chapters and decodable text, manipulatives, worked examples, multiple representations, optional read-aloud under adult control | Diagnostic placement; response-level misconceptions; assisted vs independent attempts; content versioning; teacher-reviewed skill graph | Explicit adult observations and a shareable report controlled by the account holder | Educator review, accessibility study, parent usability study, efficacy pilot |
| Later: generative companion | Stories can use approved learner interests and life context, with continuity over years | Retrieval over approved content, constrained generation, evaluator checks, human escalation, longitudinal learning experiments | Adult and teacher control what context is used and what is shared; corrections are visible and reversible | Child-safety review, consent and retention controls, independent learning-outcome evidence |

## Current interaction contract

1. The account holder creates a nickname and selects a world. No child account, full name, photo, audio, birth date, or school data is needed.
2. The server retrieves the learner's current journey position. A chapter has three beats: reading, writing, then arithmetic. The chapter scene and previous choice are shown above the task.
3. Within each beat, the selector chooses an unlocked skill in that domain. It prefers a due skill with weak or missing evidence. The question comes from a reviewed answer-key catalog. A learner can reveal a clue before submitting; clue use is recorded before the server returns it. After a missed answer, one alternate item checks the same skill before the story moves on, so a learner does not get stuck.
4. The answer is graded on the server. Writing mechanics are checked for the actual capital letter and ending mark; choice and number answers use normalized matching. Only the item ID, outcome, time, and whether a clue was used are retained. Stronger labels and prerequisite unlocking require correct answers without a clue.
5. A signed, one-hour challenge binds the item and journey version to one learner. Updating the journey position and recording the attempt happen in one transaction. Two tabs cannot both advance the same beat.
6. After three beats, the learner makes an ungraded story choice. The choice ID is saved, its authored consequence appears in the next scene, and a story recap preserves every decision. After four chapters, the learner can replay the world with different choices while keeping skill evidence.
7. The adult panel explains a focus skill from observed practice, suggests one offline activity, and offers a conversation prompt from the current chapter. The print view can be brought to a teacher.

## Data and service architecture

The current Flask blueprint uses the existing signed-in account as the trust boundary. Every learner lookup is scoped by owner ID. `primer_learner` stores nickname and world; `primer_skill_state` stores aggregate attempts, correct count, streak, smoothed estimate, and review time; `primer_attempt` stores item ID, outcome, nonce, and time; `primer_hint_used` stores only the nonce and time of a revealed clue; `primer_journey` stores chapter, beat, reviewed choice IDs, and a monotonic version. No raw child answers or free-form story text are persisted.

The story catalog and question catalog are versioned code, not an LLM response. The server sends display text and a signed challenge to the browser, never the answer key. The browser renders server text with `textContent`. Mutations are account-scoped and the challenge's version is checked against the database before a step advances. Learner removal and account deletion remove journey and evidence rows.

The selector uses a skill graph with prerequisites, due review times, and a smoothed practice signal `(correct + 1) / (attempts + 2)`. This is a prioritization heuristic, **not** a psychometrically calibrated knowledge estimate. The current `Strong` label requires four attempts, a high estimate, three correct answers without an in-app clue, and two recent answers without an in-app clue; it is still a UI description of practice, not a validated reading or math level. The system cannot tell whether an adult helped. The next algorithm iteration should model item difficulty, distinguish different kinds of adult support, and evaluate whether its choices actually improve learning against a nonadaptive path.

## Engineering expansion plan

- **Content:** A content package needs stable IDs, skill, difficulty, prerequisites, answer rubric, hint ladder, accessibility text, cultural review, locale, and version. Existing attempts should keep the content version that produced them. Four items per skill are enough for a bounded prototype, not for a durable personalized curriculum.
- **Assessment:** Introduce teacher-reviewed diagnostic sets and alternate forms, then analyze errors by misconception. Writing should accept multiple correct forms only when a reviewed rubric can score them reliably; otherwise keep the task explicitly bounded.
- **Story engine:** Author branch consequences that alter scenes and tasks, not only a line of dialogue. Add narrative state invariants and content linting so every branch is reachable, coherent, and educationally appropriate.
- **Infrastructure:** Keep account-scoped relational evidence as the source of truth. When content volume grows, move reviewed content to a versioned service and cache immutable bundles; keep scoring and journey transitions transactional. Add metrics for latency, completion, item failure, and adult handoff without emitting child answer text to telemetry.
- **Safety and learning:** Before child-facing generation or collecting personal context, add verified adult roles, consent and deletion paths, age-appropriate output constraints, audit trails, educator red-team review, and an evaluation showing learning gain. Generative output must never silently determine mastery.

The [IES reading](https://ies.ed.gov/ncee/wwc/PracticeGuide/21), [early math](https://ies.ed.gov/ncee/wwc/practiceguide/18), and [elementary writing](https://ies.ed.gov/ncee/wwc/PracticeGuide/17) guides inform the curriculum direction. The current catalog covers only a narrow portion of those recommendations. The [FTC COPPA guidance](https://www.ftc.gov/business-guidance/resources/complying-coppa-frequently-asked-questions) is the starting point for a dedicated review before this becomes an independent under-13 product or collects voice, photos, or richer child context.
