# Foundations grade paths and item engine

## What makes the product specific to IntelliPlan

Foundations is the beginning of one learning history. A child returns to a world that remembers decisions; each chapter crosses reading, writing, and arithmetic; the next task responds to recorded evidence; the family sees the corresponding skill and can try an offline activity. The family view is the place for counts and skill labels. The learner story stays focused on the scene, the task, and encouraging feedback. This is a companion to teachers and families, not a school grade or diagnosis.

## Grade path

The adult selects kindergarten through grade 8 when creating a learner and can change it later in Family view. Existing learners without a profile start at kindergarten until an adult sets their grade. Each grade has two reading, two writing, and three math skills; kindergarten also retains the original nine early skills. The selected grade is the initial band, not a claimed measured ability. Younger grades use the original story voice; grades 4–8 use older wording and the same stable choice IDs. Within a grade, a second skill unlocks after two correct answers without an in-app clue on the first. After two misses without an independent correct answer in a domain, the next task bridges to the prior grade. After that bridge, the learner returns to the selected grade. A miss also offers one immediate retry of the same skill before the story moves on. Successful answers are scheduled for later review.

| Grade | Reading | Writing | Math |
| --- | --- | --- | --- |
| K | short accounts and clues, plus original sound/sentence/passage path | capitals and complete sentences, plus original sentence path | counting, comparing, joining, plus original counting/addition path |
| 1 | stated details and sequence | subject-verb agreement and complete sentences | addition/subtraction within 20; tens and ones |
| 2 | connected details and text clues | past tense and precision | two-digit addition/subtraction; equal groups |
| 3 | central idea and inference | commas in a series and combining ideas | multiplication/division facts; addition within 1,000 |
| 4 | summary and supporting details | dialogue punctuation and clarity | two-digit multiplication, equivalent fractions, hundredths |
| 5 | cause/effect and evidence | causal connections and transitions | fractions with hundredths, decimals, volume |
| 6 | explanations and claim/support | pronoun case and reasoned connections | unit rates, integers, expressions |
| 7 | claims and evidence | parallel structure and reasoned revision | percent, proportional relationships, linear equations |
| 8 | reasoning and precise evidence | active voice and evidence connections | exponent rules, equations, slope |

These are focused practice strands, not every standard or text type in each grade. The math progression was checked against the official [K–8 mathematics overview](https://www.thecorestandards.org/Math/Content/8/introduction/) and the language progression against the official [K–8 ELA strands](https://www.thecorestandards.org/ELA-Literacy/introduction/how-to-read-the-standards/). Alignment is directional; no standards certification or mastery validation has been completed.

## Deterministic question engine

`primer/generated.py` defines 63 generated skills and 207,941 stable item IDs. The server resolves an ID to a prompt, answer, options, hint, and explanation at request time. Math variants change operands within the skill's bounded range; language variants recombine controlled names, settings, events, and sentence structures. Fixed original items remain available for legacy kindergarten evidence. A learner-specific offset and a coprime step move through each skill's variants before repeating. The answer key is never included in an activity response; a signed, one-hour challenge binds item, learner, journey version, and nonce. The server grades and records only outcome and item ID. Equivalent numeric and fraction forms are accepted.

The pool size is a count of deterministic variants, **not** a count of independently authored questions. Tests check every generated question-and-option pair for uniqueness within its skill, reconstructability, answer presence, and valid choice keys. Sampled items from every skill are graded end to end. These checks catch mechanical defects; they do not replace educator review, reading-level validation, bias review, or field evaluation with children.

## Expansion needed for full coverage

To cover a whole school year at each grade, add independently reviewed passages at several text complexity levels, authentic writing tasks with human or carefully validated feedback, fuller math domains (geometry, measurement, data, and statistics), multilingual and accessibility review, and a standards-to-item coverage matrix. Pilot with educators and families, check learning gains and false signals, then add adaptive diagnostics. The current bounded engine provides varied practice in its listed strands; it should not be presented as a complete K–8 tutor.
