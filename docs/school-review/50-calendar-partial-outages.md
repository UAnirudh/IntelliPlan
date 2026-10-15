# Preserve known commitments during partial calendar outages

The planner previously wrapped both connected calendar reads in one exception
handler. A Google failure prevented the Outlook lookup; an Outlook failure
discarded Google busy intervals already fetched. The result then looked like
both calendars were empty, increasing the chance of scheduling over a known
commitment even though its provider had worked.

Google and Outlook token lookup/fetch failures are now isolated. A successful
provider's busy ranges and the class timetable survive the other provider's
failure. Explicit owner tokens, local clock/timezone, request-scoped caching
and fresh permission checks remain in place. Revocation during a failing
provider call still discards previously loaded connected-calendar data.

Failure logs contain only a fixed provider label, exception class and a bounded
integer HTTP status (or unknown). Exception strings, response bodies, tokens,
calendar titles and owner identifiers are not logged by this handler. This
adds diagnostic value to the earlier opaque HttpError observations without
exposing their contents.

Evidence: four new disposable cases cover token and fetch failure for each
provider, preserving the other provider and classes. A fifth covers revocation
during the failing request.63 owner/calendar/follow-through tests pass; scoped
Ruff and independent Python/security and spec reviews pass. No production
calendar request or real student account was created for this test.

Limits: an unavailable provider's busy intervals remain unknown. This change
does not stop all planning during outages, display a user-facing degraded-data
warning, prove real Google/Microsoft integrations or guarantee no conflicts.
Those remain acceptance criteria, along with broader scheduler quality.

Delivery: local follow-up on branch codex/calendar-partial-outage, pending
push, hosted checks and release. Network-camera PR100 is a separate checkpoint.
