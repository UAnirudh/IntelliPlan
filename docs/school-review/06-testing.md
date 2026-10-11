# Testing report

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

## Live provider attempt and exclusions

`python scripts/verify_learning_provider.py --run-live` makes at most one blueprint and one lesson call on a disposable database, with synthetic content. The recorded local check returned503 in the blueprint path: contract-approval flag false, Vertex project/location absent, no eligible Gemini service. External generation requests:0. It correctly exits nonzero. [Live provider evidence](evidence/live-provider-results.json).

No authenticated production Canvas/StudentVUE/calendar flow, real packaged mobile/desktop/extension workflow, real video render, screen-reader audit, penetration test, PostgreSQL concurrent load/restore/migration or educational-quality benchmark was completed. Twenty-four skipped legacy tests remain skipped; thousands of legacy warnings are not a clean operational-health result. Tests are evidence of the contracts they exercise, not legal/school certification.
