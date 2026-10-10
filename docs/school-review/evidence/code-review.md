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
