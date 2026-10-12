# Independent review record

Baseline commit: `ea1b1fadb0672512cc366713a8228c89ca847f7f`. Reviewed local changes and new files on `codex/school-learning-hub`, not a deployed release. The code-review skill's parallel review process authorized the independent reviewers.

## Standards

Backend review identified credential precedence mismatches, an alternate assessment resummarization path, worker policy/cancellation persistence races and stale session resume. Corrections use the actual endpoint authenticator, shared provider policy, conditional leases and optimistic sitting revisions. Final critical backend re-review reported no unresolved defects and passed13 relevant regression tests. Ruff passed the learning package; the final check includes new scripts/tests too.

Browser review identified nested script loading, persisted-session recovery, answer replay/UI feedback, organization save targeting, answer-key independence overstatement and asynchronous course selection/mutation refresh races. Scripts now load through the content block, sessions restore from the server, scored feedback persists, admin saves retain the selected organization, and all builder checks are assisted. Course read and mutation rendering use selection sequence guards. Both delayed-read and delayed-mutation reproductions passed after the final corrections, preserving the most recently selected course and suppressing stale status. Recorded Chromium workflows pass with zero JavaScript errors; this does not establish comprehensive accessibility.

## Spec

Final spec reviewer found no remaining HIGH/CRITICAL spec findings in the prioritized implementation. A LOW documentation inaccuracy described HTML as discarded rather than inertly rendered; it was corrected. The reviewer found the reports appropriately candid about partial scope, live-provider refusal, local-only fixtures and missing production/privacy/accessibility/device/content-quality work. A request to preserve linked evidence files was completed.

Explicitly deferred requirements remain in the acceptance ledger and are not converted to completed delivery by this approval. A fresh complete product-wide security audit was not performed.

Summary: no unresolved high-severity findings in the reviewed implementation; the broader master brief remains partially delivered as documented. Standards review does not establish spec completeness, and spec review does not establish deployed reliability.


## Continuation review — 2026-10-10

Independent reviewers found and verified fixes for selected LMS source gates, Drive versus calendar OAuth callbacks, published calendar-feed revocation, legacy reminder polling, child budgets incorrectly limiting parent counters, lost completion timestamps across retention batches, stale beta notice reporting, and preview withdrawal/scoring/grade-selection races. Hint/answer feedback is announced in the status region. Recent scored history reuses the existing adaptive reducer, so an old failure no longer creates a stale review reminder after later independent success; delayed success retains its existing three-day interval. The focused regression passes. Final backend re-review reports no further actionable defects. No real student data was deleted; all review reproductions use disposable fixtures.

Final deferred-response browser re-review verifies old controls cannot score while a replacement loads, stale profile responses cannot overwrite a grade choice, grade/save/next remain locked during scoring, and withdrawal failures preserve feedback. No remaining actionable findings in that focused review.

## UI continuation review — 2026-10-10

The independent browser reviewer exercised question starters, request failures without phantom history, preservation of a newer draft, busy-state cleanup, draft outline updates, generation/cancel/retry/review and persisted lesson progress. The spec reviewer found progress was not included in capability filtering: with every activity denied, the fallback fetched a blocked progress API. The correction gates progress by study and shows an explicit access explanation; a second correction clears contradictory status copy. A disposable browser fixture verifies that state makes no progress request. Final browser re-review found no further actionable defects; the Python reviewer found the verifier preserves disposable storage/provider boundaries and reports its scoped audits honestly. Both JavaScript syntax checks and verifier Ruff check pass. Broader deferred requirements remain in the acceptance ledger.

## Course rendering and revision review — 2026-10-10

Independent source/browser review verifies raw-HTML-disabled Markdown, trusted-URL denial, sanitized fragment insertion, macro isolation and bounded math/source fallback. The browser reviewer caught missing-helper whitespace collapse; fallback class/state now preserve lines. Original-course revisions preserve source content/evidence and copied items require review. Backend review caught a regenerated first lesson appearing last in display/review/export; all three now follow blueprint positions with regression assertions. Browser review caught concurrent save/regenerate and newer-input loss plus skipped keyboard fields; the shared workspace lock, stale status guard and focused editor now pass deferred-response/keyboard reproductions. Native quiz fields serialize the visible option correctly, retain drafts after server400 and use required-field validation. Final reviewers report no remaining actionable findings in these changes. The complete disposable app verifier passes12 workflows/21 responsive and scoped axe checks with zero JavaScript errors; raw manual-review cases remain. This review does not establish production, provider quality or full brief completion.

## Request-details continuation review — 2026-10-10

Independent backend/spec review finds the recognized dimensions bounded, unknown fields removed, legacy absent profiles compatible, token reservations inclusive of UTF-8 preference input, and stored account/evidence data absent from the new lesson payload. The browser reviewer reproduced generation starting while an outline save was pending; a shared workspace mutation lock now serializes those actions and prevents newer-input loss. Atomic draft/revision updates reject stale saves. Deferred-response re-review verifies no generation request during save, updated revision on repeat save, preserved drafts/restored controls on409 and no browser errors. Final reviewers report no remaining actionable findings in this scoped change. Live request extraction accuracy, feasibility and educational outcomes remain unverified.

## Replacement-check continuation — 2026-10-10

Backend, browser and spec reviewers were dispatched against the request-details checkpoint but failed before review because their execution limit was reached. No independent approval is claimed for this continuation. Root source review and disposable API/browser regressions exercise unchanged teaching/evidence, human review, owner/institution gates, bounded owned ancestry, duplicate rejection, reserved spending, cancellation and policy revocation. The browser run exposed previous-course controls remaining interactive during loading; an inert loading workspace and separate displayed/request identities now preserve drafts after a failed selection and suppress late responses/errors. Deferred refusal/locking tests pass. Independent re-review and live educational quality remain outstanding.

## Module organization and AI boundary continuation — 2026-10-10

Independent review remains unavailable. Root source review and tests verify preserved course organization, unchanged objectives/content/evidence, prerequisite graph validation, owner/admin/revision/incomplete-job refusals and renewed review. The real browser exercises native invalid-duration focus, rejected order retaining drafts and explicit prerequisite changes. A further source audit found task extraction/onboarding outside central tutor mapping and anonymous generation capable of skipping the identity lookup. Mapped generation now requires identity, extractor API honors its existing platform flag, saved onboarding context requires personalization consent, and grade prediction preserves numerical results when AI is disallowed. Provider-call traps verify these boundaries over HTTP with AI forced available. Full legacy/production coverage and independent re-review remain open.

## Curated-check continuation review — 2026-10-12

Independent Python and scoped spec reviewers found no actionable defects in the twelve fixed original activities, exact-objective matching, trusted key verification, worker provenance, hidden public keys, owner review, assisted-only scoring and edited-objective fallback. The JavaScript reviewer verified truthful fixed-English/generated/review labels and textContent insertion. The verifier reviewer checked the real mobile scoring, stored assisted status, key stripping and replay assertions without running the database/browser itself.

Actual browser execution found a serious scrollable-region-focusable violation on a longer code example at320px. Root fixed code and display-math focusability after sanitized insertion. Independent JavaScript review found the fixed attributes safe and the existing focus style visible. The corrected real browser verifies ArrowRight horizontal scrolling of the overflowing code region, then scores the check;17 workflows/29 responsive and scoped axe checks pass with no JavaScript errors. Math keyboard scrolling, screen readers, teacher/age suitability and broad assessment quality remain unverified. Full regression4245 passed/31 skipped; no external provider or production operation occurred.

## Study-session PostgreSQL continuation — 2026-10-12

Independent Python and scoped spec reviewers approve the two controlled concurrent signed-in HTTP tests: database/index conflict handling retains one open session for overlapping starts, and revision checking rejects an in-flight resume after completion. Reviewers verify persisted state/evidence, explicit disposable database guard, bounded waits and event cleanup. No application fix was required. A restored-fixture setup now reuses/enables an existing unique preview flag instead of inserting a duplicate; independent Python review confirms consent/approval/policy assertions remain. Root's restoredPG scope passes199 tests/1659 warnings in20.49s. No arbitrary-load, every-race or production reliability claim is made.
