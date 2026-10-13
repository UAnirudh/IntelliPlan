# Conversational plan repair

Chat now has an `adjust_schedule` tool for unavailable days, limited daily
minutes, extra time, and catching up. It invokes the existing follow-through
repair engine against the owner's saved plan instead of inventing a new
timetable or changing assignment deadlines. The agent instructions no
longer interpret a request to reschedule study work as a deadline edit.

Preview mode returns the resulting plan and consequences without saving.
Saved repairs conditionally update the same owned active row, matching its
original schedule and progress. Another plan update or deactivation rejects
the stale write. Assignment rows and previously exported calendar events
are not changed; the response states that boundary explicitly.

Current scheduler, source and personalization permissions are required
before reading the saved plan and checked after computation. Explicit dates,
whole-minute limits, boolean preview and accepted arguments are validated;
an invalid date cannot silently target today. The feature kill switch is
respected. No extra AI call performs the repair.

Past completed and missed sittings are harvested into the existing durable
outcome log in the same transaction as saving. Chunking bounds each payload
without dropping outgoing rows. Preview, consent revocation and stale-write
rejection do not publish new outcomes. This preserves the history used by
the existing follow-through model. It does not introduce passive measurement.

The shared enrichment path now resolves assignment metadata by task ID
before its legacy title fallback. Repair finalization retains task IDs in
the metadata it supplies, so repeated titles with distinct deadlines do not
acquire the same deadline.

Focused checks cover real repair and persistence, read-only preview, skipped
days, reduced time, unchanged task deadlines, invalid inputs, restricted
sources, in-flight consent withdrawal, stale saved-plan writes, and atomic
history preservation. Separate regressions check identity-based enrichment
and repair finalization with repeated titles.

Final scoped suite: **453 passed** across chat/policy/clock, scheduling,
follow-through, planner and manual-schedule tests. Independent Python review
findings were fixed and re-reviewed. Two real chat HTTP tests exercise saved
repair versus preview using mocked model responses and real disposable
storage. Browser verification passes six responsive/theme axe checks plus a
composer/preview/saved rendering fixture, with zero JavaScript errors.
Preview cards explicitly say they are unsaved and omit the Scheduler link;
saved cards retain it. Model responses and reminder activation in browser
fixtures are mocked, not live provider or preference-delivery evidence.

This is part of PR 87. The broader original engineering brief, full UI
simplification, country/language support, independent PostgreSQL repair
interleavings and live authenticated vendor/calendar behavior remain open.
The conditional-write fixtures use disposable SQLite; they establish the
checked application behavior, not PostgreSQL contention performance.
