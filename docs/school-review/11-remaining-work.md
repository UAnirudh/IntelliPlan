# Remaining work and acceptance ledger

Latest user priorities are intelligent assignment scheduling, study signals
with external cameras (USB/webcams/capture/phone/network), a question-led tutor
with interactive visuals and Fish Audio, and paid-plan launch readiness while
Stripe verification is pending. The first slice shares the student's local
clock, uses DST-aware calendar constraints, fits new sittings into fragmented
gaps and rejects unverified v2 output. Local tests and scoped review support
this checkpoint; release and full-suite results follow separately. None of
these four broad goals or the original brief is declared complete. See
[smart scheduler calendar work](41-smart-scheduler-calendar.md).

PR92 is merged and live at main75c2784, with4562 hosted unit tests and130
public browser tests passing, exact-revision health verification and Railway
plugin confirmation. The following camera checkpoint implements browser-visible
source selection, safe switching/cancellation/disconnection, phone self-view,
school focus permission gates and preserved cumulative study/session totals.
Local Node, Python and rendered browser persistence checks pass, with scoped
independent review. Direct phone pairing, native network-camera paths, hardware
proof and signal-quality validation remain open. See
[study camera inputs](42-study-camera-inputs.md).

The next local tutor checkpoint shares question-led instructions across text,
adaptive, premium and photo paths; worksheet mode chooses a problem first.
Controlled linear/fraction SVGs and generated quizzes replace executable
generated HTML. Keyboard/mobile rendering, hostile-content handling, audio
cancellation, Fish key aliases and explicit format-to-voice behavior have local
fixture evidence. Live provider pedagogy, Fish playback, full signed-in flows,
broader subject visuals and paid readiness remain unverified or incomplete.
See [question-led tutor](43-question-led-tutor.md). Camera PR93 passed hosted
unit/browser and security checks and is merged at5b15f310; Railway deployment
229030f9-31d5-4386-b64f-6e69fb111cec is SUCCESS. Exact public revision/schema
verification passes and filtered runtime error logs are empty. Post-merge
checks still need confirmation. The separate pre-existing Cloudflare
Workers build remains failed and is not the Railway production target.

Statuses refer to this local checkpoint. **Verified Working** is limited to the evidence cited; it does not mean production verified. **Implemented but Unverified** means code exists without the required live/operational proof. **Partially Working** means only part of the full requirement is delivered. **Blocked** requires an external prerequisite. **Not Implemented** means the requested capability is absent.

Follow-on user requests originally sequenced the engineering brief before UI refinement and multi-country/language support. The latest steering requests further UI adaptation alongside continued original work, with commits and pushes after each major change and visible results. The branch is pushed and linked to PR 86; the original brief, whole-product UI and localization are not declared complete. Localization scope still requires the existing-product audit.

PR 86 is now merged and released: Railway plugin monitoring confirms the exact main merge commit deployed successfully, public health/schema checks pass, and UI assets match. The next checkpoint connects chat schedule creation to the same constraint-aware planner as the Scheduler page and reduces Command Center controls. Conversational schedule repair, whole-product simplification, and country/language support remain ongoing. See [release and chat planning](35-chat-planner-and-release.md).

PR 87 is also merged and released at `a0ae247`: Railway production deployment `4b19c659-8a7f-4870-b81e-5de0452fae3d` succeeded, hosted unit checks passed (4462 passed, 16 skipped), and public live browser checks passed (130 passed, 1 skipped). Chat can preview or save real constraint-aware plan repairs while preserving deadlines and outgoing outcomes. See [conversational repair](36-conversational-plan-repair.md).

The next adaptive recommendation checkpoint filters stored imports, gates mixed histories/audits and indirect availability reads, withholds responses after policy changes, binds narration to current owner consent, bounds explanation retries, and rejects stale override writes. Disposable tests and scoped independent reviews support this slice; production delivery is tracked separately. See [adaptive source policy](37-adaptive-source-policy.md). Universal policy enforcement, live provider/vendor proof and the remaining original acceptance criteria remain open.

The following native dependency checkpoint patches three mobile transitive packages and adds a critical dependency/typecheck gate. Local lockfile audit counts fall from one critical/21 high to zero critical/19 high; desktop remains eight moderate. Unpatched `braces`, `node-forge` and `sprintf-js` findings remain open, including affected parent packages. Packaging/signing/device proof is separate. See [native dependency patches](38-native-dependency-patches.md).

PR88 and PR89 are now merged and live: the Railway plugin confirms both exact merge commits reached SUCCESS, public health/schema checks pass and filtered runtime error scans are empty. Hosted pre-merge tests and security passed, with PR88 post-merge public/unit checks also passing. The next release-verification checkpoint ensures main's browser tests wait for the exact served commit and schema flags, rather than accepting the previous healthy deployment. See [release verification](39-release-revision-verification.md). Broader original requirements and live authenticated/device proof remain open.

PR90 is also merged and live; its post-release workflow verifies the exact public health revision before130 passing public browser checks (1 skipped), following4519 passing unit checks (16 skipped). The following adaptive behavior checkpoint requires fresh personalization consent for history reads/model fitting, removes indirect history fitting from availability reads and corrects the Settings provider-sharing disclosure. Ordinary plan recommendations and active sessions remain usable. See [adaptive behavior consent](40-adaptive-behavior-consent.md). This does not close other legacy local-profile consumers or broader retention/measurement requirements.

The following continuation adds conversational day/time repair through the existing follow-through engine, preserves outgoing past outcomes atomically with saved changes, rejects stale writes, and keeps repeated assignment titles' deadlines separate. Whole-product simplification, localization and the other original requirements remain open. See [conversational repair](36-conversational-plan-repair.md).

| Requested acceptance item | Status | Evidence / remaining work |
|---|---|---|
| Existing features audited | Partially Working |644 source route decorators + full regression suite + scoped feature matrix; live whole-product audit incomplete |
| Major broken functionality repaired | Partially Working |Navigation, scripts, summaries and discovered permission/race/evidence defects fixed; whole-platform vendor/device reliability not demonstrated |
| Redundant learning features consolidated | Partially Working |One entry hub and reused endpoints; legacy pages/contracts preserved; duplicated underlying systems not fully normalized |
| Unified Learning Hub exists/functions | Verified Working |Local API + actual desktop/mobile browser workflows |
| District/school policy server enforcement | Verified Working |Hierarchy/scopes, mapped routes/provider adapters/jobs, direct API tests |
| No student restriction bypass anywhere | Partially Working |Covered prompt/Premium/direct/mixed-credential cases deny; legacy reminder/background sender and selected source reads covered; task/onboarding and Text Dissector identity/policy gates, breakdown AI/Canvas/scheduler/progress/timer boundaries added; unmapped/offline paths still need audit |
| Tutor promotes authentic learning | Partially Working |Guided-mode instructions and protected concepts; provider reply quality/adversarial benchmark absent |
| Academic integrity evaluated | Partially Working |Strict protection and alternate API tests; two concept explanations, no broad subject benchmark |
| Meaningful study activities | Verified Working |Objectives, method, paper/retrieval checkpoint, persistent session |
| Completion distinct from elapsed time | Verified Working |Server stores self-report with nullable correctness; no timer mastery |
| Concurrent study requests preserve session state | Verified Working |Two controlled signed-in HTTP interleavings on restored synthetic PG: one open session for overlapping starts, completed session not resurrected by stale resume; broader load/races unverified |
| Focus follows preferences/policy | Verified Working |Voluntary user-selected reset and mapped focus denial; no passive monitoring |
| Device restrictions accurately described | Verified Working |Extension requirement and web/device/MDM limits documented |
| Controlled experimental maturity | Partially Working |Registry/admin beta/flags/internal alpha; approved reviewed-practice opt-in, notice, withdrawal and feedback verified locally; general promotion/legacy inventory incomplete |
| No false Stable quality claims | Partially Working |New course/video maturity honest; legacy Stable values are operational designations, not independently audited maturity |
| Admin-only course generator | Verified Working |Backend admin/flag/institution checks; ordinary/Premium denied |
| Curated authorized resources | Partially Working |Reviewed source links for Python and elementary algebra; no broad retrieval, authorization to ingest/rehost unconfirmed |
| Licensing/attribution respected | Verified Working |Link-only metadata/attribution, no copied source media; future commercial ingestion needs review |
| Original concept checks alongside curated links | Partially Working |Twelve fixed English Python/algebra activities, independently verified keys and mobile scoring/replay path; no generalized assessment, age suitability or teacher/pedagogical validation |
| Genuine structured original lessons | Implemented but Unverified |Required teaching/quiz schemas and fixture persistence; live provider unavailable |
| Original content quality evaluation | Partially Working |Schema validation, human key review and trusted Python/exact-arithmetic verification of twelve fixed curated keys; live generated correctness/age-quality benchmark missing |
| Persistent course/generation state | Verified Working |Saved blueprint/lessons/job, retry/cancel/expired lease tests |
| Individual original-lesson editing/regeneration | Verified Working |New course revisions preserve originals/evidence; validated edit, one missing-key worker regeneration, review lock, sequence/export, owner/admin/provider gates and browser flows; production/provider output unverified |
| Saved module organization editing | Verified Working |Local preserved title/unit/duration/order/prerequisite edits for both modes, unchanged objectives/content/evidence, graph validation and renewed review; pedagogical quality and live deployment unverified |
| Natural-language course request details | Implemented but Unverified |Bounded extraction schema in existing original-blueprint call; inspect/edit/persist/export and worker preference payload verified with fixtures; live extraction accuracy, effective level/depth/time adaptation and feasibility unverified |
| Adaptive recommendations | Partially Working |Interpretable missing/failed/assisted prerequisites and limited due review; fresh reviewed checks, hint accounting and existing adaptive practice path connected; preserved replacement course checks locally verified with fixtures; semantic novelty, error taxonomy and educational quality incomplete |
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
| Security/accessibility reviewed | Partially Working |Current scoped LMS/worker/privacy/curated-check independent source reviews and twenty-nine axe/responsive checks; mobile code keyboard scrolling fixed and exercised; broader earlier changes, manual-review cases, shared/legacy-page audit, real screen-reader testing and penetration testing pending |
| Accurate school documentation | Verified Working |Required reports plus continuation evidence and explicit limitations |
| Launch blockers identified | Verified Working |This ledger plus privacy/pilot report |

Blocked: approved live Gemini/Vertex setup, institutional authorization and provider audience/data terms, unavailable RTX environment if later justified training requires it. Do not flip contractual flags to bypass these blockers.

Not implemented: universal feature-promotion lifecycle, institutional SSO/roster onboarding, all-store retention scheduler and unstructured/unknown-schema audit anonymization, generalized resource discovery, curriculum-wide concept graph, scheduler evidence normalization, new hub offline synchronization, model training, actual video rendering, calendar-ingested assessment schedules, currency spending reconciliation and packaged-client/MDM changes. Replacement course-check generation is implemented with local fixture evidence; live correctness/semantic freshness and independent re-review remain incomplete.

Priority next engineering work: close unmapped/background policy coverage; complete privacy/retention/SSO/provider prerequisites; validate restored PostgreSQL schema/deletion/concurrency and deploy a supervised worker; run real vendor/account/device workflows; assess content quality and accessibility; then broaden the catalog/adaptation. Only after those gates consider a student pilot or Premium course rollout.

Actual Today, next-action and schedule route families now enforce institution scheduler denial before views/cache/service access, with ten HTTP regressions and 244 focused passing checks. Background briefing owner binding remains pending. See [planner policy](26-planner-policy.md).

Subsequent work implements owner binding and current consent/institution/source/provider checks for background narration and cache use, with 105 focused passing checks. Deterministic health calculations, other background paths and live provider behavior still need review. See [briefing policy](29-background-briefing-policy.md). Live Supabase health/read/write checks and matching website reads passed without user-data changes; these do not establish all application write paths. See [database checks](28-live-database-verification.md).

The next planner continuation stamps the whole Today response cache by current policy/consent, discards in-flight changes, skips scheduled work for scheduler-restricted owners and gates mixed grades/stored task imports. Local Quick Add and tracked-course tasks remain available. Final focused suite: 218 passed; independent Python review completed after a local-task preservation correction, while the separate spec reviewer hit its usage limit. Historical health snapshots and other repository consumers remain pending. See [planner cache and sources](30-planner-cache-and-sources.md).

Historical health reads/writes now check fresh owner/source permissions; revoked writes roll back without changing existing scores or leaving new rows pending. Final focused suite: 192 passed, with independent Python review. Other consumers and true PostgreSQL interleavings remain pending for this specific history scope. See [health history](33-health-history-permissions.md). Major checkpoints, including the adapted Learning Hub/Command Center UI, are committed and pushed to PR 86; hosted tests/security pass, while production release remains separate.

The newsletter investigation found zero production subscribers with weekly consent evidence. Six older dated opt-ins lack the weekly version, including three with unknown age. The existing scheduled endpoint returned zero recipients on October 5; no actual newsletter delivery is proven. Local audience diagnostics now explain exclusions without replacing subscriber consent. Email checks: 121 passed; production variables and cron deployment were inspected, but diagnostic changes are not deployed. See [newsletter investigation](27-newsletter-delivery.md).

Continuous course-worker operation is now implemented locally, with explicit watch/poll options, graceful shutdown requests, per-step session cleanup, safe events and an opt-in supervisor configuration. Real curated checkpoint/resume fixtures and CLI lifecycle checks pass; no supervisor or production service is running. Container build, process-signal delivery, forced termination and PostgreSQL recovery/concurrency still require operational proof. See [worker operation](21-worker-operation.md).

The subsequent privacy continuation removes known nested audit identity references with conflict-safe updates, rechecks retention under owner/scope/content locks and serializes account deletion before late target references can be inserted. Fresh and restored disposable PostgreSQL tests now demonstrate narrow deletion/retention, lease-claim and shared-budget concurrency behavior; synthetic backup/restore fingerprints match. Production backups/restored-data comparison, deployed supervision, other workflow races, large-tenant load and all-store/unstructured privacy propagation remain incomplete. See [PostgreSQL proof](22-retention-postgres.md).

The Plani continuation repairs scoped internal-tool permissions, consent for automatic context/account reads/note retrieval, actual profile ownership, nested calendar placement/export, per-call allowances, truthful provider failures and in-flight prompt/source revocation. The shared LMS collector and grade-personalization helper now check source permissions before cache/credentials, and dense retrieval checks owner consent/provider policy. These paths have disposable regression evidence and completed independent source/helper review for the scoped Plani diff; separate standards/spec attempts remained incomplete. Source provenance remains insufficient for selective aggregate disclosure, unmapped legacy providers/background paths still require review, and real deployed behavior is unverified. See [Plani boundaries](19-plani-data-boundaries.md). Independent re-review of all broader prior module/AI-route continuations remains incomplete.

The subsequent legacy LMS continuation adds seven named source controls, connection/callback and background credential/cache gates, HAC missing-work provenance, Notion manual-task export checks, generic sync pre/post guards, post-fetch publication filtering and permission checks before refreshed credentials are saved. Focused stored-fixture regressions and independent Python, JavaScript and spec reviews complete for this scope. Status/disconnect remain usable under denial. Live vendors, unsupported independent grade preferences, production races and unmapped/offline paths remain unverified. See [LMS boundaries](20-lms-policy-boundaries.md).

Additional course requirements remain incomplete: live request-extraction accuracy and effective subject/depth/time adaptation, formatting beyond the five saved teaching sections, diagrams/media, independently executed generated mathematical/code assessments, pedagogical validation of edited module sequencing, curriculum-wide quality scoring and broken-link retrieval fallbacks. Twelve fixed curated keys now have trusted repository Python/exact-arithmetic proof; this does not execute learner submissions or establish provider output correctness. See [curated checks](23-curated-checks.md). The request-preference schema, inspection/editing, persistence and lesson context are implemented locally; see [request details](15-course-request-details.md). Sanitized Markdown/math/code/tables in the saved teaching sections now pass adversarial Chromium checks; see [content rendering](13-content-rendering.md). Original lesson edits/regeneration, replacement checks and course organization now preserve separate course revisions; see [lesson revisions](14-lesson-revisions.md), [replacement checks](16-replacement-checks.md) and [module editing](17-module-organization.md). The link catalogs and human review are bounded working features, not substitutes for those acceptance criteria. The full master brief is not complete or deployment approved.
