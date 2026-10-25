// Service worker: app shell + last-known prices for offline use in stores with bad signal.
const SHELL_CACHE = "rp-shell-v2";
const DATA_CACHE  = "rp-data-v2";
const SHELL = ["/", "/static/manifest.json", "/static/icon-192.png"];
const MAX_DATA_ENTRIES = 300;

self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL_CACHE).then(c => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== SHELL_CACHE && k !== DATA_CACHE).map(k => caches.delete(k)))
  ));
  self.clients.claim();
});

async function trim(cache) {
  const keys = await cache.keys();
  for (let i = 0; i < keys.length - MAX_DATA_ENTRIES; i++) await cache.delete(keys[i]);
}

function offlineJson() {
  return new Response(JSON.stringify({ error: "offline" }), { status: 503, headers: { "Content-Type": "application/json" } });
}

// Network first, fall back to the cached copy (marked so the page can say it's offline)
async function networkFirstData(req) {
  const cache = await caches.open(DATA_CACHE);
  try {
    const res = await fetch(req);
    if (res.ok) { await cache.put(req, res.clone()); trim(cache); }
    return res;
  } catch {
    const hit = await cache.match(req);
    if (!hit) return offlineJson();
    const headers = new Headers(hit.headers);
    headers.set("X-From-Cache", "1");
    return new Response(await hit.blob(), { status: 200, headers });
  }
}

self.addEventListener("fetch", e => {
  const req = e.request;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (req.method === "GET" && (url.pathname === "/search" || url.pathname === "/prices")) {
    e.respondWith(networkFirstData(req));
    return;
  }
  if (req.method !== "GET" || url.pathname.startsWith("/api/") || url.pathname.startsWith("/scan")) {
    e.respondWith(fetch(req).catch(offlineJson));
    return;
  }
  if (req.mode === "navigate") {
    // Fresh page when online, cached shell when not
    e.respondWith(fetch(req).then(res => {
      if (res.ok && url.pathname === "/") { const copy = res.clone(); caches.open(SHELL_CACHE).then(c => c.put("/", copy)); }
      return res;
    }).catch(() => caches.match("/")));
    return;
  }
  e.respondWith(caches.match(req).then(hit => hit || fetch(req)));
});
