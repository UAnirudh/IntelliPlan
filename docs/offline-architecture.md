# Offline architecture

How IntelliPlan behaves without a connection, what it deliberately refuses
to do, and where the seams are.

## The shape of it

Four pieces, each with one job:

| Piece | Lives in | Owns |
|---|---|---|
| Read cache | `static/js/ip-offline.js` (IndexedDB) | The last good copy of each data endpoint |
| Write queue | `static/js/ip-core.js` (`IP.queue`, localStorage) | Writes attempted while offline |
| Replay ledger | `intelliplan/sync/` (Postgres/SQLite) | Making a replayed write apply exactly once |
| Shell cache | `static/sw.js` (Cache Storage) | Pages and static assets |

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

## Known limitations

These are real and unfixed, recorded here rather than papered over:

1. **Background Sync cannot flush the queue on its own.** The queue is in
   localStorage, which a worker cannot read. A `sync` wake-up posts a
   message to any open tab; with no tab open, the writes wait for the next
   visit. Moving the queue into IndexedDB would fix this and is the obvious
   next step.
2. **Only the dashboard reads through the cache so far.** Scheduler,
   grades, and the study surfaces still fetch directly and will show their
   normal error states offline.
3. **No conflict resolution beyond last-write-wins.** The server applies
   whatever arrives; there is no vector clock and no merge. For the current
   write set — dismissals, manual task edits, test marks — the last write
   is the right answer. It would not be for collaborative group tasks.
4. **Server-dependent features stay server-dependent.** AI planning, LMS
   sync, and grade prediction require a connection and say so.

## Testing

- `tests/test_sync_idempotency.py` — the ledger: replay, scoping,
  what is and is not recorded, the sanitiser, and `/api/sync/ops/check`.
- Browser-level verification of the read cache and the offline pill is
  manual for now; the flows are listed in the roadmap.
