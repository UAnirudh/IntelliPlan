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

## Live provider attempt and exclusions

`python scripts/verify_learning_provider.py --run-live` makes at most one blueprint and one lesson call on a disposable database, with synthetic content. The recorded local check returned503 in the blueprint path: contract-approval flag false, Vertex project/location absent, no eligible Gemini service. External generation requests:0. It correctly exits nonzero. [Live provider evidence](evidence/live-provider-results.json).

No authenticated production Canvas/StudentVUE/calendar flow, real packaged mobile/desktop/extension workflow, real video render, screen-reader audit, penetration test, PostgreSQL concurrent load/restore/migration or educational-quality benchmark was completed. Twenty-four skipped legacy tests remain skipped; thousands of legacy warnings are not a clean operational-health result. Tests are evidence of the contracts they exercise, not legal/school certification.
