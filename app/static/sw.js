// Cache-first for static assets, network-first for pages (last known balance works offline).
// Never caches POSTs or HTMX partials, and drops all cached pages on logout so a shared tablet
// can't show one kid's balance to the next.
const ASSETS = "kb-assets-v5";
const PAGES = "kb-pages-v1";

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(PAGES).then((c) => c.add("/offline")).then(() => self.skipWaiting()));
});

// Drop caches from older versions: caches.match() searches all of them, so a stale v1 would keep winning.
self.addEventListener("activate", (e) =>
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== ASSETS && k !== PAGES).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  ),
);

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  if (url.pathname === "/logout") {
    e.waitUntil(caches.delete(PAGES).then(() => caches.open(PAGES)).then((c) => c.add("/offline")));
    return; // the POST itself goes to the network untouched
  }
  if (req.method !== "GET") return;

  if (url.pathname.startsWith("/static/")) {
    e.respondWith(
      caches.match(req).then(
        (hit) =>
          hit ||
          fetch(req).then((res) => {
            if (res.ok) {
              const copy = res.clone();
              caches.open(ASSETS).then((c) => c.put(req, copy));
            }
            return res;
          }),
      ),
    );
    return;
  }

  if (req.mode === "navigate") {
    e.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(PAGES).then((c) => c.put(req, copy));
          }
          return res;
        })
        .catch(() => caches.match(req).then((hit) => hit || caches.match("/offline"))),
    );
  }
});
