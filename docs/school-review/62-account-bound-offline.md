# Account-bound offline data and preserved pending changes

The continuation audit found shared URL-keyed API caches, ownerless queued
writes and rendered private HTML in a shared service-worker page cache. After
an account switch those paths could show old information or replay a queued
mutation using the new account's cookies. The existing replay ledger scoped
operation IDs by the current account, but did not identify the original owner.

The shared page template now supplies an authenticated owner marker.
`static/js/ip-queue-core.js` stamps new entries with that owner and filters
listing, counts and deduplication by it. Page and worker flushes read the
uncached `/api/sync/owner` endpoint. A stale page cannot flush a different
owner's queue. Each replay carries an owner header; the server checks it before
the replay ledger or route, including an account switch after preflight.
401/403/409 responses preserve the pending operation without consuming retries.

Other failed operations also stay stored, including after repeated transient
failures. Capacity rejection preserves existing entries. Replacing a deduplicated
entry uses one delete/put transaction. Queue push rejects when persistence fails;
optimistic UI awaits it and visibly rolls back with a Retry action. Scheduler
progress retains its existing local copy and reports enqueue failure. A bug
report's text is cleared only after successful delivery or durable enqueue.

Legacy IndexedDB and localStorage writes have no reliable account provenance.
They remain stored and are not assigned to whichever account signs in next.
Automatic recovery is intentionally paused; explicit owner-validated recovery
is still required. No production records or queued edits were deleted.

`static/js/ip-offline.js` keys new reads by owner and URL and sends the same
owner assertion. It never falls back to cached reads after HTTP 4xx denial.
Legacy unscoped copies are not selected. Authenticated non-static responses use
`private, no-store`. The service worker caches only an explicit public-page
allowlist, respects those response directives and never searches old private
page caches on navigation failure. Its activation removes the obsolete HTML
cache; static update activation still requires the existing reload handshake.
Private full-page offline navigation now shows the honest offline card; scoped
API copies remain usable inside an already-open owner's page.

| Acceptance | Classification | Evidence |
| --- | --- | --- |
| Server rejects mismatched/signed-out replay before mutation and ledger | Verified Working locally | Five new Flask cases,29 total sync tests pass |
| Owner-separated reads, counts and queue dedupe | Verified Working locally | Real Chromium, two tabs and shared IndexedDB |
| Account changes between identity check and send | Verified Working locally | Controlled loopback409; original work retained with unchanged retries |
| Denial cannot expose cached API result | Verified Working locally | Real browser403 path with an existing cached copy |
| Failed owner preflight preserves visible pending count | Verified Working locally | Loopback503 then actual page queue count |
| Repeated server failures preserve edits | Verified Working locally | Six503 flushes followed by successful worker replay |
| Worker replay works without page owner metadata | Verified Working locally | Actual dedicated worker using the same IndexedDB |
| Full queue/storage failure cannot claim saved | Verified Working locally | Capacity60 and injected IDB failure, optimistic rollback, no success message |
| Legacy data retained without reassignment | Verified Working locally | Legacy record remains in actual IndexedDB; old migration rejects |
| Private HTML excluded offline | Verified Working locally | Actual registered service worker, private page loaded, network disabled, no private text in fallback |

Reproduce with `python -m pytest tests/test_sync_idempotency.py -q` and
`python scripts/verify_offline_owner_browser.py`. CI includes the browser verifier.
The fixture uses synthetic loopback identities and provider responses; it does
not use production accounts or prove live authentication. Independent Python/
standards, JavaScript and specification reviews found issues in persistence
confirmation and result ownership; the fixes were re-reviewed with no blocking
findings. Scoped Ruff, JavaScript syntax and whitespace checks pass. Full-suite,
hosted and deployment evidence are separate gates recorded after completion.

Remaining: comprehensive stale-tab/BFCache cleanup, other localStorage users,
encryption, explicit legacy-owner recovery, true multi-context enqueue/flush
serialization, concurrent server idempotency, complete policy revocation during
offline operation and all packaged-client paths. The initial row read and
replacement are not one serialized operation. Shared-origin JavaScript can
still access browser storage; account keying is not encryption or an XSS defense.
The original product transformation and school-adoption requirements remain open.
