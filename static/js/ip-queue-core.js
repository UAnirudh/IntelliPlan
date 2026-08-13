/* ==========================================================================
   ip-queue-core.js — the offline store, shared by the page and the worker
   --------------------------------------------------------------------------
   This file exists because of one constraint: a service worker cannot read
   localStorage. The offline write queue used to live there, so a Background
   Sync wake-up had nothing to flush and could only poke an open tab — which
   is precisely the case where the student is already back and the queue
   would have flushed anyway. Background sync that only works when you don't
   need it is not background sync.

   So the queue moved to IndexedDB and the logic moved here, where both
   `window` and `ServiceWorkerGlobalScope` can load it: the page via a
   <script> tag, the worker via importScripts().

       IPQueueCore.openDb()          the shared connection (reads + queue)
       IPQueueCore.enqueue(entry)    park a write
       IPQueueCore.all()             everything parked, oldest first
       IPQueueCore.size()            how many
       IPQueueCore.flush()           replay them; resolves {sent, failed, kept}
       IPQueueCore.migrateFrom(arr)  adopt the old localStorage entries

   No DOM, no IP.*, no dependencies. Anything that needs a toast or an event
   is the caller's job — this half has to run in a worker where neither
   exists.
   ========================================================================== */
(function (scope) {
  'use strict';

  var DB_NAME     = 'intelliplan-offline';
  //: v2 adds the `queue` store beside the `reads` store v1 created. The page
  //: and the worker must agree on this number or whichever opens first
  //: blocks the other on a versionchange it cannot satisfy.
  var DB_VERSION  = 2;
  var READ_STORE  = 'reads';
  var QUEUE_STORE = 'queue';

  var MAX_ITEMS   = 60;
  var MAX_TRIES   = 5;
  var OP_ID_HEADER = 'X-IP-Op-Id';

  /* ── Connection ───────────────────────────────────────────────────── */

  var dbPromise = null;

  function openDb() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise(function (resolve, reject) {
      if (!scope.indexedDB) { reject(new Error('IndexedDB unavailable')); return; }
      var req = scope.indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = function () {
        var db = req.result;
        if (!db.objectStoreNames.contains(READ_STORE)) {
          db.createObjectStore(READ_STORE, { keyPath: 'url' });
        }
        if (!db.objectStoreNames.contains(QUEUE_STORE)) {
          // keyPath `id`, assigned by us rather than auto-incremented: the
          // page hands ids to the UI so it can show a write as pending, and
          // an autoIncrement key is not known until the write commits.
          var store = db.createObjectStore(QUEUE_STORE, { keyPath: 'id' });
          store.createIndex('ts', 'ts');
        }
      };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror   = function () { reject(req.error); };
      // Private browsing and a wedged upgrade both hang rather than error.
      // Without this the first call never settles and the caller waits for
      // a database that is not coming.
      req.onblocked = function () { reject(new Error('IndexedDB blocked')); };
    }).catch(function (err) {
      dbPromise = null;               // let a later call try again
      throw err;
    });
    return dbPromise;
  }

  function tx(storeName, mode, fn) {
    return openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var t = db.transaction(storeName, mode);
        var out;
        try { out = fn(t.objectStore(storeName)); } catch (e) { reject(e); return; }
        t.oncomplete = function () {
          resolve(out && typeof out === 'object' && 'result' in out ? out.result : out);
        };
        t.onerror = function () { reject(t.error); };
        t.onabort = function () { reject(t.error || new Error('aborted')); };
      });
    });
  }

  /* ── Ids ──────────────────────────────────────────────────────────── */

  /** One key per logical write, stable across every retry of it. */
  function newOpId() {
    var c = scope.crypto;
    if (c && c.randomUUID) { try { return c.randomUUID(); } catch (e) {} }
    return 'op-' + Date.now().toString(36) + '-' +
           Math.random().toString(36).slice(2, 10);
  }

  /* ── Queue ────────────────────────────────────────────────────────── */

  function all() {
    return tx(QUEUE_STORE, 'readonly', function (store) {
      return store.getAll();
    }).then(function (rows) {
      return (rows || []).sort(function (a, b) { return (a.ts || 0) - (b.ts || 0); });
    }).catch(function () { return []; });
  }

  function size() {
    return tx(QUEUE_STORE, 'readonly', function (store) { return store.count(); })
      .catch(function () { return 0; });
  }

  function remove(id) {
    return tx(QUEUE_STORE, 'readwrite', function (store) { store.delete(id); })
      .catch(function () {});
  }

  function put(item) {
    return tx(QUEUE_STORE, 'readwrite', function (store) { store.put(item); })
      .catch(function () {});
  }

  /**
   * Park a write. Resolves with the stored entry.
   *
   * `dedupeKey` collapses repeated edits to the same field, so typing in a
   * notes box offline replays once with the final value instead of replaying
   * every keystroke. The op id is minted here and never re-minted — see the
   * ledger in intelliplan/sync for why that is the whole ballgame.
   */
  function enqueue(entry) {
    var item = {
      id: 'q' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7),
      opId: entry.opId || newOpId(),
      url: entry.url,
      method: entry.method || 'POST',
      body: entry.body,
      dedupeKey: entry.dedupeKey || '',
      label: entry.label || '',
      ts: Date.now(),
      tries: 0
    };
    return all().then(function (rows) {
      var doomed = [];
      if (item.dedupeKey) {
        doomed = rows.filter(function (r) { return r.dedupeKey === item.dedupeKey; });
      }
      // Oldest-out when full. A queue that refuses new writes because it is
      // full loses the write the student just made, which is the one they
      // are still looking at.
      var surplus = (rows.length - doomed.length) - (MAX_ITEMS - 1);
      if (surplus > 0) doomed = doomed.concat(rows.slice(0, surplus));

      return Promise.all(doomed.map(function (r) { return remove(r.id); }))
        .then(function () { return put(item); })
        .then(function () { return item; });
    });
  }

  /* ── Flush ────────────────────────────────────────────────────────── */

  function sendOne(item) {
    var headers = { 'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest' };
    if (item.opId) headers[OP_ID_HEADER] = item.opId;
    var body = item.body;
    if (body != null && typeof body !== 'string') {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(body);
    }
    return fetch(item.url, {
      method: item.method || 'POST',
      headers: headers,
      body: body,
      credentials: 'same-origin',
      cache: 'no-store'
    });
  }

  /**
   * Replay every parked write, oldest first. Resolves
   * ``{sent, failed, kept}`` and never rejects — a flush that throws in a
   * `sync` handler makes the browser retry the whole wake-up.
   *
   * Sequential rather than parallel on purpose: these are edits to the same
   * small set of objects, and replaying them out of order can land an older
   * value on top of a newer one.
   */
  function flush() {
    if (scope.navigator && scope.navigator.onLine === false) {
      return size().then(function (n) { return { sent: 0, failed: 0, kept: n }; });
    }
    return all().then(function (items) {
      if (!items.length) return { sent: 0, failed: 0, kept: 0 };
      var sent = 0, failed = 0, kept = 0;

      return items.reduce(function (chain, item) {
        return chain.then(function () {
          return sendOne(item).then(function (resp) {
            if (resp.ok) { sent++; return remove(item.id); }
            // 4xx is a verdict, not a hiccup: the same bytes will be
            // rejected the same way forever, so drop it rather than loop.
            // 429 is the exception — that one means "later".
            if (resp.status >= 400 && resp.status < 500 && resp.status !== 429) {
              failed++;
              return remove(item.id);
            }
            throw new Error('http ' + resp.status);
          });
        }).catch(function () {
          item.tries = (item.tries || 0) + 1;
          if (item.tries >= MAX_TRIES) { failed++; return remove(item.id); }
          kept++;
          return put(item);
        });
      }, Promise.resolve()).then(function () {
        return { sent: sent, failed: failed, kept: kept };
      });
    }).catch(function () {
      return { sent: 0, failed: 0, kept: 0 };
    });
  }

  /* ── Migration ────────────────────────────────────────────────────── */

  /**
   * Adopt entries from the old localStorage queue. Called once by the page;
   * the worker never needs it, since it could not read that store anyway.
   *
   * Pre-existing entries have no op id. One is minted here so their replays
   * are at least idempotent with each other — it cannot help against an
   * attempt the server already saw unkeyed, and pretending otherwise would
   * be worse than the honest gap.
   */
  function migrateFrom(items) {
    if (!Array.isArray(items) || !items.length) return Promise.resolve(0);
    return items.reduce(function (chain, old) {
      return chain.then(function (n) {
        return enqueue({
          opId: old.opId || newOpId(),
          url: old.url, method: old.method, body: old.body,
          dedupeKey: old.dedupeKey, label: old.label
        }).then(function () { return n + 1; });
      });
    }, Promise.resolve(0));
  }

  scope.IPQueueCore = {
    openDb: openDb,
    READ_STORE: READ_STORE,
    QUEUE_STORE: QUEUE_STORE,
    OP_ID_HEADER: OP_ID_HEADER,
    MAX_ITEMS: MAX_ITEMS,
    MAX_TRIES: MAX_TRIES,
    newOpId: newOpId,
    enqueue: enqueue,
    all: all,
    size: size,
    remove: remove,
    flush: flush,
    migrateFrom: migrateFrom
  };

})(typeof self !== 'undefined' ? self : this);
