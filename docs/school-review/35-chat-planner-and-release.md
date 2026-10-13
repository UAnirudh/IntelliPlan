# Production release and chat planning

## Released checkpoint

PR 86 merged as `6edfe95152849e5ef65537091b49a4ab388b98ab` after
both hosted test jobs and security checks passed. The release unit job
reported **4422 passed, 16 skipped**; its separate live-site smoke job
reported **130 passed, 1 skipped**. The latter ran before release and
therefore did not verify this new deployment.

Railway plugin monitoring confirmed production deployment
`45fbfcdf-d18a-4c2e-9f89-d6df99a614d3` reached **SUCCESS** for that exact
merge commit. Runtime logs showed all four web workers booting. Subsequent
read-only HTTP checks returned 200 from `/health`, `/api/live`, and
`/api/stats`; health reported completed migrations and a valid users schema.
The Learning Hub and Command Center CSS bytes matched the released files
after newline normalization. A later filtered runtime log query returned
no entries matching error, failed, or Traceback. Main's post-merge hosted
test job also passed. This is scoped release evidence, not proof of every
signed-in workflow or vendor integration.

## Next checkpoint: executable plans from chat

The chat `generate_schedule` tool previously requested an AI-generated JSON
timetable, although the Scheduler page already used the deterministic
constraint-aware planner. Chat now uses that same planning entrypoint:
task estimates, availability, commitments, deadline allocation, and existing
follow-through context reach the planner instead of a second timetable
generation prompt. No extra provider call creates the schedule.

The agent still needs its permitted provider to interpret conversation;
the offline-provider test directly invokes the scheduling tool. It does not
establish an offline natural-language chatbot.

Fractional daily hours and one-sided clock windows are validated. Manual
and LMS IDs use separate namespaces; title-only LMS records include course,
title and deadline in their fallback identity. Restricted stored imports
are filtered using the existing planner source policy. Policy changes
before saving discard the new plan. Planning does not write a plan-version
audit before that policy check. If no work fits, the existing active plan
is preserved. Saving and calendar export retain their existing boundaries.

Focused planner/clock/policy/estimate tests: **264 passed**. These include
real planner output and saved schedule equality, unavailable provider,
fractional and start-only/end-only windows, colliding manual/LMS IDs,
repeated LMS titles, invalid hours, in-flight policy revocation, calendar
denial, and preservation of existing plans on failure. Independent Python
review findings were fixed and re-reviewed. Remaining pre-existing lint
findings in the legacy agent file were not expanded into an unrelated cleanup.

## Calmer chat surface

Each empty-state branch offers two starters instead of four. The composer
asks for deadlines or a plan; optional reminders sit in a collapsed native
disclosure, and their label updates after activation. Starter rows no longer
inherit card shadows. Daily-visit XP toasts are hidden only on the chat
surface; rewards still accrue and remain available elsewhere.

Browser fixtures verify 320, 768 and 1440 widths in light/dark themes,
disclosure open/close behavior, and scoped axe checks. Reminder activation
is mocked to verify the UI label without writing preferences or requesting
notification permission. Screenshots are synthetic local previews.

## Still ongoing

This connects chat schedule creation to the real planner; conversational
repair of an existing plan still needs its own integration and validation.
The original full engineering brief, whole-product UI simplification,
country/language support and remaining operational gates are incomplete.
See the acceptance ledger in `11-remaining-work.md`.
