// codex-fleet runs as a local development control center. Disable stale
// Workbox caching so rebuilt local assets are visible immediately.
self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const cacheNames = await caches.keys();
      await Promise.all(cacheNames.map((cacheName) => caches.delete(cacheName)));
      await self.registration.unregister();
      await self.clients.claim();
    })()
  );
});
