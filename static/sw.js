/* ==========================================================================
   sw.js — IntelliPlan service worker
   --------------------------------------------------------------------------
   Three jobs, in order of how much a student notices them:

     1. Keep the app openable offline. Navigations fall back to the last
        good render of that page, and only to the generic offline card when
        the student has genuinely never loaded it.
     2. Keep static assets instant, without pinning a stale bundle.
     3. Deliver push notifications and route the click to the right page.

   Updating
   --------
   Any byte changed in this file is a new worker as far as the browser is
   concerned, which is what makes a deploy visible to an open tab. The new
   worker installs and then waits; base.html notices, offers the student a
   reload, and only promotes it when they accept. Nothing here should call
   skipWaiting() outside that handshake — doing so is what produced new
   assets under an old page.

   What it deliberately does not do
   --------------------------------
   It does not cache authenticated API responses. IP.offline (ip-offline.js)
   owns cached reads in IndexedDB, where the page can label them with their
   age and the student can see what they are looking at. A second, invisible
   cache in here competing with it produced the worst outcome available:
   data of unknown age, presented as current.

   The write queue lives in IndexedDB (ip-queue-core.js, imported below)
   precisely so this file can flush it. An earlier version kept it in
   localStorage, which a worker cannot read, so a Background Sync wake-up
   could only poke an open tab — the one situation where the queue would
   have flushed anyway. See docs/offline-architecture.md.
   ========================================================================== */

// Shared with the page. Same IndexedDB connection, same replay rules, so a
// write parked by the tab and flushed by the worker behaves identically.
importScripts('/static/js/ip-queue-core.js');

const CACHE_NAME = 'intelliplan-v5';
const PAGE_CACHE = 'intelliplan-pages-v1';

// The shell a student is most likely to return to. Anything failing to
// precache must not fail the install — one 302 on /dashboard for a signed-
// out visitor would otherwise leave them with no worker at all.
const STATIC_ASSETS = [
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/css/ip-base.css',
  '/static/css/ip-async.css',
  '/static/css/ip-premium.css',
  '/static/js/ip-queue-core.js',
  '/static/js/ip-core.js',
  '/static/js/ip-async.js',
  '/static/js/ip-offline.js',
];

// Kept small on purpose: a page cache that grows without bound is a quota
// error at the worst moment, and the pages past this are ones nobody
// reopens offline.
const MAX_CACHED_PAGES = 24;

const OFFLINE_PAGE = '/offline';
const OFFLINE_HTML = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Offline | IntelliPlan</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
background:#0a0a0a;color:#e8e8e8;display:flex;align-items:center;justify-content:center;
min-height:100vh;padding:2rem;text-align:center}
.wrap{max-width:440px}
h1{font-size:1.6rem;font-weight:700;margin-bottom:.75rem;letter-spacing:-.02em}
p{font-size:.95rem;line-height:1.6;opacity:.7;margin-bottom:1.25rem}
.hint{font-size:.85rem;opacity:.55;margin-bottom:1.5rem}
button{background:#fff;color:#0a0a0a;border:none;padding:.7rem 1.6rem;
border-radius:8px;font-size:.9rem;font-weight:600;cursor:pointer}
button:hover{opacity:.85}
.icon{margin-bottom:1rem;opacity:.8}
</style>
</head>
<body>
<div class="wrap">
<div class="icon"><svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M1 1l22 22"/><path d="M16.72 11.06A10.94 10.94 0 0 1 19 12.55"/><path d="M5 12.55a10.94 10.94 0 0 1 5.17-2.39"/><path d="M10.71 5.05A16 16 0 0 1 22.56 9"/><path d="M1.42 9a15.91 15.91 0 0 1 4.7-2.88"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/></svg></div>
<h1>You haven't opened this page offline before</h1>
<p>Pages you've already visited stay available without a connection. This one hasn't been loaded yet, so there's nothing saved to show.</p>
<p class="hint">Anything you changed while offline is saved on this device and will sync once you're back online.</p>
<button onclick="location.reload()">Try again</button>
</div>
</body>
</html>`;

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    // Individually, not addAll: addAll is all-or-nothing, so one renamed
    // asset silently costs the student every other cached file too.
    await Promise.all(STATIC_ASSETS.map(url =>
      cache.add(new Request(url, { cache: 'reload' })).catch(() => {})
    ));
    await cache.put(new Request(OFFLINE_PAGE), new Response(OFFLINE_HTML, {
      headers: { 'Content-Type': 'text/html; charset=utf-8' }
    }));
  })());
  // Deliberately no skipWaiting() here.
  //
  // It used to activate immediately, which sounds like the helpful thing
  // and is not: combined with clients.claim() below, a deploy handed the
  // open tab a new worker serving new assets to a page still running the
  // old HTML and old JS. That mismatch is invisible until something breaks
  // oddly, and it lasts until the student happens to reload.
  //
  // Instead a new worker waits, the page notices and offers to reload, and
  // the swap happens at a moment the student chose. The page asks for it
  // with the message below.
  //
  // The first install is unaffected: with no controller to replace there is
  // nothing to wait behind, so it activates straight away.
});

self.addEventListener('message', event => {
  // Sent by the "Update" action in the page's reload prompt. This is the
  // only thing that promotes a waiting worker, so an update can never take
  // effect without the student asking for it.
  if (event.data && event.data.type === 'SKIP_WAITING') self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const keep = new Set([CACHE_NAME, PAGE_CACHE]);
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => !keep.has(k)).map(k => caches.delete(k)));
    await self.clients.claim();
  })());
});

/* ── Helpers ─────────────────────────────────────────────────────── */

async function trimCache(name, max) {
  const cache = await caches.open(name);
  const keys = await cache.keys();
  // Oldest-first: Cache Storage preserves insertion order.
  for (const key of keys.slice(0, Math.max(0, keys.length - max))) {
    await cache.delete(key);
  }
}

/** A response worth keeping: a real 200 from us, not a redirect to /login. */
function isCacheableResponse(response) {
  return response &&
         response.ok &&
         response.status === 200 &&
         response.type !== 'opaqueredirect' &&
         !response.redirected;
}

/* ── Fetch ───────────────────────────────────────────────────────── */

self.addEventListener('fetch', event => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Live surfaces and anything the page caches itself: straight to network,
  // no interception. Getting a stale answer here is worse than an error.
  if (url.pathname.startsWith('/live') ||
      url.pathname.startsWith('/api/') ||
      url.pathname.startsWith('/oauth') ||
      url.pathname.startsWith('/cron/') ||
      url.pathname.includes('/tasks/unified') ||
      url.pathname.includes('/generate_schedule')) {
    return;
  }

  // Static: stale-while-revalidate. Instant from cache, refreshed behind it,
  // and the ?v= fingerprint on every URL means a deploy is a new key rather
  // than a stale hit.
  if (url.pathname.startsWith('/static/')) {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE_NAME);
      const cached = await cache.match(request, { ignoreSearch: false });
      const network = fetch(request).then(resp => {
        if (isCacheableResponse(resp)) cache.put(request, resp.clone());
        return resp;
      }).catch(() => null);
      return cached || (await network) || Response.error();
    })());
    return;
  }

  // Navigations: network first, then this exact page from cache, then the
  // offline card. Caching the rendered page is what makes "open the app in
  // a tunnel and read today's plan" work at all.
  if (request.mode === 'navigate') {
    event.respondWith((async () => {
      try {
        const resp = await fetch(request);
        if (isCacheableResponse(resp)) {
          const cache = await caches.open(PAGE_CACHE);
          cache.put(request, resp.clone());
          trimCache(PAGE_CACHE, MAX_CACHED_PAGES);
        }
        return resp;
      } catch (err) {
        const cached = await caches.match(request, { ignoreSearch: true });
        if (cached) return cached;
        const offline = await caches.match(OFFLINE_PAGE);
        return offline || new Response(OFFLINE_HTML, {
          status: 503,
          headers: { 'Content-Type': 'text/html; charset=utf-8' }
        });
      }
    })());
  }
});

/* ── Background sync ─────────────────────────────────────────────── */

self.addEventListener('sync', event => {
  if (event.tag !== 'ip-flush-queue') return;
  event.waitUntil((async () => {
    // Flush here, with no tab required. This is the whole point of moving
    // the queue into IndexedDB: a student who completes a task on the train
    // and closes the app gets it synced when the signal returns, rather
    // than the next time they happen to open IntelliPlan.
    // Tell any open tab what happened so its pending count and connection
    // pill are not left describing a queue that has already drained.
    await announceFlush(await self.IPQueueCore.flush());
  })());
});

/** Broadcast a flush result to every open tab. */
async function announceFlush(result) {
  if (!result.sent && !result.failed) return;
  const clientList = await self.clients.matchAll({
    type: 'window', includeUncontrolled: true
  });
  for (const client of clientList) {
    client.postMessage({ type: 'ip-queue-flushed', result });
  }
}

self.addEventListener('message', event => {
  if (!event.data || event.data.type !== 'ip-flush-now') return;
  // A page can hand the flush to the worker rather than running it itself.
  // Worth having: the worker outlives the tab, so a student who marks a
  // task done and immediately closes IntelliPlan still gets it delivered,
  // where an in-page fetch would be cancelled mid-flight.
  event.waitUntil(self.IPQueueCore.flush().then(announceFlush));
});

/* ── Push notifications ──────────────────────────────────────────── */

self.addEventListener('push', event => {
  let data = { title: 'IntelliPlan', body: 'You have a reminder' };
  if (event.data) {
    try { data = JSON.parse(event.data.text()); } catch (e) {}
  }
  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: '/static/icons/icon-192.png',
      badge: '/static/icons/icon-192.png',
      vibrate: [200, 100, 200],
      // Same tag replaces rather than stacks, so a phone left unopened for
      // a day shows the current reminder instead of a column of stale ones.
      tag: data.tag || 'intelliplan',
      renotify: !!data.renotify,
      data: { url: data.url || '/dashboard' },
      actions: [
        { action: 'open', title: 'Open IntelliPlan' },
        { action: 'dismiss', title: 'Dismiss' }
      ]
    })
  );
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  if (event.action === 'dismiss') return;

  const target = event.notification.data?.url || '/dashboard';
  event.waitUntil((async () => {
    const clientList = await self.clients.matchAll({
      type: 'window', includeUncontrolled: true
    });
    // Focus an existing tab and navigate it. The previous version compared
    // full URLs and so only ever matched a tab already sitting on exactly
    // the notification's destination — every other case opened a duplicate
    // window on top of the app the student already had open.
    for (const client of clientList) {
      if ('focus' in client) {
        await client.focus();
        if ('navigate' in client) {
          try { await client.navigate(target); } catch (e) {}
        }
        return;
      }
    }
    if (self.clients.openWindow) await self.clients.openWindow(target);
  })());
});
