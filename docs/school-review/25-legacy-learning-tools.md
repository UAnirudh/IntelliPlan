# Legacy learning-tool policy boundaries — 2026-10-12

This continuation prioritizes concrete restriction/privacy gaps before further cosmetic UI or localization. It covers Text Dissector and assignment breakdown; it does not establish universal feature-policy coverage.

## Text Dissector

The page and analysis API now participate in the tutor route mapping. Anonymous analysis requests require identity; institutional tutor denial and assessment protection deny analysis before a provider call. The handler repeats those checks at publication, including failed-call fallback. With processing/provider permission initially absent, an allowed reading tool returns its deterministic text preview without invoking AI. If the permitted-provider set changes during generation, generated output is discarded, including when another provider remains allowed.

Input must be a JSON object containing a bounded string. Output accepts only bounded summary text, lists of textual key points/questions and vocabulary objects containing textual term/meaning fields. Malformed model shapes use deterministic fallback instead of breaking the renderer; unknown fields and model-supplied fallback markers are dropped. Provider exceptions are logged by class, without their message. The legacy page accurately labels fallback as a copied text excerpt and general questions, rather than presenting it as an AI summary.

## Assignment breakdown

AI assistance checks current owner opt-in, tutor/assessment policy, processing permission and available permitted providers. Actual User state is refreshed rather than trusting an earlier request object. Direct helper calls repeat the guard; post-call checks reject output after consent or provider-set changes. These checks preserve deterministic local steps when AI is unavailable or denied. There is no new institutional personalization toggle; the existing user opt-in and institution processing/tutor controls are used.

Canvas permission is checked before active-account credentials are read and after assignment material fetch. Internal provenance tags trigger another check around AI processing and before fallback/storage. Revoked Canvas material cannot survive via deterministic fallback: the original permitted request/local description is restored, estimates are recomputed and fetched context/generated results are discarded. This does not undo a provider transmission that already occurred before revocation.

Scheduling and plan-progress synchronization both check the explicit owner UID before plan access, including helper use without a logged-in request actor. Denial leaves local checklist creation and done/undone operations usable. Five-minute breakdown starts now participate in the study gate before creating an Active session. Existing guest deterministic planning and normal authorized flows are retained.

## Verification boundaries

Provider availability is forced true and calls trapped in denial tests, so passing checks cannot merely depend on absent provider configuration. Scoped regressions exercise typed renderer failures, actual signed-in HTTP denials, allowed availability, owner scheduling, step done/undone, in-flight source/provider changes and permitted local fallback. Stale-consent tests use Core SQL with commit expiration disabled, assert the request User still holds the old opt-in, and then verify refreshed denial.

Independent Python, scoped spec and JavaScript/verifier reviews identified and drove corrections for secondary plan synchronization, Canvas-derived fallback publication, malformed renderer fields, partial provider revocation and insufficient stale-consent proof. Final reviewed changes preserve those boundaries. External model output is a declared fixture; live provider/integration behavior, broad academic-integrity evaluation, every legacy/background path and production rollout remain incomplete.

The final full disposable SQLite regression passes **4277 tests,33 skipped,10613 warnings in2184.47s**, exit0. This includes the strengthened stale-request consent assertions. The preceding focused learning/breakdown/tool run passes228 tests before those assertions were strengthened. Changed Python Ruff, inline JavaScript parsing and whitespace checks pass. [Full result](evidence/pytest-legacy-tools-full.txt).

The final dedicated legacy-tool scope passes **32 tests,198 warnings in15.03s**, exit0, including those strengthened consent checks. [Focused result](evidence/pytest-legacy-tools-final-targeted.txt).

Two browser attempts exited with a native access violation while the periodic diagnostic was dumping threads, including one before any workflow. They are not counted as passing evidence. A controlled run with only the periodic diagnostic disabled passes18 workflows/29 Learning Hub responsive and scoped axe checks with no JavaScript errors. Workflow assertions, server/storage paths and provider traps remain unchanged. Python documents that [`dump_traceback_later` uses a watchdog thread](https://docs.python.org/3.13/library/faulthandler.html#dumping-the-traceback-after-a-timeout). This observation suggests a runtime/diagnostic interaction; it does not diagnose the exact native cause. The harness now makes that diagnostic opt-in through `INTELLIPLAN_BROWSER_TRACEBACKS=1`, records its state and retains normal assertions/timeouts. Native-diagnostic stability on this Windows Python3.13.2 runtime remains unresolved.

The final normal entry point also exits0 with **18 HTTP/storage workflows,29 Learning Hub responsive checks,29 scoped axe audits and no JavaScript errors**, with diagnostic state recorded as false. Its added legacy-page workflow verifies truthful fallback/excerpt display, no trapped provider invocation and a real403 after tutor revocation replaces the old result. The29 accessibility/layout checks remain scoped to learning/admin surfaces; no new legacy-page accessibility certification is claimed. [Browser report](evidence/browser-results.json). The opt-in diagnostic adjustment follows the full application regression and has separate syntax/toggle/source review; application behavior is unchanged.
