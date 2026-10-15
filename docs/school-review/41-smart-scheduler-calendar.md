# Smart scheduling: local time and fragmented calendars

This checkpoint improves the existing assignment optimizer. It does not close
the full scheduler requirement or the original engineering brief.

The planner, candidate selection, clock placement and final verification now
share one captured student-local instant per request. The authenticated
student's valid browser IANA zone takes precedence over their stored zone;
older fixed-offset accounts remain supported. Background repairs explicitly
resolve each plan owner's clock. An anonymous cron caller cannot supply that
student's zone through a request header.

Google and Outlook busy imports use the same local horizon and IANA rules,
including changes in daylight saving time. UTC event ordering is preserved
through repeated hours; the ambiguous local interval is conservatively busy.
Outlook all-day boundaries are converted from the requested UTC response,
rather than treating UTC dates as local dates. End minutes round outward so
an event with seconds cannot leave a false free minute. Source semantics:
[Graph calendar view](https://learn.microsoft.com/en-us/graph/api/user-list-calendarview?view=graph-rest-1.0)
and [Python IANA zones](https://docs.python.org/3/library/zoneinfo.html).

New flexible sittings are resized only when real clock placement improves.
The sizing considers individual remaining gaps, not just total daily capacity
or the largest gap. Equal 20+20+20 and unequal 20+40/25+50-minute fixtures hold
all 60 required minutes. Existing and pinned sittings keep their sizes; their
unplaceable work remains explicitly deferred. Unchanged part numbering and
interleaved sitting order remain intact. Placement failures also defer work
instead of returning untimed blocks as though scheduled.

Candidate checks and the final enriched schedule use actual availability and
busy intervals. Empty availability or a failed availability check cannot
certify placed work. When v2 cannot produce a verified plan, the generation
route returns an explicit503 rather than silently escaping through unchecked
legacy generation. The deliberately disabled-v2 legacy path remains separate.
Catch-up for work already overdue is a named soft warning retaining the
original deadline; missing a future deadline remains a hard failure.

Local verification includes 124 focused tests and 199 clock/policy regressions,
with a further 79-test scheduling/constraints run covering twelve seeded
scarce-calendar cases and minute conservation. These sets overlap. The first
full local run had 4546 passing tests, 33 skipped and two URL assertion failures
caused by this workspace's old localhost APP_BASE_URL. Both URL tests pass with
the canonical site URL; a clean full rerun and hosted checks follow separately.
Independent standards/correctness and spec reviews identified calendar-fold,
fallback, availability, unequal-gap and background-owner issues; all were
corrected and re-reviewed with no blocking findings. Scoped Ruff passes.

Remaining scheduler work includes exact time-of-day deadlines, broader
dependency/repair quality benchmarks, explicit owner-bound busy-calendar
loading during background sweeps and real authenticated calendar accounts.
Fixtures demonstrate bounded behavior, not outcome gains for actual students.

The next user-priority slices remain open:

| Area | Acceptance still required |
| --- | --- |
| Study signals and cameras | Explicit camera/device selection, disconnect/cancel safety, phone companion and network-camera connection paths, local processing and truthful signal labels; real hardware proof separately |
| Question-led tutor | Consistent Socratic behavior across ordinary/premium/vision paths, safe interactive visual questions and audio with verified Fish Audio behavior |
| Paid launch | Read-only provider readiness checks, checkout/webhook/entitlement/cancellation paths; live charges remain gated on account readiness and verification |

Stripe verification, physical camera/network availability and live provider
credentials are external prerequisites, not evidence that delivery succeeded.

Released as PR92, main commit75c2784c61da45a4e9240c278986fc768f3abe6c.
The Railway plugin confirms deployment69f81bde-3d54-4184-967b-8b09a30d3c51
at SUCCESS; public health serves that exact revision with successful schema
flags, and a filtered runtime error scan is empty. Post-merge hosted checks
pass4562 unit tests (16 skipped), then the exact-revision wait, then130 public
browser tests (1 skipped). Security checks pass. The separate, pre-existing
Cloudflare Workers build remains failed and is not the Railway release target.
