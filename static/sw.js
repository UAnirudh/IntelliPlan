const CACHE_NAME = 'intelliplan-v4';
const STATIC_ASSETS = [
  '/',
  '/dashboard',
  '/scheduler',
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/css/command_center.css',
  '/static/js/command_center.js',
];

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
.wrap{max-width:420px}
h1{font-size:1.6rem;font-weight:700;margin-bottom:.75rem;letter-spacing:-.02em}
p{font-size:.95rem;line-height:1.6;opacity:.7;margin-bottom:1.5rem}
button{background:#fff;color:#0a0a0a;border:none;padding:.7rem 1.6rem;
border-radius:8px;font-size:.9rem;font-weight:600;cursor:pointer}
button:hover{opacity:.85}
.icon{font-size:3rem;margin-bottom:1rem}
</style>
</head>
<body>
<div class="wrap">
<div class="icon"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M1 1l22 22"/><path d="M16.72 11.06A10.94 10.94 0 0 1 19 12.55"/><path d="M5 12.55a10.94 10.94 0 0 1 5.17-2.39"/><path d="M10.71 5.05A16 16 0 0 1 22.56 9"/><path d="M1.42 9a15.91 15.91 0 0 1 4.7-2.88"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/></svg></div>
<h1>You're offline</h1>
<p>IntelliPlan needs an internet connection for this page. Check your connection and try again.</p>
<button onclick="location.reload()">Retry</button>
</div>
</body>
</html>`;

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(async cache => {
      await cache.addAll(STATIC_ASSETS);
      await cache.put(new Request(OFFLINE_PAGE), new Response(OFFLINE_HTML, {
        headers: { 'Content-Type': 'text/html; charset=utf-8' }
      }));
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  if (request.method !== 'GET') return;

  if (url.pathname.startsWith('/live') ||
      url.pathname.includes('/tasks/unified') ||
      url.pathname.includes('/generate_schedule')) {
    return;
  }

  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then(cached =>
        cached || fetch(request).then(resp => {
          const clone = resp.clone();
          caches.open(CACHE_NAME).then(c => c.put(request, clone));
          return resp;
        }).catch(() => caches.match(request))
      )
    );
    return;
  }

  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(request).then(resp => {
        const clone = resp.clone();
        caches.open(CACHE_NAME).then(c => c.put(request, clone));
        return resp;
      }).catch(() =>
        caches.match(request).then(cached =>
          cached || new Response(JSON.stringify({ error: 'offline' }), {
            status: 503,
            headers: { 'Content-Type': 'application/json' }
          })
        )
      )
    );
    return;
  }

  event.respondWith(
    fetch(request).then(resp => {
      if (resp.ok || resp.type === 'opaqueredirect') {
        const clone = resp.clone();
        caches.open(CACHE_NAME).then(c => c.put(request, clone));
      }
      return resp;
    }).catch(() =>
      caches.match(request).then(cached => cached || caches.match(OFFLINE_PAGE))
    )
  );
});

// ── PUSH NOTIFICATIONS ────────────────────────────────────────
self.addEventListener('push', event => {
  let data = { title: 'IntelliPlan', body: 'You have a reminder' };
  if (event.data) {
    try { data = JSON.parse(event.data.text()); } catch(e) {}
  }
  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: '/static/icons/icon-192.png',
      badge: '/static/icons/icon-192.png',
      vibrate: [200, 100, 200],
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
  event.waitUntil(
    clients.matchAll({ type: 'window' }).then(windowClients => {
      const url = event.notification.data?.url || '/dashboard';
      for (const client of windowClients) {
        if (client.url === url && 'focus' in client) return client.focus();
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
