# IntelliPlan Foundations: the first chapter

The [vision and architecture](vision-and-architecture.md) describes the longer product path and the current branching journey. This document records the initial baseline; the current release also includes four chapters per world, a story recap, one alternate clue after a miss, and separate evidence for answers given with a revealed clue.

## Product promise

IntelliPlan already connects a student's plan, grades, study sessions, notes, and Plani tutor. Foundations adds a guided path for early reading, sentence building, and arithmetic inside that same workspace. Its distinctive loop is **practice → observed evidence → next activity → adult handoff**. A child can return for a short chapter; an adult can see which skill needs practice and bring that evidence to a teacher. We do not describe an unverified child response as mastery or use an AI impression to mark an answer correct.

The Primer is a direction, not a feature claim. The first release is a curated, text-based foundation for an account holder to use with a learner. It is a supplement to adults and classroom instruction. It does not claim private-tutor quality, diagnose a learning difference, or teach ethics and character.

## First release

1. An account holder creates a learner using a nickname and a chosen story world. No date of birth, school, photo, voice recording, or child's full name is requested.
2. A short activity asks one bounded question in reading, writing, or arithmetic. The world changes the scene and examples, while the target skill and answer remain stable.
3. The server checks the answer against a reviewed item, stores only the outcome, and returns a specific hint. The answer key never appears in the challenge response.
4. An evidence-based skill map selects prerequisites, revisits errors, and spaces successful practice. The UI calls a skill **practicing** until enough attempts support a stronger label.
5. The adult view shows attempts, a next step, and a print-friendly progress note. Links connect the experience to Plani and the existing study workspace.

## Learning design and algorithm

The reviewed catalog has three skills per domain: sound and word recognition → sentence meaning → short passage; sentence order → capitals and punctuation → clear sentence; counting → addition → word problems. A skill unlocks when its prerequisite has two correct answers without a revealed clue. Each story chapter sequences reading, writing, and arithmetic. Within a domain, the selector prefers an unlocked, due skill with little evidence or recent errors. An item rotates within its skill rather than repeating immediately.

Each scored attempt updates a Beta(1,1) evidence estimate: `(correct + 1) / (attempts + 2)`. This is a practice signal, not a probability that a child "knows" the concept. The UI labels a skill **growing** after two correct answers without an in-app clue and **strong** only after at least four attempts, an estimate of 0.75 or higher, three correct answers without an in-app clue, and a two-answer streak without that clue. Adult help is not measured. A wrong answer offers one alternate item for the same skill and schedules review immediately; correct streaks schedule review after 1, 3, then 7 days, while a clue-assisted answer returns after one day. The question remains available even when every skill is waiting.

## Architecture and operations

- Flask blueprint: authenticated JSON endpoints for learners, activity, answers, and progress. Ownership is derived from the signed-in account for every lookup. The server signs short-lived challenge tokens and rejects replayed answers.
- SQLAlchemy Core tables: learner, skill state, attempt evidence, clue use, and journey state in the existing database. Attempts store item ID, correctness, and time; raw child writing is not retained. Account-level delete removes this feature's learner and evidence rows.
- Static catalog and selector: versioned in code for review and deterministic tests. No LLM is in the scoring path. Existing Plani remains available for a parent or teacher to discuss a skill, without passing a child's raw answer into the chat automatically.
- Frontend: one responsive Jinja page using IntelliPlan's global tokens, navigation, and phone tab bar. It sits immediately below Command Center in the sidebar. Accessible forms, visible feedback, keyboard operation, and reduced-motion behavior are required.
- Scale: indexed owner and learner IDs keep reads bounded; a single database transaction records each result. The catalog can later move to a reviewed content service without changing the learner evidence contract.

## Expansion path

After real use and teacher review, add voice-supported decoding, handwriting evaluation with human review, richer branching stories, curriculum alignment, and family/teacher sharing permissions. Before expanding personalization, measure learning gain with pre/post assessments and compare against a nonadaptive path. Add safety review, retention controls, and verified adult roles before collecting richer child context or enabling free-form child-facing AI.

## Evidence and launch gates

The [What Works Clearinghouse reading guide](https://ies.ed.gov/ncee/wwc/PracticeGuide/21) recommends linking sounds to letters, teaching decoding, and reading connected text. This release checks bounded recognition and comprehension; it does not assess fluent reading or decoding aloud. The [early math guide](https://ies.ed.gov/ncee/wwc/practiceguide/18) supports developmental progressions and monitoring, which informed prerequisite gates and recorded attempts, but the specific selector and thresholds here have not been validated as an intervention. The [elementary writing guide](https://ies.ed.gov/ncee/wwc/PracticeGuide/17) includes sentence construction alongside the wider writing process; the release covers only bounded sentence mechanics.

This release requests only a nickname and a chosen world, stores no raw answer text, excludes Foundations from product analytics and survey prompts, and lets the account holder delete a learner. Before marketing it as an independent under-13 child product or adding voice, photos, free-form AI, or school sharing, complete a review against the [FTC's COPPA guidance](https://www.ftc.gov/business-guidance/resources/complying-coppa-frequently-asked-questions) and verify the adult and consent flows end to end.

## Acceptance checks

- An unauthenticated request cannot read or write learner data; one account cannot access another account's learner.
- Invalid answers, stale tokens, and replayed tokens cannot create attempts.
- Correct and incorrect answers change the skill estimate, due date, and next activity as specified.
- Reading, writing, and arithmetic are all reachable; the child sees a hint and the adult can view and print progress.
- Desktop and phone layouts fit the existing shell and keep the new destination immediately after Command Center.
