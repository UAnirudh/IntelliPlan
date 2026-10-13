# Adaptive recommendation source policy

Stored imported assignments now use the main planner's source filtering before
they enter adaptive recommendations. Local, Quick Add and tracked-course tasks
remain available when an imported source is denied. Unknown import provenance
is withheld. Mixed-source saved plans, mastery/session/feedback history,
availability personalization and schedule audit records require the current
scheduler and all mapped source permissions because these aggregates lack
complete per-item provenance.

Each adaptive HTTP operation pins the owner's effective policy and personalization
consent. Helpers recheck that stamp before protected reads, writes and AI
narration. A changed stamp replaces the complete HTTP response with an error;
a failed policy lookup denies access. These checks detect changes at operation
boundaries, rather than promising atomic rollback of every concurrent effect.

Narration binds the actual owner to the provider policy context, checks consent
before dispatch and after the result, and restores the prior context on every
exit. It permits one application/provider attempt, with deterministic explanation
fallback. Gemini configuration retry is disabled for this mode. Automatic SDK
retries are disabled in the shared Gemini, Groq and Anthropic clients; the
central provider chain retains its ordinary transient retry and fallback
behavior outside single-attempt mode. The Google SDK requirement is raised to
the locally verified 2.8.0 baseline for explicit retry options. Its documented
[`attempts` setting](https://googleapis.github.io/python-genai/genai.html)
includes the initial attempt. Optional Gemini mTLS credential-refresh transport
is not covered by this proof; this is not a universal exactly-once transport
guarantee.

Adaptive overrides conditionally update the exact owned active plan originally
read, matching both its schedule and completion data. Concurrent replacement,
deactivation or completion changes reject the stale write. Failed saves return
409 and do not record a successful version or override.

Regression coverage includes all ten mapped source keys, unknown imports,
restricted aggregate query traps, fetch-time revocation, actual HTTP response
withholding, owner binding, pre/post-dispatch consent changes, provider failure
followed by revocation, SDK/configuration retry bounds, indirect availability
reads and stale-plan writes. Final focused suite: **419 passed**. Scoped Ruff
and whitespace checks pass; independent Python and spec reviews completed.
The tests use disposable storage and mocked providers. They do not establish
live provider delivery, PostgreSQL contention performance or atomic consent
transactions across all application paths.

The original master brief remains incomplete, including universal policy
coverage, institutional identity/rosters, general resource discovery,
curriculum-wide quality and live content validation, all-store retention,
supervised course-worker operation, device/MDM behavior, currency reconciliation,
country/language support and the complete UI simplification.
