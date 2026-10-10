# Feature audit matrix

Scope: source inspection of affected contracts, 637 source route decorators, existing regression tests, and new local API/browser workflows. “Existing tests” means the repository's tests, usually using mocked upstream APIs. It does not mean authenticated live vendor validation.

| Surface | Before this checkpoint | Change / condition after | Evidence and limit |
|---|---|---|---|
| Learning entry points | Separate tutor, study, review and active pages | One hub with shared activities and preserved deep links | Chromium and discoverability tests; legacy views still exist |
| District/school controls | No central organization hierarchy governing these surfaces | Scoped admin policy, restrictive inheritance, audit and provider intersection | Direct API, role, token/cookie and optimistic-version tests; unmapped/background routes need further review |
| AI tutor | Existing guided tutor, reviewed checks and personalization controls | Hub mode instructions, optional new evidence context, deterministic strict-assessment support | Existing tutor suite + adversarial gate tests; unrestricted model teaching quality not benchmarked |
| Assessment support | Prompt instructions alone could not enforce institution restrictions | Protected derivative/fraction explanations; other assessment generations denied | Concept browser test and alternate-route denial; limited reviewed coverage |
| Study/focus | Active Study, extension Focus Shield, separate note workflows | Objectives, methods, voluntary reset, pause/reload/resume and reported checkpoints | Browser and API tests; no passive attention inference |
| Learning progress | Existing tutor, Primer and flashcard stores | New lightweight evidence links to existing stores | Scoring/replay/self-report tests; not a universal mastery migration |
| Curated courses | No integrated admin-only course path | Six Python foundations lessons from reviewed PSF/Harvard source links | Browser and worker tests; static catalog, no general web search |
| Original courses | No integrated durable course path | Editable outline, structured original lesson fields, review/scoring and durable jobs | Fixture browser/worker tests; real provider blocked locally |
| Course recovery | No durable course job contract | Conditional leases, token reservation, cancel, retry and revocation checks | Fault-injection tests; production multi-worker load unverified |
| Educational video | Legacy media surfaces, no new course-rendering adapter | Source-page video links and honest unavailable generated-video capability | Primary source checks; no rendered video or transcript ingestion |
| Lesson recording | No-transcript summary could be generated from placeholder seed | Missing transcript fails explicitly; owner/provider policy bound in background | Source correction and full suite; real upload-to-transcription path unverified |
| Canvas | OAuth, refresh, gradebook and task implementation | Preserved; capability-gated mapped endpoints | Existing OAuth/scope/refresh/gradebook tests; no live Canvas account |
| StudentVUE | Existing connector with institution/provider limitations | Preserved; capability-gated mapped endpoints | Existing login/JSON tests; district authorization and live sync unverified |
| Google/Outlook calendars | Existing OAuth/calendar helpers and feeds | Preserved; mapped calendar policy gates | Existing calendar busy-time/feed/cloud-doc tests; no live OAuth calendar sync |
| Scheduler | Existing feasibility/audit engine, parsing and manual plans | Preserved; hub links scheduled work and mapped routes are gated | Existing scheduler/depth/clarify/audit tests; no new whole-school scheduling benchmark |
| Assignments/grades | Existing manual/LMS/import contracts | Preserved, not migrated | Regression suite; all live import combinations not demonstrated |
| Offline/PWA | Existing service worker and queue architecture | Preserved; hub uses authenticated network APIs | Source/regression checks only; new hub offline mutation/reconciliation not implemented |
| Notifications/email | Existing consent, quiet-hour and delivery paths | Mapped request policy gates; existing provider behavior preserved | Existing tests only; background delivery's organization governance remains incomplete |
| Extension/desktop/mobile | Existing browser extension, desktop and mobile clients | Server restrictions exercised for selected direct paths; responsive hub | Chromium mobile-width check; actual packaged client/MDM/device testing incomplete |
| Privacy/export/deletion | Existing consent and deletion infrastructure | New user-owned export and deletion-plan coverage | Regression and new export tests; retention automation and restored PostgreSQL test pending |
| Accessibility/performance | Existing shared UI infrastructure | Labeled controls, plain-text output, focus/status semantics | Basic keyboard/mobile smoke and measurements; screen-reader, contrast and load audit incomplete |

Other existing features (billing, public marketing, classroom collaboration, parent communications, advanced workflows) are not individually certified by this matrix. Their presence in the regression suite is not equivalent to a live product audit.
