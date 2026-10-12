# IntelliPlan engineering delivery — 2026-10-11

This is a locally implemented and tested engineering checkpoint, **not a school launch approval**. The full attached master brief exceeds this checkpoint. The acceptance ledger below and remaining-work report explicitly identify incomplete requirements.

The existing application was exercised through its regression suite and a source inventory of 644 literal route decorators. Inspection concentrated on tutoring, study, provider routing, account deletion, institution permissions, course generation, and the relevant integration tests. It did not establish that every production integration or device works.

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
17. [Preserved module organization](17-module-organization.md).
18. [Additional AI policy and consent coverage](18-ai-policy-coverage.md).
19. [Plani account-data and internal-operation boundaries](19-plani-data-boundaries.md).
20. [Legacy LMS connection and data boundaries](20-lms-policy-boundaries.md).

The engineering checkpoint was saved before a subsequent [Learning Hub and institution UI pass](12-ui-design.md), informed by live Skydive, Cluely, Linear and Notion references. It adds theme-aware layouts, useful question starters, course preparation/progress and grouped settings while preserving beta notices and policy gates. Ten browser workflows and sixteen scoped responsive/accessibility checks accompany the UI work. The broader acceptance ledger remains unchanged in scope.

Evidence is preserved in [evidence](evidence/). Source inventory is not a proof that every route is governed. Existing assignments, schedule storage, accounts, reviewed tutor checks, Primer and Family records were not replaced. Account deletion includes the new learning records. Course flags are absent/disabled by default; enabling beta requires a deliberate platform operation.

The subsequent engineering continuation adds sanitized Markdown/math/code/tables in five saved teaching sections, preserved lesson edits/regeneration and replacement checks, inspectable request preferences, module organization revisions, and additional AI policy/consent boundaries. The earlier AI-route checkpoint passes4068 tests with24 skipped; its focused run passes146 tests. The current disposable browser run passes16 workflows and27 scoped responsive/axe checks with zero JavaScript errors. External output remains fixtures, raw accessibility manual-review cases remain recorded, and independent re-review of all broader earlier continuations is incomplete. Further cosmetic UI work is deferred while the broader engineering requirements and external launch gates remain incomplete.

No commit in this checkpoint constitutes institutional permission, provider-contract approval, legal certification, validated educational effectiveness, WCAG conformance, or production deployment verification.

The subsequent Plani continuation closes scoped internal-operation and account-data consent gaps, profile ownership, nested calendar placement/export and source-revocation races, retrieval embedding permissions, background LMS cache/credential boundaries, and model-round accounting. The current full regression passes4138 tests with24 skipped, and its expanded focused suite passes402 tests; the scoped independent read-only review completed after three additional fixes. [Behavior and limitations](19-plani-data-boundaries.md). Full-scope acceptance and live operational gates remain incomplete; UI refinement remains deferred.

The subsequent legacy LMS continuation adds seven named source controls and guards connection/callback, cache/credential, refresh/sync and post-fetch publication boundaries. The latest full regression passes4183 tests with24 skipped; the focused run passes434 tests. Independent Python, JavaScript and scoped spec reviews completed. The fresh browser report retains16 workflows and27 responsive/scoped axe checks with no JavaScript errors. [LMS behavior and limitations](20-lms-policy-boundaries.md). No live deployment, vendor authorization or full-brief completion is claimed.
