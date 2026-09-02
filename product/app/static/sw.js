/* Service worker.
 *
 * The one decision in here that matters: **no page is ever cached.**
 *
 * This app shows one person's job search - who they have written to, what
 * they earn, their CV. A service worker that cached HTML would serve one
 * user's dashboard to the next person to sign in on a shared phone or a
 * family computer, and it would keep doing it after they signed out. That is
 * a far worse bug than being offline.
 *
 * So: static assets are cached, because a stylesheet belongs to nobody.
 * Everything else goes to the network every time. Offline shows an honest
 * message rather than a stale, possibly-someone-else's screen.
 */

// Bump this on any change to a file in SHELL. The fetch handler below is
// cache-first for /static/, and activate only clears caches whose key is not
// this one - so a stylesheet that changes without a bump here is never seen
// again by anyone who has already loaded the app. The white-and-black redraw
// was invisible to every returning visitor until this went to v2.
const VERSION = "jm-v2";
const SHELL = [
  "/static/style.css",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/manifest.webmanifest",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;

  // Anything that changes state goes straight to the network. A cached POST
  // is not a thing worth inventing.
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Static only. Note what is NOT here: documents, JSON, anything under a
  // path that needs a session.
  const isStatic = url.pathname.startsWith("/static/");
  if (!isStatic) {
    event.respondWith(
      fetch(req).catch(() =>
        new Response(
          "<!doctype html><meta charset=utf-8>" +
          "<meta name=viewport content='width=device-width,initial-scale=1'>" +
          "<style>body{font:16px/1.6 system-ui;margin:0;display:grid;" +
          "place-items:center;height:100vh;background:#faf9f5;color:#141413;" +
          "text-align:center;padding:2rem}</style>" +
          "<div><h1>No connection</h1><p>Job Machine needs to be online. " +
          "Your letters are safe on the server &mdash; nothing is lost.</p></div>",
          { headers: { "Content-Type": "text/html; charset=utf-8" },
            status: 503 })
      )
    );
    return;
  }

  event.respondWith(
    caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      // Only cache a genuinely good response; a 404 cached forever is a bug
      // that outlives the deploy that caused it.
      if (res && res.status === 200 && res.type === "basic") {
        const copy = res.clone();
        caches.open(VERSION).then((c) => c.put(req, copy));
      }
      return res;
    }))
  );
});
