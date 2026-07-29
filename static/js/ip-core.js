/* ==========================================================================
   ip-core.js — IntelliPlan client runtime primitives
   --------------------------------------------------------------------------
   Provides the plumbing every page shares:

     IP.request()   fetch with timeout, abort, and backoff retry
     IP.ApiError    one error shape, with a `retriable` flag
     IP.store       localStorage with TTL, namespacing, and quota recovery
     IP.queue       offline mutation queue, flushed when the tab reconnects
     IP.report()    client error reporting to /api/client-error
     IP.toast()     non-blocking status messages (never window.alert)

   No build step, no dependencies. Loaded before ip-async.js.
   ========================================================================== */
(function (window, document) {
  'use strict';

  var IP = window.IP || (window.IP = {});

  /* ── Constants ────────────────────────────────────────────────────── */

  var STORE_PREFIX     = 'ip:';
  var QUEUE_KEY        = 'ip:__queue';
  var DEFAULT_TIMEOUT  = 15000;
  var DEFAULT_RETRIES  = 2;
  var BACKOFF_BASE_MS  = 400;
  var BACKOFF_CAP_MS   = 8000;
  var MAX_REPORTS_PER_LOAD = 12;
  var QUEUE_MAX_TRIES  = 5;
  var QUEUE_MAX_ITEMS  = 60;
  var TOAST_MS         = 4200;

  /* ── Errors ───────────────────────────────────────────────────────── */

  /**
   * Normalised failure. Everything that can go wrong in a request — network
   * down, timeout, 4xx, 5xx, malformed JSON — arrives as one of these so
   * callers only ever branch on `.retriable` and `.message`.
   */
  function ApiError(message, opts) {
    opts = opts || {};
    this.name      = 'ApiError';
    this.message   = message || 'Request failed';
    this.status    = opts.status || 0;
    this.code      = opts.code || '';
    this.body      = opts.body;
    this.url       = opts.url || '';
    this.retriable = !!opts.retriable;
    this.offline   = !!opts.offline;
    if (Error.captureStackTrace) Error.captureStackTrace(this, ApiError);
  }
  ApiError.prototype = Object.create(Error.prototype);
  ApiError.prototype.constructor = ApiError;

  /** Human-facing copy. Plain and specific, never "Oops!". */
  ApiError.prototype.friendly = function () {
    if (this.offline) return 'You are offline. Reconnect and try again.';
    if (this.status === 401) return 'Your session expired. Sign in again to continue.';
    if (this.status === 403) return 'You do not have access to this.';
    if (this.status === 404) return 'That is no longer available.';
    if (this.status === 429) return 'Too many requests. Wait a moment, then retry.';
    if (this.status >= 500)  return 'The server had a problem. Retrying usually fixes it.';
    if (this.code === 'timeout') return 'The request took too long to respond.';
    if (this.status === 0)   return 'Could not reach IntelliPlan. Check your connection.';
    return this.message;
  };

  IP.ApiError = ApiError;

  /* ── Request ──────────────────────────────────────────────────────── */

  var inflight = Object.create(null);   // dedupe identical concurrent GETs

  function sleep(ms) {
    return new Promise(function (r) { setTimeout(r, ms); });
  }

  /** Full jitter exponential backoff, capped. Avoids retry stampedes. */
  function backoffDelay(attempt, retryAfterSec) {
    if (retryAfterSec > 0) return Math.min(retryAfterSec * 1000, BACKOFF_CAP_MS * 2);
    var ceiling = Math.min(BACKOFF_BASE_MS * Math.pow(2, attempt), BACKOFF_CAP_MS);
    return Math.round(Math.random() * ceiling);
  }

  function parseRetryAfter(res) {
    var raw = res && res.headers && res.headers.get('Retry-After');
    if (!raw) return 0;
    var secs = parseInt(raw, 10);
    return isNaN(secs) ? 0 : secs;
  }

  function isRetriableStatus(status) {
    return status === 408 || status === 425 || status === 429 ||
           status === 500 || status === 502 || status === 503 || status === 504;
  }

  /**
   * fetch() with the parts every caller kept re-implementing: a timeout that
   * actually aborts, retry with backoff on transient failures only, JSON
   * parsing, and a single error shape.
   *
   * Retries are safe by default: GET/HEAD retry automatically, POST/PATCH/
   * DELETE only when the caller passes `retryUnsafe: true` (use it for
   * idempotent writes such as upserts).
   */
  IP.request = function (url, options) {
    var opts     = options || {};
    var method   = (opts.method || 'GET').toUpperCase();
    var isSafe   = method === 'GET' || method === 'HEAD';
    var retries  = opts.retries != null ? opts.retries
                 : (isSafe || opts.retryUnsafe ? DEFAULT_RETRIES : 0);
    var timeout  = opts.timeout != null ? opts.timeout : DEFAULT_TIMEOUT;
    var dedupe   = isSafe && opts.dedupe !== false;
    var key      = method + ' ' + url;

    if (dedupe && inflight[key]) return inflight[key];

    var promise = (function attempt(n) {
      var controller = new AbortController();
      var timedOut   = false;
      var timer      = setTimeout(function () {
        timedOut = true;
        controller.abort();
      }, timeout);

      // Let callers cancel too (page navigation, block teardown).
      if (opts.signal) {
        if (opts.signal.aborted) controller.abort();
        else opts.signal.addEventListener('abort', function () { controller.abort(); });
      }

      var headers = Object.assign({
        'Accept': 'application/json',
        'X-Requested-With': 'XMLHttpRequest'
      }, opts.headers || {});

      var body = opts.body;
      if (body != null && typeof body !== 'string' && !(body instanceof FormData)) {
        headers['Content-Type'] = 'application/json';
        body = JSON.stringify(body);
      }

      return fetch(url, {
        method: method,
        headers: headers,
        body: isSafe ? undefined : body,
        credentials: 'same-origin',
        signal: controller.signal,
        cache: opts.cache || 'no-store'
      }).then(function (res) {
        clearTimeout(timer);
        return readBody(res).then(function (parsed) {
          if (res.ok) {
            // Some endpoints answer 200 with {status:"error"}. Treat as failure.
            if (parsed && parsed.status === 'error') {
              throw new ApiError(parsed.message || 'Request failed', {
                status: res.status, url: url, body: parsed, retriable: false
              });
            }
            return parsed;
          }
          throw new ApiError(
            (parsed && (parsed.message || parsed.error)) || ('HTTP ' + res.status),
            {
              status: res.status,
              url: url,
              body: parsed,
              retriable: isRetriableStatus(res.status),
              code: 'http'
            }
          );
        }).then(null, function (err) {
          if (err instanceof ApiError) {
            err.retryAfter = parseRetryAfter(res);
            throw err;
          }
          throw err;
        });
      }, function (err) {
        clearTimeout(timer);
        if (timedOut) {
          throw new ApiError('Request timed out', {
            code: 'timeout', url: url, retriable: true
          });
        }
        if (err && err.name === 'AbortError') {
          throw new ApiError('Request cancelled', { code: 'abort', url: url });
        }
        throw new ApiError('Network request failed', {
          code: 'network', url: url, retriable: true,
          offline: navigator.onLine === false
        });
      }).then(null, function (err) {
        var canRetry = n < retries && err instanceof ApiError &&
                       err.retriable && err.code !== 'abort';
        if (!canRetry) throw err;
        return sleep(backoffDelay(n, err.retryAfter)).then(function () {
          return attempt(n + 1);
        });
      });
    })(0);

    if (dedupe) {
      inflight[key] = promise;
      promise.then(
        function () { delete inflight[key]; },
        function () { delete inflight[key]; }
      );
    }
    return promise;
  };

  function readBody(res) {
    var type = res.headers.get('Content-Type') || '';
    if (res.status === 204) return Promise.resolve(null);
    if (type.indexOf('application/json') !== -1) {
      return res.json().then(null, function () { return null; });
    }
    return res.text().then(function (t) { return t; }, function () { return null; });
  }

  /* Convenience verbs. */
  IP.get  = function (url, o) { return IP.request(url, Object.assign({}, o, { method: 'GET' })); };
  IP.post = function (url, body, o) { return IP.request(url, Object.assign({}, o, { method: 'POST', body: body })); };

  /**
   * Walk every page of a paginated endpoint and return the concatenated
   * items. For the callers that genuinely need the whole set (a cache the
   * rest of the page filters client-side, an export) — the server stays
   * bounded per request, but nothing is silently truncated.
   *
   * `maxPages` is a runaway guard, not a product limit; when it trips the
   * caller is told via the `truncated` flag rather than quietly given a
   * short list.
   */
  IP.getAll = function (url, options) {
    var opts     = options || {};
    var perPage  = opts.perPage || 100;
    var maxPages = opts.maxPages || 25;
    var key      = opts.key || 'items';
    var sep      = url.indexOf('?') === -1 ? '?' : '&';
    var all      = [];

    function page(n) {
      return IP.get(url + sep + 'per_page=' + perPage + '&page=' + n, opts).then(function (res) {
        var items = (res && (res[key] || res.items || res.results)) || [];
        all = all.concat(items);
        if (res && res.has_more && n < maxPages) return page(n + 1);
        return {
          items: all,
          truncated: !!(res && res.has_more && n >= maxPages),
          pages: n
        };
      });
    }
    return page(1);
  };

  /* ── Persistence ──────────────────────────────────────────────────── */

  /**
   * localStorage with an expiry envelope. Every value round-trips as
   * {v, t, ttl} so a stale cache can still be served instantly while a fresh
   * request runs behind it (stale-while-revalidate).
   *
   * Writes never throw. On QuotaExceededError we drop the oldest IP entries
   * and retry once, then give up silently — a failed cache write must never
   * break a page.
   */
  var store = {
    get: function (key, opts) {
      opts = opts || {};
      try {
        var raw = localStorage.getItem(STORE_PREFIX + key);
        if (!raw) return null;
        var env = JSON.parse(raw);
        if (!env || typeof env !== 'object' || !('v' in env)) return null;
        var age = Date.now() - (env.t || 0);
        if (env.ttl && age > env.ttl) {
          if (!opts.allowStale) { store.del(key); return null; }
          return { value: env.v, stale: true, age: age };
        }
        return { value: env.v, stale: false, age: age };
      } catch (e) { return null; }
    },

    /** Returns the value only, or `fallback`. For call sites that ignore staleness. */
    value: function (key, fallback) {
      var hit = store.get(key);
      return hit ? hit.value : (fallback === undefined ? null : fallback);
    },

    set: function (key, value, ttl) {
      var env = JSON.stringify({ v: value, t: Date.now(), ttl: ttl || 0 });
      try {
        localStorage.setItem(STORE_PREFIX + key, env);
        return true;
      } catch (e) {
        if (!evictOldest(8)) return false;
        try { localStorage.setItem(STORE_PREFIX + key, env); return true; }
        catch (e2) { return false; }
      }
    },

    del: function (key) {
      try { localStorage.removeItem(STORE_PREFIX + key); } catch (e) {}
    },

    /** Drop everything under a namespace, e.g. store.clear('pet:'). */
    clear: function (prefix) {
      try {
        var full = STORE_PREFIX + (prefix || '');
        var doomed = [];
        for (var i = 0; i < localStorage.length; i++) {
          var k = localStorage.key(i);
          if (k && k.indexOf(full) === 0 && k !== QUEUE_KEY) doomed.push(k);
        }
        doomed.forEach(function (k) { localStorage.removeItem(k); });
      } catch (e) {}
    }
  };

  function evictOldest(count) {
    try {
      var entries = [];
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (!k || k.indexOf(STORE_PREFIX) !== 0 || k === QUEUE_KEY) continue;
        var t = 0;
        try { t = (JSON.parse(localStorage.getItem(k)) || {}).t || 0; } catch (e) {}
        entries.push({ k: k, t: t });
      }
      if (!entries.length) return false;
      entries.sort(function (a, b) { return a.t - b.t; });
      entries.slice(0, count).forEach(function (e) { localStorage.removeItem(e.k); });
      return true;
    } catch (e) { return false; }
  }

  IP.store = store;

  /* ── Offline mutation queue ───────────────────────────────────────── */

  /**
   * Writes attempted while offline are parked here and replayed when the tab
   * reconnects, so a dropped connection never silently loses a user action.
   * Entries carry a `dedupeKey` so repeated edits to the same field collapse
   * into the latest value instead of replaying a whole edit history.
   */
  var queue = {
    all: function () {
      try { return JSON.parse(localStorage.getItem(QUEUE_KEY) || '[]') || []; }
      catch (e) { return []; }
    },

    _save: function (items) {
      try { localStorage.setItem(QUEUE_KEY, JSON.stringify(items.slice(-QUEUE_MAX_ITEMS))); }
      catch (e) {}
    },

    push: function (entry) {
      var items = queue.all();
      if (entry.dedupeKey) {
        items = items.filter(function (i) { return i.dedupeKey !== entry.dedupeKey; });
      }
      items.push({
        id: 'q' + Date.now() + Math.random().toString(36).slice(2, 7),
        url: entry.url,
        method: entry.method || 'POST',
        body: entry.body,
        dedupeKey: entry.dedupeKey || '',
        label: entry.label || '',
        ts: Date.now(),
        tries: 0
      });
      queue._save(items);
      IP.emit('queue:change', { size: items.length });
      return items.length;
    },

    /** Replay every parked write. Resolves with {sent, failed, kept}. */
    flush: function () {
      var items = queue.all();
      if (!items.length || navigator.onLine === false) {
        return Promise.resolve({ sent: 0, failed: 0, kept: items.length });
      }
      var sent = 0, failed = 0, kept = [];

      return items.reduce(function (chain, item) {
        return chain.then(function () {
          return IP.request(item.url, {
            method: item.method, body: item.body, retries: 1, retryUnsafe: true
          }).then(function () {
            sent++;
          }, function (err) {
            item.tries = (item.tries || 0) + 1;
            // A 4xx will never succeed on replay — drop it rather than loop.
            var permanent = err.status >= 400 && err.status < 500 && err.status !== 429;
            if (!permanent && item.tries < QUEUE_MAX_TRIES) kept.push(item);
            else failed++;
          });
        });
      }, Promise.resolve()).then(function () {
        queue._save(kept);
        IP.emit('queue:change', { size: kept.length });
        if (sent) IP.emit('queue:flushed', { sent: sent, failed: failed });
        return { sent: sent, failed: failed, kept: kept.length };
      });
    },

    size: function () { return queue.all().length; }
  };

  IP.queue = queue;

  window.addEventListener('online', function () {
    queue.flush().then(function (r) {
      if (r.sent) IP.toast('Back online. ' + r.sent + ' pending change' + (r.sent === 1 ? '' : 's') + ' saved.', 'success');
      if (r.failed) IP.toast(r.failed + ' change' + (r.failed === 1 ? ' could not be' : 's could not be') + ' saved.', 'error');
    });
  });

  /* ── Tiny event bus ───────────────────────────────────────────────── */

  var listeners = Object.create(null);

  IP.on = function (name, fn) {
    (listeners[name] || (listeners[name] = [])).push(fn);
    return function () { IP.off(name, fn); };
  };
  IP.off = function (name, fn) {
    if (!listeners[name]) return;
    listeners[name] = listeners[name].filter(function (f) { return f !== fn; });
  };
  IP.emit = function (name, detail) {
    (listeners[name] || []).forEach(function (fn) {
      try { fn(detail); } catch (e) { /* a bad listener must not break the emitter */ }
    });
  };

  /* ── Error reporting ──────────────────────────────────────────────── */

  var reportCount = 0;
  var seenFingerprints = Object.create(null);
  var recent = [];                      // ring buffer, attached to bug reports
  var RECENT_MAX = 10;

  /** The last few failures this page saw. Used to enrich a bug report. */
  IP.recentErrors = function () { return recent.slice(); };

  function fingerprint(payload) {
    return [payload.kind, payload.message, payload.source, payload.line].join('|');
  }

  /**
   * Send a client-side failure to the server. Deduped by fingerprint and
   * capped per page load so a render loop cannot flood the endpoint.
   * Reporting failures are swallowed — the reporter must never be the thing
   * that breaks the page.
   */
  IP.report = function (error, context) {
    try {
      var err = error || {};
      var payload = {
        kind:      (context && context.kind) || 'error',
        message:   String(err.message || err || 'Unknown error').slice(0, 500),
        stack:     String(err.stack || '').slice(0, 4000),
        source:    (context && context.source) || '',
        line:      (context && context.line) || 0,
        url:       location.pathname + location.search,
        ua:        navigator.userAgent.slice(0, 300),
        viewport:  window.innerWidth + 'x' + window.innerHeight,
        online:    navigator.onLine !== false,
        ts:        new Date().toISOString(),
        context:   (context && context.extra) ? JSON.stringify(context.extra).slice(0, 1000) : ''
      };

      recent.push({ t: payload.ts, kind: payload.kind, message: payload.message, url: payload.url });
      if (recent.length > RECENT_MAX) recent.shift();

      var fp = fingerprint(payload);
      if (seenFingerprints[fp]) return;
      if (reportCount >= MAX_REPORTS_PER_LOAD) return;
      seenFingerprints[fp] = true;
      reportCount++;

      var json = JSON.stringify(payload);
      if (navigator.sendBeacon) {
        navigator.sendBeacon('/api/client-error', new Blob([json], { type: 'application/json' }));
      } else {
        fetch('/api/client-error', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: json,
          credentials: 'same-origin',
          keepalive: true
        }).catch(function () {});
      }
    } catch (e) { /* reporting is best-effort */ }
  };

  window.addEventListener('error', function (e) {
    // Ignore resource load errors (img/script) — they carry no useful stack.
    if (e.target && e.target !== window && e.target.tagName) return;
    IP.report(e.error || { message: e.message }, {
      kind: 'window.error', source: e.filename || '', line: e.lineno || 0
    });
  }, true);

  window.addEventListener('unhandledrejection', function (e) {
    var reason = e.reason;
    // A cancelled request is normal during navigation, not a defect.
    if (reason && reason.code === 'abort') return;
    IP.report(reason, { kind: 'unhandledrejection' });
  });

  /* ── Toast ────────────────────────────────────────────────────────── */

  var toastHost = null;

  function ensureToastHost() {
    if (toastHost && document.body.contains(toastHost)) return toastHost;
    toastHost = document.createElement('div');
    toastHost.className = 'ip-toast-host';
    toastHost.setAttribute('role', 'status');
    toastHost.setAttribute('aria-live', 'polite');
    document.body.appendChild(toastHost);
    return toastHost;
  }

  /* A shape per status, so the meaning survives colour blindness, a
     greyscale screenshot, and forced-colours mode. The outlines have to
     differ in silhouette, not just in glyph: an inverted "!" and "i" both
     read as a marked circle at 18px. Error borrows the same triangle the
     error-state card uses, so the two agree. */
  var TOAST_ICONS = {
    success: '<polyline points="20 6 9 17 4 12"/>',
    error:   '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>' +
             '<path d="M12 9v4"/><path d="M12 17h.01"/>',
    info:    '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/>'
  };

  /**
   * Replaces window.alert() everywhere. `type` is 'info' | 'success' |
   * 'error'. Returns a dismiss function so callers can close it early.
   */
  IP.toast = function (message, type, opts) {
    opts = opts || {};
    if (!document.body) return function () {};
    var kind = type || 'info';
    var host = ensureToastHost();
    var el = document.createElement('div');
    el.className = 'ip-toast ip-toast--' + kind;

    var icon = document.createElement('span');
    icon.className = 'ip-toast__icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" ' +
      'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      (TOAST_ICONS[kind] || TOAST_ICONS.info) + '</svg>';
    el.appendChild(icon);

    var text = document.createElement('span');
    text.className = 'ip-toast__text';
    text.textContent = message;
    el.appendChild(text);

    if (opts.action && opts.onAction) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'ip-toast__action';
      btn.textContent = opts.action;
      btn.addEventListener('click', function () { opts.onAction(); dismiss(); });
      el.appendChild(btn);
    }

    host.appendChild(el);
    requestAnimationFrame(function () { el.classList.add('is-in'); });

    var timer = setTimeout(dismiss, opts.duration || TOAST_MS);
    function dismiss() {
      clearTimeout(timer);
      el.classList.remove('is-in');
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 220);
    }
    return dismiss;
  };

  /* ── Boot ─────────────────────────────────────────────────────────── */

  if (document.readyState !== 'loading') queue.flush();
  else document.addEventListener('DOMContentLoaded', function () { queue.flush(); });

})(window, document);
