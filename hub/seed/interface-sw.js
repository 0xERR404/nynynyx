// Минимальный service worker — нужен ТОЛЬКО чтобы Android/Chrome посчитал
// сайт устанавливаемым PWA. Стратегия — network-first: страница живая
// (чат, модули), кэш используется лишь как запасной вариант при обрыве сети,
// не как основной источник данных.
const CACHE_NAME = 'nexus404-shell-v1';
const APP_SHELL = ['/', '/style.css', '/app.js', '/manifest.webmanifest', '/icon-192.png', '/icon-512.png'];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)).catch(() => {})
    );
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((names) =>
            Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)))
        )
    );
    self.clients.claim();
});

self.addEventListener('fetch', (event) => {
    if (event.request.method !== 'GET') return;  // не кэшируем чат/API-мутации

    event.respondWith(
        fetch(event.request)
            .then((response) => {
                const copy = response.clone();
                caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy)).catch(() => {});
                return response;
            })
            .catch(() => caches.match(event.request))
    );
});
