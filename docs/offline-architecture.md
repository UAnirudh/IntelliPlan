# Offline architecture

How IntelliPlan behaves without a connection, what it deliberately refuses
to do, and where the seams are.

## The shape of it

Four pieces, each with one job:

| Piece | Lives in | Owns |
|---|---|---|
| Store | `static/js/ip-queue-core.js` (IndexedDB) | The connection, the queue, and the replay rules |
| Read cache | `static/js/ip-offline.js` | The last good copy of each data endpoint |
| Queue API | `static/js/ip-core.js` (`IP.queue`) | The page's half: events, toasts, pending count |
| Replay ledger | `intelliplan/sync/` (Postgres/SQLite) | Making a replayed write apply exactly once |
| Shell cache | `static/sw.js` (Cache Storage) | Pages and static assets |

`ip-queue-core.js` is loaded by the page as a `<script>` and by the service
worker via `importScripts`, so both halves share one IndexedDB connection
(`intelliplan-offline`, v2: `reads` + `queue`) and one set of replay rules.
That sharing is the point — see *Background flush* below.

There is deliberately **one cache per kind of thing**. An earlier version of
the service worker also cached `/api/` responses, which meant two caches of
different ages could answer the same question and the page had no way to
say which it had received. The worker now leaves `/api/` alone entirely.

## Reads

`IP.offline.read(url)` fetches, stores the result in IndexedDB on success,
and falls back to the stored copy on failure. It resolves with:

```js
{ data, fromCache: bool, cachedAt: epochMs|null, stale: bool }
```

It rejects only when the network failed *and* nothing was cached — the one
case where there is genuinely nothing to show.

`stale` is set past `STALE_AFTER_MS` (15 minutes). Stale data is still
displayed: a week-old timetable beats a blank page. But it is **always
labelled** — the dashboard renders "Showing your last synced copy, from N
minutes ago" above the task list whenever any part of it came from cache.
Unlabelled stale data is the failure mode that costs a planner its
credibility, so the label is not optional.

## Writes

A write attempted offline is parked in `IP.queue` with:

- a **stable op id**, minted at enqueue time, not at flush time;
- a `dedupeKey`, so repeated edits to the same field collapse to the latest.

On reconnect (the `online` event, or a page load) the queue replays each
entry with an `X-IP-Op-Id` header. A 4xx is treated as permanent and
dropped; anything else is retried up to five times.

`IP.request` also mints an op id for any **mutating request with retries
enabled**. Before this, `retryUnsafe: true` could apply a write twice on a
live connection — request lands, response times out, retry fires.

Replay is sequential, not parallel: these are edits to the same small set
of objects, and replaying out of order can land an older value on top of a
newer one.

`IP.queue.size()` is synchronous — the connection pill reads it during
render — and returns a count maintained after each operation rather than
querying IndexedDB, which cannot answer synchronously. `IP.queue.all()`
and `.push()` are async. Existing callers only ever used `size()`
synchronously and ignored `push()`'s return, so the move cost them nothing.

### Background flush

A `sync` wake-up tagged `ip-flush-queue` flushes the queue **in the worker**,
with no tab open. A page can also hand a flush to the worker with
`postMessage({type: 'ip-flush-now'})`, which is worth doing when the student
may navigate away mid-send: the worker outlives the tab, an in-page fetch
does not. Either way the worker broadcasts `ip-queue-flushed` so open tabs
correct their pending count instead of describing a queue that has drained.

This is why the queue is in IndexedDB. It used to be in localStorage, which
a worker cannot read, so a wake-up could only poke an open tab — the one
case where the queue would have flushed by itself.

Entries parked by the old localStorage queue are adopted into IndexedDB on
first load and the old key is cleared. They get an op id at migration time,
which makes their replays idempotent *with each other*; it cannot help
against an attempt the server already saw unkeyed, and claiming otherwise
would be worse than the gap.

## The replay ledger

The hard problem is not "the request failed" — that just retries. It is
"the request may or may not have landed": the radio dropped after the bytes
left the device and before the response came back. Replaying is the only
safe response, and replaying `/dismiss` naively awards the streak twice.

So the server records, per `(user_id, op_id)`, the response it produced.
Any later arrival of the same id returns that recording verbatim, with
`X-IP-Replay: 1`, without a handler running.

Implementation notes that matter:

- It is a `before_request`/`after_request` pair, not a decorator. With 306
  routes, a decorator is a list to keep in sync forever, and the cost of
  missing one is silent double-writes.
- **4xx responses are recorded.** A validation failure is a stable outcome;
  a replay must not suddenly succeed against different server state.
- **5xx responses are not.** The server broke; the client should genuinely
  retry, and freezing a 500 would make the failure permanent.
- `(user_id, op_id)` is UNIQUE and the insert is allowed to lose that race
  — two tabs flushing the same queue is the normal case. The loser rolls
  back and the winner's row stands.
- Ops are scoped by user. Client-generated ids can collide across students,
  and an unscoped hit would return one student's response body to another.
- Rows are pruned after 14 days (`intelliplan.sync.prune`).

`POST /api/sync/ops/check` takes a list of op ids and reports which have
already been applied — the client asks once per flush instead of replaying
blind.

## The shell

The service worker:

- **Static assets**: stale-while-revalidate. Safe because every URL carries
  a `?v=` fingerprint derived from file mtimes, so a deploy is a new key.
- **Navigations**: network first, then that exact page from cache, then a
  generic offline card. The page cache is capped at 24 entries.
- **`/api/`, `/live`, `/oauth`, `/cron/`**: not intercepted at all.
- Only genuine `200`s are cached — never redirects, which would otherwise
  pin a `/login` bounce in place of the real page.

### Scope

The worker is registered with `{scope: '/'}`. This is load-bearing: a
worker registered from `/static/sw.js` defaults to a `/static/` scope and
never sees a single navigation, so none of the above runs. App.py sends
`Service-Worker-Allowed: /` to make the wider scope legal; the registration
has to actually ask for it.

## Desktop

The Electron app loads the web app from the same origin, so it inherits all
of the above rather than reimplementing any of it: the service worker
registers inside the renderer, caches pages and assets, and serves them when
the network is gone. Nothing here is desktop-specific.

Verified by killing the server and relaunching the app: the real UI comes up
from cache, not Chromium's error page and not the app's own offline card.

The card in `desktop/src/main.js` is the last resort, on `did-fail-load`. It
only fires when the worker has nothing to serve, which in practice means a
profile that has never loaded IntelliPlan online — confirmed with a fresh
`--user-data-dir`. Its copy says exactly that, because "your plan is safe"
would be false on the only profile that ever sees it.

Two things the desktop still does not share:

* **Its own cookie jar and its own cache.** Signing in on the website does
  not sign you in on the desktop, and each keeps a separate offline copy.
  That is Chromium profile behaviour, not a bug, but it does mean a student
  has to open each one online once.
* **The tray's "what's next"** polls `/api/active/next` every 60 seconds and
  shows nothing when that fails, rather than falling back to the cached
  plan. Degrades quietly; could be better.

## Known limitations

These are real and unfixed, recorded here rather than papered over:

1. **Not every surface reads through the cache.** Dashboard, grades, and
   the saved schedule do. Classes, stats, streak, and the study surfaces
   still fetch directly and show their normal error states offline.
2. **No conflict resolution beyond last-write-wins.** The server applies
   whatever arrives; there is no vector clock and no merge. For the current
   write set — dismissals, manual task edits, test marks — the last write
   is the right answer. It would not be for collaborative group tasks.
3. **Server-dependent features stay server-dependent.** Generating a new
   plan, AI planning, LMS sync, and grade prediction require a connection
   and say so. Re-reading a plan you already made does not.

## Testing

- `tests/test_sync_idempotency.py` — the ledger: replay, scoping,
  what is and is not recorded, the sanitiser, and `/api/sync/ops/check`.
- `tests/test_today_cache_invalidation.py` — the plan cache is evicted by
  plan-changing writes and survives reads and rejected writes.

The client half is verified in a real browser (the preview pane cannot
register a service worker at all). Flows confirmed by hand:

| Flow | Result |
|---|---|
| Kill the server, reload `/dashboard` | Renders from cache, labelled with its age |
| Restart, reload | Label disappears |
| Queue a write, flush twice | Ledger absorbs the second; one application |
| Park a write, `postMessage('ip-flush-now')` | Worker drains it; page count updates |
| Seed the old localStorage queue, reload | Adopted into IndexedDB, old key cleared |
| Read cache after the v1 → v2 upgrade | Still reads and writes |
| Kill the server, reload `/scheduler` | Saved plan renders from cache, labelled |
| Tick a block offline | Parked in the queue, drained on reconnect |
| Grades offline with a cached copy | Renders, labelled with its age |
| Grades offline with no cached copy | "No grades saved on this device yet" |
