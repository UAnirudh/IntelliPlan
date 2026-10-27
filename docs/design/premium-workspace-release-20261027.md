# Premium study workspace and quiz reliability

This release uses a quiet charcoal/mint default palette with a matching light
theme, generous spacing, rounded composers and accessible study-mode controls.
Saved palette/accent choices remain authoritative. Study, Learn, the separate
Learning Hub and Command Center share the same surface system.

The response renderer adds labelled code copying with selection recovery, native
table semantics inside keyboard-scrollable wrappers, Gemini text parts and
preservation of structured domain objects. Existing TeX envelope repair, display
math and chemistry rendering from main are retained.

Quiz input and model-output contracts reject malformed questions, invalid scores
and inconsistent verdicts. Word overlap no longer upgrades an incorrect verdict.
Retry preserves the answer without awarding completion credit. Session/question
guards cover late grader, reward, mastery-display and completion responses.
Failed reward/streak saves no longer fabricate local success. The first mastery
review initializes database defaults before updating its counters. Client review
and streak dates use the device calendar date rather than UTC.

## Verification against the isolated release

- Full hermetic Python suite: **5403 passed, 15 skipped**.
- Browser-runtime tests: **82 passed**.
- Premium Chromium verification: **20 light/dark desktop/phone layouts and 11
  interaction flows**, including retries, double submission, stale responses,
  full five-question session persistence, reward totals and streak fields.
- Tutor runtime: **5 flows passed**, including actual browser audio decoding.
- Chemistry renderer: desktop and phone equation/JSON, keyboard/overflow and
  hostile-content checks passed.
- Scoped Python Ruff and Git whitespace checks passed. Two independent read-only
  reviews found no remaining concrete release defects.

The premium browser verifier runs in CI against isolated accounts and blocks
external browser requests. No migration or production records are included.

## Evidence limits

Shape validation does not establish factual correctness of generated content.
A synthetic tutor check using production provider configuration completed its
desktop request; its following phone request hit provider rate limits and safely
returned `safety_unavailable`. That provider-capacity limitation is unresolved;
real connected-LMS workflows and backend mastery timezone parity remain separate
acceptance items. Existing institutional policy checks remain in force.
