# Remaining work and acceptance ledger

Statuses refer to this local checkpoint. **Verified Working** is limited to the evidence cited; it does not mean production verified. **Implemented but Unverified** means code exists without the required live/operational proof. **Partially Working** means only part of the full requirement is delivered. **Blocked** requires an external prerequisite. **Not Implemented** means the requested capability is absent.

| Requested acceptance item | Status | Evidence / remaining work |
|---|---|---|
| Existing features audited | Partially Working |643 source route decorators + full regression suite + scoped feature matrix; live whole-product audit incomplete |
| Major broken functionality repaired | Partially Working |Navigation, scripts, summaries and discovered permission/race/evidence defects fixed; whole-platform vendor/device reliability not demonstrated |
| Redundant learning features consolidated | Partially Working |One entry hub and reused endpoints; legacy pages/contracts preserved; duplicated underlying systems not fully normalized |
| Unified Learning Hub exists/functions | Verified Working |Local API + actual desktop/mobile browser workflows |
| District/school policy server enforcement | Verified Working |Hierarchy/scopes, mapped routes/provider adapters/jobs, direct API tests |
| No student restriction bypass anywhere | Partially Working |Covered prompt/Premium/direct/mixed-credential cases deny; legacy reminder/background sender and selected source reads covered; other unmapped/offline/anonymous paths still need audit |
| Tutor promotes authentic learning | Partially Working |Guided-mode instructions and protected concepts; provider reply quality/adversarial benchmark absent |
| Academic integrity evaluated | Partially Working |Strict protection and alternate API tests; two concept explanations, no broad subject benchmark |
| Meaningful study activities | Verified Working |Objectives, method, paper/retrieval checkpoint, persistent session |
| Completion distinct from elapsed time | Verified Working |Server stores self-report with nullable correctness; no timer mastery |
| Focus follows preferences/policy | Verified Working |Voluntary user-selected reset and mapped focus denial; no passive monitoring |
| Device restrictions accurately described | Verified Working |Extension requirement and web/device/MDM limits documented |
| Controlled experimental maturity | Partially Working |Registry/admin beta/flags/internal alpha; approved reviewed-practice opt-in, notice, withdrawal and feedback verified locally; general promotion/legacy inventory incomplete |
| No false Stable quality claims | Partially Working |New course/video maturity honest; legacy Stable values are operational designations, not independently audited maturity |
| Admin-only course generator | Verified Working |Backend admin/flag/institution checks; ordinary/Premium denied |
| Curated authorized resources | Partially Working |Reviewed source links for Python and elementary algebra; no broad retrieval, authorization to ingest/rehost unconfirmed |
| Licensing/attribution respected | Verified Working |Link-only metadata/attribution, no copied source media; future commercial ingestion needs review |
| Genuine structured original lessons | Implemented but Unverified |Required teaching/quiz schemas and fixture persistence; live provider unavailable |
| Original content quality evaluation | Partially Working |Schema validation and human key review; independent correctness/age-quality benchmark missing |
| Persistent course/generation state | Verified Working |Saved blueprint/lessons/job, retry/cancel/expired lease tests |
| Individual original-lesson editing/regeneration | Verified Working |New course revisions preserve originals/evidence; validated edit, one missing-key worker regeneration, review lock, sequence/export, owner/admin/provider gates and browser flows; production/provider output unverified |
| Adaptive recommendations | Partially Working |Interpretable missing/failed/assisted prerequisites and limited due review; fresh reviewed checks, hint accounting and existing adaptive practice path connected; course-specific fresh assessment/error taxonomy incomplete |
| Safe generation recovery | Verified Working |Checkpoint, reservation, lease and in-flight revocation/cancel tests locally; production concurrency unverified |
| Relevant video retrieval | Partially Working |Original educational source video links; no general retrieval/transcript indexing |
| Accurate AI video representation | Verified Working |Unavailable alpha denied; no render falsely claimed |
| Expensive generation controls | Partially Working |Token caps, reservations, one attempt and rate limits; shared monthly school/district token reservations added; no currency/provider reconciliation |
| Future Premium entitlement design | Partially Working |Future entitlement metadata; rollout/billing/consent gates remain process work |
| Premium cannot bypass admin beta | Verified Working |Automated direct endpoint denials |
| Training only if justified | Verified Working |No justified benchmark/data/hardware path; no training run |
| No silent student training | Verified Working |No new dataset export/training pipeline; provider-side terms still require review |
| Canvas integrations tested | Partially Working |Existing automated OAuth/refresh/gradebook tests; real authenticated provider path unverified |
| Google Calendar integrations tested | Partially Working |Existing automated helpers/busy-time/feed tests; live OAuth/event sync unverified |
| Feasible scheduling | Partially Working |Existing engine/audit tests retained; no new school workload/live benchmark |
| Student data appropriately protected | Partially Working |Scope, explicit provider confirmation, minimized evidence, export/deletion controls; new scoped retention command, beta export and direct audit-target anonymization verified; all-store retention/SSO/contract/privacy operations incomplete |
| Critical automated workflows covered | Verified Working |New focused regressions + final full-suite evidence |
| Actual end-to-end tests executed | Verified Working |Real browser/HTTP/storage with clearly mocked external generation; not production/provider end-to-end |
| Security/accessibility reviewed | Partially Working |Independent code review and twenty-one scoped axe/responsive checks for changed learning/admin surfaces; raw manual-review cases, shared/legacy-page audit, real screen-reader testing and penetration testing pending |
| Accurate school documentation | Verified Working |Eleven reports and explicit evidence/limitations |
| Launch blockers identified | Verified Working |This ledger plus privacy/pilot report |

Blocked: approved live Gemini/Vertex setup, institutional authorization and provider audience/data terms, unavailable RTX environment if later justified training requires it. Do not flip contractual flags to bypass these blockers.

Not implemented: universal feature-promotion lifecycle, institutional SSO/roster onboarding, all-store retention scheduler and arbitrary legacy/nested audit JSON anonymization, generalized resource discovery, curriculum-wide concept graph, fresh course-specific assessment generation, scheduler evidence normalization, new hub offline synchronization, model training, actual video rendering, calendar-ingested assessment schedules, currency spending reconciliation and packaged-client/MDM changes.

Priority next engineering work: close unmapped/background policy coverage; complete privacy/retention/SSO/provider prerequisites; validate restored PostgreSQL schema/deletion/concurrency and deploy a supervised worker; run real vendor/account/device workflows; assess content quality and accessibility; then broaden the catalog/adaptation. Only after those gates consider a student pilot or Premium course rollout.

Additional course requirements remain incomplete: broad subject/depth/time/profile extraction, formatting beyond the five saved teaching sections, diagrams/media, independently executed mathematical/code assessments, module restructuring after generation, curriculum-wide quality scoring and broken-link retrieval fallbacks. Sanitized Markdown/math/code/tables in the saved teaching sections now pass adversarial Chromium checks; see [content rendering](13-content-rendering.md). Original lesson edits and regeneration now preserve separate course revisions; see [revisions](14-lesson-revisions.md). The link catalogs and human review are bounded working features, not substitutes for those acceptance criteria. The full master brief is not complete or deployment approved.
