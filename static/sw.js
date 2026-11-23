// Minimal service worker: makes Broker Buddy installable ("Add to Home Screen").
// It always goes to the network, so brokers never see stale deal data.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (e) => {
  if (e.request.mode === "navigate") {
    e.respondWith(fetch(e.request).catch(() =>
      new Response("<h1 style='font-family:system-ui;padding:24px'>You're offline</h1><p style='font-family:system-ui;padding:0 24px'>Broker Buddy needs a connection. Reconnect and try again.</p>",
        { headers: { "Content-Type": "text/html" } })));
  }
});
