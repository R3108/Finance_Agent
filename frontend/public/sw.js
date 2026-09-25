/*
 * Ledgerly service worker — deliberately minimal.
 *
 * Financial data must never be served stale, so nothing under /api is ever cached and pages are
 * always fetched from the network. The only thing stored is a small offline notice, shown when a
 * page navigation fails because there's no connection. This is what makes the app installable
 * without the risk of someone seeing yesterday's balance and thinking it's today's.
 */
const CACHE = "ledgerly-offline-v1";
const OFFLINE = "/offline.html";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.add(OFFLINE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.mode !== "navigate") return;            // API calls, scripts, images: straight to the network
  event.respondWith(fetch(req).catch(() => caches.match(OFFLINE)));
});
