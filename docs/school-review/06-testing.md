# Testing report

## Latest study-session PostgreSQL proof — 2026-10-12

The restored synthetic PostgreSQL scope passes **199 tests,1659 warnings in20.49s**, exit0. Nine guarded PG cases now include actual concurrent signed-in HTTP start/start and complete/resume behavior. The existing uniqueness/error handler and revision guard preserve one open session and prevent completion resurrection; no app change was needed. The remaining scope includes the fixed original bank, worker, learning and account deletion. A test-only existing-flag setup was corrected for restored databases. [Result](evidence/pytest-postgres-study.txt), [scope and limits](24-study-postgres.md). The fixture server was stopped and its port closed. The complete regression/browser results below predate only these test changes and are not a newly rerun full suite.

The separate SQLite learning/concurrency scope passes145 tests/9 skipped/1541 warnings in11.00s, exit0. Explicit guarded PG tests skip under SQLite as intended. [Result](evidence/pytest-study-sqlite.txt). Changed test Ruff and whitespace checks pass.

## Latest curated-check verification — 2026-10-12

The final full disposable SQLite suite passes **4245 tests,31 skipped,10416 warnings in260.05s**, exit0. The focused bank/learning/worker/deletion scope passes **190 tests,1579 warnings in12.06s**. It adds trusted repository Python and exact-arithmetic proofs for all twelve fixed keys, schema/catalog coverage, safe-copy boundaries, review/scoring/provenance, edited-objective fallback and replay/policy denials. [Full result](evidence/pytest-curated-full.txt), [focused result](evidence/pytest-curated-targeted.txt).

The fresh browser harness exits0 with17 HTTP/storage workflows,29 responsive/axe checks and no JavaScript errors. A real mobile audit found a code-scroll keyboard defect; focusable named code/math regions now pass the rerun, and ArrowRight actually scrolls a long example at320px. Scoring persists assisted status, keeps public keys hidden and denies replay. Independent Python/JavaScript/spec reviews pass for this bounded slice and the subsequent renderer correction. Provider output remains fixtures, screen-reader/manual checks remain open, and the previous PostgreSQL proofs are separate from this SQLite run. [Scope and limits](23-curated-checks.md). Earlier results below are historical checkpoints.

## Latest retention and PostgreSQL verification — 2026-10-12

The current full disposable SQLite suite passes **4223 tests,31 skipped,10284 warnings in267.61s**, exit0. Seven skips are explicit-local PostgreSQL tests, executed separately. The scoped SQLite run passes283 tests before the final User lock; the full run includes it. A fresh PostgreSQL17.9 scope passes **290 tests,2401 warnings in57.50s**. Backup/restore comparison matches127 tables,692 synthetic rows and124 sequences. The restored fixture passes **22 worker/concurrency checks,71 warnings in17.26s**, including the final explicit overlapping lock-order assertions. [Full result](evidence/pytest-retention-full.txt), [PG result](evidence/pytest-postgres-targeted.txt), [restored result](evidence/pytest-postgres-restored.txt), [comparison](evidence/postgres-restore-comparison.txt).

The fresh browser harness exits0 with16 HTTP/storage workflows,27 responsive checks,27 scoped axe audits and no JavaScript errors. External output remains fixtures and manual accessibility cases remain. Independent Python/source and scoped spec reviews completed after race fixes; changed Python Ruff, compilation and whitespace checks pass. The isolated PG server was stopped and its port closed. No production data/provider operation occurred. [Scope and reproduction](22-retention-postgres.md). Earlier results below are historical checkpoints.

## Latest LMS policy verification — 2026-10-11

Subsequent worker CLI checks pass **247 tests,2042 warnings in32.08s**, including actual curated queue checkpoint/resume and declared lifecycle stubs. Cold CLI help succeeds with explicit disposable settings; changed Python checks and independent Python/spec reviews pass. The full regression below predates that CLI addition. [Worker results](evidence/pytest-worker-targeted.txt), [operation and limits](21-worker-operation.md).

The completed full suite passes **4183 tests,24 skipped,9949 warnings in291.70s**, with process exit0. The focused learning/Plani/course-tracking/HAC/timeline/Notion/LMS-repository suite passes **434 tests,2505 warnings in35.54s**. A preceding full run was interrupted and is not counted as completed. [Full results](evidence/pytest-lms-policy-full.txt), [focused results](evidence/pytest-lms-policy-targeted.txt).

The fresh Chromium report records16 HTTP/storage workflows,27 responsive checks and27 scoped axe checks, with no JavaScript errors. It exercises the learning/admin surfaces; additional legacy LMS operations use signed-in stored-fixture HTTP/helper regressions. External generation/integration calls are stubs, raw manual accessibility cases remain, and no screen-reader or whole-app conformance claim is made. Independent source, JavaScript and scoped spec reviews completed after the LMS fixes. Ruff on changed modules, Python compilation, JavaScript parsing and whitespace checks pass. [Boundary report](20-lms-policy-boundaries.md). Earlier results below are historical checkpoints, not the current test count.

Run from the repository root in PowerShell, with a disposable database. The root `conftest.py` defaults to in-memory SQLite only when `DATABASE_URL` is absent; explicitly override that variable when reproducing. Never point destructive test fixtures at a real student database.

```powershell
$env:DATABASE_URL='sqlite:///:memory:'
$env:APP_BASE_URL='https://intelliplan.tech'
python -m pytest -q --disable-warnings --maxfail=8
python -m ruff check intelliplan/learning scripts/inventory_learning_routes.py scripts/verify_learning_provider.py
node --check static/js/learning_hub.js
node --check static/js/school_admin.js
git diff --check
```

Continuation full-suite result: **4020 passed,24 skipped,8517 warnings in245.92s**, preserved in [pytest-final.txt](evidence/pytest-final.txt). Baseline:3949 passed,24 skipped,2 failed in398.03s. Those two failures expected production calendar redirects but inherited a localhost APP_BASE_URL. Rerunning with the explicit production-origin test setting removed the environment failures. An intermediate implementation exposed one real hub discoverability regression; preserving the active-work entry point fixed it. Later run:3997 passed,24 skipped in275.83s. This run includes the governance, shared-scored-evidence and algebra continuation. The earlier checkpoint passed4005 tests. A final reminder/marketing notification-guard expansion was then exercised by the focused suites below.

Focused learning/provider/discoverability checks passed85 tests before the final session-race regression was added. Reviewer reran13 relevant critical tests successfully. A final targeted run (learning hub, provider fallback, account deletion, active discoverability) passed **98 tests in11.03s**, including an additional export/deletion test added after the full run. That test verifies actual new course/job/lesson/evidence/session/membership cleanup, audit actor anonymization, owner isolation and continued export after beta revocation. [Targeted results](evidence/pytest-targeted.txt). Final Ruff, JS parsing and whitespace outputs are preserved in [static checks](evidence/static-checks.txt).

New coverage includes anonymous/private boundaries, premium/admin flag denial, district precedence, tenant isolation, policy optimistic versions, scoped roles/audit, unknown/bad settings, strict assessment and alternate routes, mixed cookie/token identities, media provider denial, personalization consent, job cancellation/leases/retry/token budgets/in-flight revocation, answer-key hiding, human review, server comparison, replay denial, self-report versus correctness, session restore/time/pause/resume/concurrent completion, export and cross-origin mutation denial.

## Continuation verification — 2026-10-10

Focused learning/account-deletion tests: **80 passed,719 warnings in7.96s**. Notification/email eligibility/sending/weekly/timezone tests: **210 passed,602 warnings in8.84s** after the last notification guard change. These add explicit preview approval/current-version consent/age/revocation/withdrawal, source scorer hint/replay/shared evidence, feedback/export, shared parent/child token ceilings, scheduled assessment windows, retained completion clocks across batches, open-session preservation, audit target anonymization, selected grade-source denial, document versus calendar OAuth purposes and account/feed revocation. [Targeted learning results](evidence/pytest-targeted.txt), [notification results](evidence/pytest-notifications.txt).

Run `python scripts/verify_learning_resources.py` to repeat public link checks. All19 unique source/terms URLs returned HTTP200 on2026-10-10. This proves reachability at that time, not redistribution permission, site accessibility or instructional quality. No source lesson text or videos are cached. [Public link results](evidence/resource-links.json).

## Actual browser execution

```powershell
python scripts/verify_learning_hub.py
```

If Playwright's bundled Chromium is unavailable, set `PLAYWRIGHT_CHROMIUM_EXECUTABLE` to an installed Chromium-compatible executable. The recorded run used Chromium headless shell1243. The script creates a temporary SQLite database and synthetic adult accounts before importing the app; it never uses production student records. AI output is deliberately mocked and identified as such.

The continuation passed nine desktop/mobile workflows (see the JSON for exact scope), including algebra multisource assembly, shared budget/window/retention controls, scoped preview approval and real reviewed-bank consent/grade/hint/scoring/feedback/withdrawal/export. Original AI output remains fixtures. Earlier passed workflows: protected concept explanation through real HTTP; study start/pause/reload/resume/paper checkpoint/completion; curated outline/worker/refresh/review; fixture original generation/answer-key review/scoring/progress; institution policy save and audit entry; mobile layout, keyboard focus smoke, and ordinary-student direct beta/tutor denial. Zero JavaScript page errors. [Browser results](evidence/browser-results.json) and [mobile screenshot](evidence/learning-hub-mobile.png).

## UI continuation — 2026-10-10

After engineering checkpoint `ca0b8b0`, the UI-focused learning/discoverability suite passed **77 tests,734 warnings in12.58s**. The expanded browser run passes **ten workflows and sixteen UI checks**, with scoped optional axe-core4.14.0 audits. It covers 320/390/768/1440px layouts, light/dark themes, enabled form/button target geometry, one main landmark, question starters without automatic requests, restricted-activity fallback, native course outlines and actual saved-lesson progress. The all-disabled fixture verifies there is no blocked progress request. Raw axe `incomplete` entries remain visible; no screen-reader or whole-app conformance claim is made. See [the UI report](12-ui-design.md), [regression results](evidence/pytest-ui.txt) and [browser JSON](evidence/browser-results.json).

## Course rendering and revision continuation — 2026-10-10

The full suite passed **4023 tests,24 skipped,8601 warnings in346.39s** before the last export-order correction. The latest focused learning/account-deletion/discoverability run passed **91 tests,827 warnings in27.39s**, including the export correction. [Full results](evidence/pytest-course-revisions.txt), [latest focused results](evidence/pytest-course-targeted.txt). JavaScript syntax, changed Python Ruff and whitespace checks pass.

The latest disposable Chromium run passes **twelve HTTP/storage workflows, twenty-one responsive checks and twenty-one scoped axe audits**, with zero JavaScript errors. It adds sanitized persisted Markdown/math/code/table rendering, hostile-content/no-untrusted-request assertions, malformed/recursive math, macro isolation, expression/input bounds, missing-library/helper fallback, native editing/focus/validation, and preserved single-lesson regeneration. Rich content and the editor are checked at320px. Automated axe reports contain no violations; raw `incomplete` items include contrast and other manual-review cases. Read-only reviewers also exercise deferred save failure, concurrent regeneration blocking, draft preservation and stale selection/status. [Renderer report](13-content-rendering.md), [revision report](14-lesson-revisions.md), [latest browser JSON](evidence/browser-results.json).

## Inspectable course-request continuation — 2026-10-10

The latest full suite passes **4025 tests,24 skipped,8625 warnings in253.08s**. The focused learning/account-deletion/discoverability suite passes **93 tests,846 warnings in10.54s**. Coverage adds bounded preference extraction metadata, draft editing/export, atomic stale-save rejection and the exact worker context without account identity. Deferred-response browser reviews verify that generation waits for saves and failed saves preserve drafts. [Full results](evidence/pytest-profiles-full.txt), [focused results](evidence/pytest-profiles-targeted.txt).

The current disposable Chromium run passes **thirteen HTTP/storage workflows and twenty-three responsive/scoped axe checks**, with zero JavaScript errors. It adds persisted request details at1440px and320px to the previous rendering/revision workflows. Provider output remains fixtures; raw accessibility manual-review cases remain recorded. [Current browser results](evidence/browser-results.json), [request details](15-course-request-details.md).

## Replacement-check and module checkpoints

The subsequent replacement-check full regression passed4032 tests /24 skipped before four institution-denial cases and module editing were added. The latest module-focused regression passes115 tests with1106 warnings in14.73s. The current Chromium run passes15 workflows and27 responsive/scoped axe checks, including deferred refusal/loading races and module reorganization. [Replacement-check results](evidence/pytest-check-variants-full.txt), [module results](evidence/pytest-modules-targeted.txt), [module report](17-module-organization.md).

## Latest AI policy and consent verification — 2026-10-10

The latest full regression passes **4068 tests,24 skipped,9053 warnings in270.90s**. The focused learning/study-input/account-deletion/discoverability suite passes **146 tests,1205 warnings in24.51s**. The initial identity-enforcement full run exposed eight study-input fixtures without real login sessions and two formerly permitted guest-generation expectations. Signed-in fixtures now use actual sessions; guest cases require401 without a provider call. Normal input bounds and mode behavior still pass after authentication. [Final full results](evidence/pytest-policy-full.txt), [focused results](evidence/pytest-policy-targeted.txt), [initial failures](evidence/pytest-policy-initial-failures.txt).

The current disposable Chromium run passes **16 HTTP/storage workflows and27 responsive/scoped axe checks** with zero JavaScript errors. AI availability is forced and provider calls trapped in new policy tests, proving denials and numerical fallback do not merely rely on missing configuration. No live provider calls are made. Independent review of these continuations remains unavailable due reviewer execution limits. [Boundary report](18-ai-policy-coverage.md), [browser results](evidence/browser-results.json).

## Plani/internal-source continuation — 2026-10-11

The latest full regression passes **4138 tests,24 skipped,9472 warnings in259.71s**. The expanded focused suite passes **402 tests,1978 warnings in28.47s**, covering Plani, institution policy, course tracking, clock parsing, retrieval, LMS cache, Canvas, HAC and timeline behavior. Tests use disposable SQLite and provider stubs; they include real nested calendar placement, cross-account profile-ID collision, policy/consent/source revocation, background credential/cache boundaries and per-call allowance/failure behavior. [Full results](evidence/pytest-plani-full.txt), [focused results](evidence/pytest-plani-targeted.txt), [behavior report](19-plani-data-boundaries.md).

The fresh browser harness exits0 with **16 HTTP/storage workflows,27 responsive checks,27 scoped axe checks and zero JavaScript errors**. Its workflows concern the existing learning/admin surfaces; the Plani HTTP/storage paths are tested in the focused suite. External generation remains fixtures, and raw accessibility manual-review cases remain recorded. The source-only route inventory still has644 literal decorators, and105 source files have raw-byte SHA-256 records. Inventory/hashes do not establish complete policy coverage.

Independent read-only source/helper review completed after finding and fixing malformed-role validation, a nested calendar-helper bypass and source-policy revocation before schedule persistence. Separate standards/spec review attempts stopped at execution limits. A full run started before these last fixes was interrupted; only the completed final run above is claimed for current source.

## Live provider attempt and exclusions

`python scripts/verify_learning_provider.py --run-live` makes at most one blueprint and one lesson call on a disposable database, with synthetic content. The recorded local check returned503 in the blueprint path: contract-approval flag false, Vertex project/location absent, no eligible Gemini service. External generation requests:0. It correctly exits nonzero. [Live provider evidence](evidence/live-provider-results.json).

No authenticated production Canvas/StudentVUE/calendar flow, real packaged mobile/desktop/extension workflow, real video render, screen-reader audit, penetration test, PostgreSQL concurrent load/restore/migration or educational-quality benchmark was completed. Twenty-four skipped legacy tests remain skipped; thousands of legacy warnings are not a clean operational-health result. Tests are evidence of the contracts they exercise, not legal/school certification.
