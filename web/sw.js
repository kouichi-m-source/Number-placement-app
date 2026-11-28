// 番号配置アプリ(Web版) オフライン用の Service Worker
// アプリ本体はネットワーク優先(つながるときは常に最新)、PDF表示用の部品(lib/)は一度取得したら端末に保存して使う。
// アプリを更新したときは、VERSION を変えると古い保存が捨てられる。
const VERSION = "v3";
const CACHE = "npa-" + VERSION;
const SHELL = [
  "./", "index.html", "style.css", "app.js", "core.js", "render.js", "io.js", "manifest.webmanifest",
  "icons/icon-192.png", "icons/icon-512.png", "icons/apple-touch-icon.png",
  "lib/pdfjs/pdf.min.mjs", "lib/pdfjs/pdf.worker.min.mjs",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k.startsWith("npa-") && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  const isLib = url.pathname.includes("/lib/");
  e.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const cached = await cache.match(req, { ignoreSearch: true });
    if (isLib && cached) return cached;
    try {
      const res = await fetch(req);
      if (res.ok) cache.put(req, res.clone());
      return res;
    } catch (err) {
      if (cached) return cached;
      if (req.mode === "navigate") { const i = await cache.match("index.html"); if (i) return i; }
      throw err;
    }
  })());
});
