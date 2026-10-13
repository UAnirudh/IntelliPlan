# Legacy LMS connection and data boundaries

This continuation extends named institution controls to Schoology, HAC, Google Classroom, Blackboard, Moodle, Notion and PowerSchool. It preserves the existing connectors and central parent/school policy intersection. A connector's operational registry status does not establish institutional authorization or reliable live behavior.

## Implemented behavior

- The institution form exposes each source as a separate boolean capability. A child school cannot override its district's denial. Existing policies without these keys keep their prior defaults.
- Connection routes and callbacks, selected active-account data routes and generic sync enforce the named source before credentials or vendor work. Status metadata and authenticated disconnect remain available after denial. Anonymous disconnect is still rejected.
- The background assignment collector checks sources before cache and credential access. Independent Classroom, Blackboard and Moodle fetches enforce their own source. Source attribution is assigned by the server, and revoked rows are removed before Grade Pulse, return or cache publication. HAC missing-work rows use the same HAC capability as regular assignments.
- The legacy unified-task endpoint checks independent sources and assigns their provenance before its final policy filter. Tests revoke access inside vendor stubs returning a false manual-source label; restricted rows never reach the downstream observer or response, while permitted local tasks remain.
- Notion token access and refresh are governed inside their shared helpers, including optional manual-task export outside the Notion routes. Denial preserves local task creation and updates without contacting Notion.
- Classroom, Blackboard and Notion refresh helpers recheck permission after the vendor returns and before changing stored credentials. Generic sync retains refreshed credentials locally until sync completes and the final policy check succeeds. Denial skips credential/count/timestamp persistence; allowed refresh/sync still saves normally.
- Plani's connection tools recognize the added capabilities. Its mixed-source grades, saved-schedule and Command Center aggregates remain conservatively omitted when any source is restricted because their provenance is insufficient for selective disclosure.

Checks at operation boundaries cannot recall an already-started external request. Production transaction races, vendor token rotation and all unmapped/offline paths require separate validation. Unsupported independent grade-source preferences still resolve through the existing active-account implementation; this patch does not add new grade integrations.

## Verification

The focused disposable-database run passes **434 tests,2505 warnings in35.54s**. Coverage includes stored fixture connections, inherited denial, denied cache/credential reads, allowed and revoked refresh persistence, generic sync before/after vendor work, source spoofing, local-only Notion task mutations and continued authenticated disconnect. External operations are declared stubs; no actual student credentials or vendor requests are used. [Focused results](evidence/pytest-lms-policy-targeted.txt).

Independent read-only Python/source, JavaScript and spec reviews completed. The source reviewer found and verified repairs for the HAC alias, Notion export helper, generic sync, unified post-fetch publication and legacy refresh-persistence gaps. The spec review reports no actionable defect in this scoped diff. These reviews do not certify all earlier changes or the full master brief. See [remaining work](11-remaining-work.md).

The completed full regression passes **4183 tests,24 skipped,9949 warnings in291.70s**, with process exit0. A preceding run was interrupted and is not counted as passing. The fresh Chromium report records16 HTTP/storage workflows,27 responsive checks,27 scoped axe checks and no JavaScript errors; external course generation remains fixtures and raw manual accessibility cases remain recorded. Changed-module Ruff, Python compilation, JavaScript parsing and whitespace checks pass. The literal route inventory remains644 and the raw-byte source manifest covers106 paths. [Full results](evidence/pytest-lms-policy-full.txt), [browser report](evidence/browser-results.json).
