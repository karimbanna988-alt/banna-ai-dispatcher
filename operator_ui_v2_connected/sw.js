/* Banna Dispatch -- application-shell service worker.

   SAFETY CONTRACT, read this before changing anything here:

   1. This worker caches the APPLICATION SHELL ONLY -- html, css, js,
      manifest, icons. It must NEVER cache a response from /api/.
      Job truth, technician state, closings and confidence values change
      minute to minute; serving a cached one would show an operator old
      work as if it were live. That is the single worst failure this
      product can have, so the /api/ bail-out is the first thing in
      fetch().

   2. There is NO background sync and NO offline write queue. A dispatch,
      a closing or a company update must never be "queued for later" --
      the human sends messages by hand, and a queued business action
      could fire against a job whose state has since moved on.

   3. Offline is READ-ONLY and visibly stale. The shell may load from
      cache with no network, but the app's own API calls will fail and
      console.js raises the OFFLINE banner. That is intended: the
      operator sees the shell and is told plainly it is not live. */

/* Bump SHELL_VERSION and the ?v= query in index.html together. The URLs
   below must match what index.html actually requests, query string
   included, or the cache-first branch silently misses every time. */
const SHELL_VERSION = "banna-shell-v7";

const SHELL_ASSETS = [
  "/index.html",
  "/css/console.css?v=7",
  "/js/console.js?v=7",
  "/js/api.js?v=7",
  "/manifest.webmanifest",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_VERSION)
      // Individually, so one missing optional asset cannot fail the
      // whole install and leave the app with no shell at all.
      .then((cache) => Promise.all(
        SHELL_ASSETS.map((url) => cache.add(url).catch(() => null))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== SHELL_VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // --- Rule 1. Never touch job truth. ---------------------------------
  // Not cached, not read from cache, not even inspected. Let it go
  // straight to the network and fail honestly when offline.
  if (url.pathname.startsWith("/api/")) return;

  // Only ever serve our own origin, and only GET.
  if (req.method !== "GET" || url.origin !== self.location.origin) return;

  // Navigation: network first, so a deployed update is picked up as soon
  // as the server is reachable. Falls back to the cached shell, which
  // contains no data -- the app then reports OFFLINE for itself.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() => caches.match("/index.html").then(
        (hit) => hit || Response.error()))
    );
    return;
  }

  // Static shell assets: cache first (they are versioned with the
  // worker), revalidating in the background.
  event.respondWith(
    caches.match(req).then((hit) => {
      const network = fetch(req).then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(SHELL_VERSION).then((c) => c.put(req, copy));
        }
        return res;
      }).catch(() => hit || Response.error());
      return hit || network;
    })
  );
});
