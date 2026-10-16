# Calendar transport timeouts and paging boundaries

Google calendar services now use a separate authenticated transport with an
explicit 15-second socket timeout, including automatic credential refresh, and
at most one refresh/retry after a 401. The SDK previously supplied a 60-second
default. Bundled Calendar discovery avoids a discovery download during service
construction. Connections and credentials are not shared between services.

Outlook calendar reads retain their 15-second socket timeout and now validate
HTTPS, exact Graph host/port, absence of URL credentials/fragments and the
v1.0 path boundary before attaching bearer credentials. Literal and encoded
path traversal is rejected while paging query tokens remain opaque. Redirects
are not followed and produce a safe error. Calendar paging no longer fetches
an unused sixth page after reaching the five-page limit. Strict truncated
reads still fail; best-effort reads retain their bounded partial result.

These are socket-inactivity timeouts, not an end-to-end wall-clock deadline.
DNS, multiple pages, token refresh, SDK transport retries and a response that
continues delivering bytes can make a full operation take longer. No global
socket defaults or process-wide retry settings are changed. Real account
authentication, reconnect and provider outage behavior remain unverified.

Evidence includes real loopback HTTP requests that accept a connection but
withhold a response, testing ordinary Google requests and credential refresh
through the actual authenticated transport. Fixtures shorten the socket
timeout to 0.1 seconds; a separate construction test checks the production 15s
setting and distinct transports. Other cases cover exhausted authentication,
untrusted destinations, redirect rejection without parsing the body, opaque
paging queries and exactly five fetches. Disposable owner-bound app tests
verify socket timeouts become the privacy-safe retryable calendar-unavailable
result. No production users, tokens or external provider requests were used.
Final focused calendar/provider/owner/endpoint checks passed 187 cases. Scoped
Ruff and whitespace checks pass. Independent Python/security and specification
reviews completed, including correction of the path-normalization gap.
Full-suite, hosted and release results are recorded separately after completion.

The transport API and thread-isolation requirements are documented in
[Google authenticated httplib2 transport](https://googleapis.dev/python/google-auth-httplib2/latest/google_auth_httplib2.html)
and [Google client thread safety](https://googleapis.github.io/google-api-python-client/docs/thread_safety.html).
Graph paging requires retaining the complete next-link query, described in
[Microsoft Graph paging](https://learn.microsoft.com/en-us/graph/paging).

The original acceptance ledger, scheduler quality benchmarks, meaningful study
signals/physical devices, live tutor/Fish behavior and paid-launch proof remain
open. No new UI, billing activation or schema changes are part of this slice.
