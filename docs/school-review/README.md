# IntelliPlan engineering delivery — 2026-10-10

This is a locally implemented and tested engineering checkpoint, **not a school launch approval**. The full attached master brief exceeds this checkpoint. The acceptance ledger below and remaining-work report explicitly identify incomplete requirements.

The existing application was exercised through its regression suite and a source inventory of 643 literal route decorators. Inspection concentrated on tutoring, study, provider routing, account deletion, institution permissions, course generation, and the relevant integration tests. It did not establish that every production integration or device works.

Implemented: a Learning Hub at `/learning` and `/study-and-learn`; scoped district/school administration at `/school-admin`; additive organization, membership, policy audit, course, lesson, durable generation job, study session, and evidence storage; admin-only course beta; reviewed link catalogs for Python and elementary algebra foundations; schema-validated original lessons; server-scored reviewed checks; persistent study sessions with voluntary resets; shared recent workspace and existing reviewed-practice evidence with delayed-review suggestions; approved opt-in practice with versioned consent, withdrawal and scoped feedback; inherited assessment windows; shared monthly generation token reservations; background notification policy enforcement; dry-run retention and account-deletion audit anonymization.

Repaired: missing hub entry points; scripts failing to load through the shared template; false recording summaries when transcription was absent; mixed cookie/token policy checks; alternate assessment-generation paths; worker cancellation and policy-revocation races; session resurrection by stale resume requests; duplicate quiz evidence; course-selection and organization-selection UI races; course-builder progress overstating independence after answer-key access.

Verified: local automated permission, privacy, scoring, recovery and regression tests, and actual Chromium workflows against a disposable database. Browser course generation uses declared fixtures. The live original-course check returned 503 before external generation: Gemini's existing contractual approval and Vertex configuration gate is not satisfied locally. No video was generated, no model was trained, and no production system was changed.

## Required delivery reports

1. Executive summary: this file.
2. [Feature audit matrix](02-feature-audit.md).
3. [Consolidated architecture](03-architecture.md).
4. [Course generator technical report](04-course-generator.md).
5. [Beta feature report](05-beta-features.md).
6. [Testing report](06-testing.md).
7. [Performance report](07-performance.md).
8. [Privacy and security report](08-privacy-security.md).
9. [Model and video investigation](09-model-video.md).
10. [School adoption readiness and administrator guide](10-school-readiness.md).
11. [Remaining work and acceptance ledger](11-remaining-work.md).
12. [UI design checkpoint](12-ui-design.md).
13. [Safe course content rendering](13-content-rendering.md).
14. [Preserved lesson revisions](14-lesson-revisions.md).
15. [Inspectable course request details](15-course-request-details.md).
16. [Preserved replacement knowledge checks](16-replacement-checks.md).

The engineering checkpoint was saved before a subsequent [Learning Hub and institution UI pass](12-ui-design.md), informed by live Skydive, Cluely, Linear and Notion references. It adds theme-aware layouts, useful question starters, course preparation/progress and grouped settings while preserving beta notices and policy gates. Ten browser workflows and sixteen scoped responsive/accessibility checks accompany the UI work. The broader acceptance ledger remains unchanged in scope.

Evidence is preserved in [evidence](evidence/). Source inventory is not a proof that every route is governed. Existing assignments, schedule storage, accounts, reviewed tutor checks, Primer and Family records were not replaced. Account deletion includes the new learning records. Course flags are absent/disabled by default; enabling beta requires a deliberate platform operation.

The subsequent engineering continuation adds sanitized Markdown/math/code/tables in five saved teaching sections, individual original-lesson edits/regeneration through preserved course revisions, and inspectable request preferences. The latest full run passes4025 tests with24 skipped; the focused run passes93 tests. The current disposable browser run passes13 workflows and23 scoped responsive/axe checks with zero JavaScript errors. External output remains fixtures, and raw accessibility manual-review cases remain recorded. Further cosmetic UI work is deferred while the broader engineering requirements and external launch gates remain incomplete.

No commit in this checkpoint constitutes institutional permission, provider-contract approval, legal certification, validated educational effectiveness, WCAG conformance, or production deployment verification.
