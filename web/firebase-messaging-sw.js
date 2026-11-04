// Service worker de Firebase Cloud Messaging: recibe las notificaciones
// aunque la web esté cerrada y abre el producto al pulsarlas.
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

// Notificaciones que muestra la propia web cuando está abierta (ver app.js).
self.addEventListener("notificationclick", (event) => {
  const url = event.notification.data?.url;
  if (!url) return;
  event.notification.close();
  event.waitUntil(clients.openWindow(url));
});
