/* ==========================================================================
   ip-offline.js — offline reads, and an honest account of what is missing
   --------------------------------------------------------------------------
   IP.queue (ip-core.js) already parks *writes* attempted while offline. This
   file is the other half: it keeps the last good copy of the *reads* a page
   needs, so opening IntelliPlan in a tunnel shows the student their actual
   assignments instead of a spinner and an apology.

     IP.offline.read(url)      fetch, cache on success, fall back on failure
     IP.offline.cachedAt(url)  when that copy was taken, or null
     IP.offline.forget(url)    drop one entry (sign-out, account switch)
     IP.offline.clear()        drop everything
     IP.offline.status()       { online, pending, staleSince }

   Storage is IndexedDB, not localStorage: a term's worth of assignments is
   far past what a 5MB string store should hold, and localStorage writes are
   synchronous on the main thread.

   What this does NOT do, deliberately
   -----------------------------------
   It does not pretend every surface works offline. A cached read is labelled
   as one, and anything requiring the server — AI planning, LMS sync, grade
   prediction — is expected to say so rather than fail obscurely.
   ========================================================================== */
(function (window, document) {
  'use strict';

  var IP = window.IP || (window.IP = {});

  //: The connection is owned by ip-queue-core.js, which the service worker
  //: also loads. Opening a second one here at a different version would
  //: block whichever got there second on a `versionchange` it cannot
  //: satisfy, and the reads cache would hang instead of failing.
  var Core = window.IPQueueCore || null;
  var READ_STORE = Core ? Core.READ_STORE : 'reads';

  //: A cached read older than this is still shown — a week-old timetable
  //: beats a blank page — but it is labelled with its age so the student
  //: is never misled about how current it is.
  var STALE_AFTER_MS = 15 * 60 * 1000;

  /* ── IndexedDB, wrapped just enough ───────────────────────────────── */

  function openDb() {
    if (!Core) return Promise.reject(new Error('IPQueueCore not loaded'));
    return Core.openDb();
  }

  function tx(mode, fn) {
    return openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var t = db.transaction(READ_STORE, mode);
        var store = t.objectStore(READ_STORE);
        var out;
        try { out = fn(store); } catch (e) { reject(e); return; }
        // `'result' in out`, not `out.result !== undefined`. A cache *miss*
        // is an IDBRequest whose result is undefined, and the looser test
        // fell through to resolving the request object itself — truthy, so
        // a miss read as a hit carrying no data. The page then announced
        // "showing your last synced copy" over a blank screen.
        t.oncomplete = function () {
          resolve(out && typeof out === 'object' && 'result' in out ? out.result : out);
        };
        t.onerror    = function () { reject(t.error); };
        t.onabort    = function () { reject(t.error || new Error('aborted')); };
      });
    });
  }

  function putRead(url, data) {
    return tx('readwrite', function (store) {
      store.put({ url: url, data: data, ts: Date.now() });
    }).catch(function () { /* a cache that cannot write is not an error */ });
  }

  function getRead(url) {
    return tx('readonly', function (store) {
      return store.get(url);
    }).catch(function () { return null; });
  }

  /* ── The read path ────────────────────────────────────────────────── */

  /**
   * Fetch `url`, caching the result; on failure, serve the last good copy.
   *
   * Resolves with `{ data, fromCache, cachedAt, stale }`. Rejects only when
   * the network failed *and* nothing was cached — at which point the caller
   * genuinely has nothing to show and should say so.
   */
  function read(url, options) {
    var opts = options || {};
    var offlineNow = navigator.onLine === false;

    var fromNetwork = offlineNow
      ? Promise.reject(new Error('offline'))
      : IP.request(url, { retries: opts.retries != null ? opts.retries : 1,
                          timeout: opts.timeout });

    return fromNetwork.then(function (data) {
      putRead(url, data);
      return { data: data, fromCache: false, cachedAt: Date.now(), stale: false };
    }, function (err) {
      return getRead(url).then(function (row) {
        if (!row) throw err;
        var age = Date.now() - (row.ts || 0);
        return {
          data: row.data,
          fromCache: true,
          cachedAt: row.ts || null,
          stale: age > STALE_AFTER_MS
        };
      });
    });
  }

  function cachedAt(url) {
    return getRead(url).then(function (row) { return row ? row.ts : null; });
  }

  function forget(url) {
    return tx('readwrite', function (store) { store.delete(url); })
      .catch(function () {});
  }

  function clear() {
    return tx('readwrite', function (store) { store.clear(); })
      .catch(function () {});
  }

  function status() {
    return {
      online: navigator.onLine !== false,
      pending: IP.queue ? IP.queue.size() : 0
    };
  }

  IP.offline = {
    read: read,
    cachedAt: cachedAt,
    forget: forget,
    clear: clear,
    status: status,
    STALE_AFTER_MS: STALE_AFTER_MS
  };

  /* ── Status affordance ────────────────────────────────────────────── */

  /**
   * One line, bottom-left, only while there is something true to say. It
   * says what is happening and what will happen next — "changes will sync"
   * — because "You are offline" alone leaves the student wondering whether
   * the tap they just made survived.
   */
  var pill = null;

  function ensurePill() {
    if (pill) return pill;
    pill = document.createElement('div');
    pill.className = 'ip-offline-pill';
    pill.setAttribute('role', 'status');
    pill.setAttribute('aria-live', 'polite');
    pill.hidden = true;
    document.body.appendChild(pill);
    return pill;
  }

  function renderStatus() {
    if (!document.body) return;
    var s = status();
    var el = ensurePill();

    if (s.online && !s.pending) { el.hidden = true; return; }

    var text;
    if (!s.online && s.pending) {
      text = 'Offline · ' + s.pending + ' change' + (s.pending === 1 ? '' : 's') +
             ' will sync when you reconnect';
    } else if (!s.online) {
      text = 'Offline · showing your last synced copy';
    } else {
      text = 'Syncing ' + s.pending + ' change' + (s.pending === 1 ? '' : 's') + '…';
    }
    el.textContent = text;
    el.dataset.state = s.online ? 'syncing' : 'offline';
    el.hidden = false;
  }

  window.addEventListener('online', renderStatus);
  window.addEventListener('offline', renderStatus);
  if (IP.on) {
    IP.on('queue:change', renderStatus);
    IP.on('queue:flushed', renderStatus);
  }

  if (document.readyState !== 'loading') renderStatus();
  else document.addEventListener('DOMContentLoaded', renderStatus);

  /* ── Service-worker liaison ───────────────────────────────────────── */

  // The worker flushes the queue itself now — both halves share the same
  // IndexedDB store via ip-queue-core.js. What arrives here is the report
  // of a flush that already happened, so the pending count and the pill do
  // not sit there describing a queue that has since drained.
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('message', function (event) {
      var data = event.data || {};
      if (data.type === 'ip-queue-flushed' && IP.queue) {
        IP.queue.refresh().then(renderStatus);
        var sent = (data.result || {}).sent || 0;
        if (sent && IP.toast) {
          IP.toast(sent + ' offline change' + (sent === 1 ? '' : 's') +
                   ' synced in the background.', 'success');
        }
      }
    });
  }

  function requestBackgroundSync() {
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker.ready.then(function (reg) {
      if (reg.sync) return reg.sync.register('ip-flush-queue');
    }).catch(function () { /* unsupported, or denied — the online listener covers it */ });
  }

  if (IP.on) IP.on('queue:change', requestBackgroundSync);

})(window, document);
