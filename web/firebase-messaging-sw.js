// Service worker de la app:
//  - recibe las notificaciones de Firebase Cloud Messaging aunque la web esté cerrada;
//  - pide siempre los archivos propios revalidando con el servidor, para que la
//    app instalada en el iPhone no se quede con una versión vieja en caché.
// Versión: __BUILD__ (la sustituye el workflow de despliegue; un cambio aquí hace que el navegador actualice el worker).
importScripts("https://www.gstatic.com/firebasejs/11.0.2/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/11.0.2/firebase-messaging-compat.js");

firebase.initializeApp({
  apiKey: "AIzaSyCXoywiQdspN1MJQBmZqQtBr6Axwu2IElM",
  authDomain: "vaultshopxclusive.firebaseapp.com",
  projectId: "vaultshopxclusive",
  storageBucket: "vaultshopxclusive.firebasestorage.app",
  messagingSenderId: "214096197908",
  appId: "1:214096197908:web:164dc6a32ba698e302ea01",
});

// Con mensajes de tipo "notification", el SDK ya muestra la notificación
// y abre el enlace (fcm_options.link) al pulsarla.
firebase.messaging();

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

// Archivos de la propia web: siempre del servidor (con ETag es barato); sin red, lo que haya en caché.
self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET" || new URL(req.url).origin !== self.location.origin) return;
  event.respondWith(fetch(req, { cache: "no-cache" }).catch(() => caches.match(req)));
});

// Notificaciones que muestra la propia web cuando está abierta (ver app.js).
self.addEventListener("notificationclick", (event) => {
  const url = event.notification.data?.url;
  if (!url) return;
  event.notification.close();
  event.waitUntil(clients.openWindow(url));
});
