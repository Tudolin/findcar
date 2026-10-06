// carwatch service worker: makes the app installable and keeps static assets fast.
// Pages and data always come from the network (prices must never be stale); if the
// server is unreachable, a small offline page is shown instead.
const VERSION = "cw-v1";
const STATIC = ["/static/css/app.css", "/static/js/app.js", "/static/vendor/htmx.min.js",
  "/static/vendor/alpine.min.js", "/static/vendor/alpine-collapse.min.js", "/static/vendor/chart.umd.min.js", "/static/vendor/sortable.min.js",
  "/static/vendor/chartjs-adapter-date-fns.min.js", "/static/icons/icon-192.png", "/offline"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(STATIC)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/static/")) {
    // stale-while-revalidate for our own static files
    e.respondWith(caches.open(VERSION).then(async (c) => {
      const hit = await c.match(req);
      const net = fetch(req).then((r) => { if (r.ok) c.put(req, r.clone()); return r; }).catch(() => hit);
      return hit || net;
    }));
    return;
  }
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).catch(() => caches.match("/offline")));
  }
});
