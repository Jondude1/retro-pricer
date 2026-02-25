// Service Worker — caches the app shell for offline use
const CACHE = "retro-pricer-v1";
const SHELL = ["/", "/static/manifest.json"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ));
  self.clients.claim();
});

self.addEventListener("fetch", e => {
  // Network-first for API calls, cache-first for static assets
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/search") ||
      url.pathname.startsWith("/prices") ||
      url.pathname.startsWith("/deal") ||
      url.pathname.startsWith("/scan")) {
    // Always network for data endpoints
    e.respondWith(fetch(e.request).catch(() =>
      new Response(JSON.stringify({error: "offline"}), {
        headers: {"Content-Type": "application/json"}
      })
    ));
  } else {
    // Cache-first for app shell
    e.respondWith(
      caches.match(e.request).then(cached => cached || fetch(e.request))
    );
  }
});
