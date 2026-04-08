const CACHE_NAME = 'intelliplan-v1';
const STATIC_ASSETS = [
  '/',
  '/schedule',
  '/priority',
  '/classes',
  '/grades',
  '/grademodel',
  '/scheduler',
  '/static/manifest.json',
];

// Install: cache static assets
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

// Activate: clean up old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Fetch: network first, fall back to cache
self.addEventListener('fetch', event => {
  // Don't cache API calls
  if (event.request.url.includes('/live') ||
      event.request.url.includes('/grades/data') ||
      event.request.url.includes('/gradebook') ||
      event.request.url.includes('/generate_schedule') ||
      event.request.url.includes('/assignment/description') ||
      event.request.method !== 'GET') {
    event.respondWith(fetch(event.request));
    return;
  }

  event.respondWith(
    fetch(event.request)
      .then(response => {
        const clone = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});