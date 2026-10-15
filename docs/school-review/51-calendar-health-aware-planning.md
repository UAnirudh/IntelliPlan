# Calendar-aware planning during outages

Connected-calendar failures now pause computed plan changes instead of treating
unknown time as empty availability. Strict reads are required by Scheduler
generation, Plani generation/adjustment, saved recovery, manual adjustment and
preview, autopilot, forecasts, quick-add/breakdown placement and saved reflow.
The existing saved plan and progress remain intact on a recognized outage.
Quick-add still retains the new task and explains that it could not be placed.
Reflow uses the verified busy map directly, including a verified empty map,
rather than doing a second best-effort lookup before saving.

The previous best-effort helper remains available for reading known busy times.
Strict and best-effort request caches are separate. Owner identity, local clock
and fresh institutional/account permission checks remain in place. Denied
calendar access intentionally uses class constraints rather than pretending
there is a provider outage. Extension placement now loads the explicit owner's
calendar instead of dropping it when no browser login is present. Unreadable
stored Google credentials are retained for reconnecting, not deleted by the
strict planner path. Missing/unusable stored calendar tokens or scopes cannot
certify free time.

Transport success is insufficient: Google FreeBusy per-calendar errors,
missing/malformed busy data and invalid intervals fail strict verification.
Outlook missing/malformed event collections, invalid busy intervals, unexpected
paging links and a calendar view exceeding the five-page import bound also
fail. A valid empty calendar remains valid. Provider bodies, tokens and event
contents are not exposed in the retry response. Existing bounded provider/type/
HTTP-status diagnostics remain separate from that safe response.

Google's FreeBusy endpoint requires an availability scope; the prior OAuth
request included only calendar.events. New connections additionally request
calendar.events.freebusy. Existing connections may need explicit reconnection
to grant it. No stored token is silently given a new permission and no real
calendar account was reauthorized during verification. The required scopes
and response errors are specified in [Google FreeBusy documentation](https://developers.google.com/workspace/calendar/api/v3/reference/freebusy/query).
Outlook's collection and pagination contract is described in [Microsoft calendarView](https://learn.microsoft.com/en-us/graph/api/user-list-calendarview?view=graph-rest-1.0).

Generation/adjustment/forecast APIs return a safe retryable503 with the
calendar_unavailable code. Chat tools carry the same explanation. Background
sweeps count calendar skips separately from failed operations and do not save
or notify a successful adjustment for a skipped owner. Disabled autopilot
returns before provider work. The existing scheduler bar displays an accessible
warning without adding another action panel. An outage removes stale risk
predictions. Request generation, plan identity and an outage epoch keep older
success/404 replies from erasing a newer warning or disabling the current UI.
A fresh successful forecast clears the warning. Automatic retries follow the
existing page-load/background cadence; this is not a new retry service.

Evidence so far:288 focused provider/owner/clock/policy/adjustment/insertion/
reflow cases passed before the final missing-scope/chat-adjustment additions.
The real Chromium component verifier passes at1280 and390px with controlled
API replies: warning, recovery, old success after new outage, old404 after new
success, autopilot clearing a displayed forecast, unchanged plan data, inert
hostile error markup and no horizontal overflow. It runs in CI and saves
screenshots. This is UI/component evidence; actual persistence is exercised
separately through disposable app tests. Scoped Ruff and source checks pass.
Independent Python/security, JavaScript and spec reviews found and corrected
provider payload errors, bypassing writers and stale-response gaps. The final focused rerun passed231 cases after the missing-scope and chat-adjustment fixes. The complete local suite and hosted release checks are still running or required.

Limits: no real Google/Outlook authentication, provider outage or reconnect
was exercised. Legacy explicit raw plan saves/user-authored edits are not
certified scheduling operations. Strict reads do not establish whole-system
transactional isolation from simultaneous plan edits or permission changes.
The scheduler's broader quality, dependency/repair benchmarks, physical study
signals, live tutor/Fish behavior, paid launch and original ledger remain open.

Release baseline: PR99 phone pairing, PR100 network cameras and PR101 partial
calendar preservation are merged. Final revision1c8cdc789415c29ed156e5fe731048edf4ed98be
is live, exact-revision/schema verified, with Railway web and newsletter
deployments successful. Post-release Tests38778805459 passed. PR100's earlier
post-release run failed its exact-revision wait after PR101 superseded it;
the final release's passing run is the current evidence. This report's new
calendar-health checkpoint is initially local pending push/checks/release.
