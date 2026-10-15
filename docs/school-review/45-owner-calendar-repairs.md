# Background repairs read the plan owner's calendar

Follow-through service construction now explicitly passes the saved plan owner
into the calendar busy-time loader. Anonymous scheduled sweeps can therefore
read each owner's connected Google and Outlook calendars rather than returning
empty data because there is no signed-in browser user.

Explicit-owner token reads select only that owner's integration rows. Google
does not fall back to the caller's session token, including when the owner's
integration is missing or malformed. The ordinary browser path retains its
existing session behavior. Calendar policy resolves a fresh owner record and
is checked before credential reads and memoized results. Outlook rechecks it
after a remote refresh before writing the refreshed token. Revoked calendar
results are discarded while the separate class timetable remains available.

Memoization now separates owner, local start date, horizon and resolved
timezone/offset. One request sweeping several students cannot reuse another
student's free/busy data. Owner-local horizon and IANA timezone rules apply to
both providers, and an unrelated caller cannot override them with its header.
Provider lookup errors report only the exception type, without response text.

Local evidence:201 owner/calendar/clock/policy/follow-through regressions pass,
including six new disposable tests for two owners' actual integration rows,
separate local days/zones/cache, caller-session isolation, central owner policy,
revocation during refresh and service construction passing the correct owner.
The initial new fixture used a scope the existing Google helper does not
recognize; it now uses the calendar.events scope actually requested by the app.
No provider request was made. Scoped Ruff and independent Python/spec reviews
pass. Full-suite and release results follow separately.

This closes the explicit owner-binding gap for this follow-through service.
Real Google/Outlook accounts, deployed sweep behavior, provider outage handling,
exact time-of-day deadlines and broader scheduling quality benchmarks remain
unverified or incomplete. Existing provider failures still yield empty busy
data; this checkpoint does not guarantee conflict-free plans during outages.

Separately, the question-led tutor PR94 is merged and public health verifies
revision45190438ea018cf0146ef98fe69dbcc226129209 with successful schema startup.
The Railway plugin was unavailable in this continuation and its local CLI
connection was unauthenticated, so this release has public exact-revision proof
rather than a fresh Railway plugin status/log inspection. Post-merge hosted
checks are tracked separately.
