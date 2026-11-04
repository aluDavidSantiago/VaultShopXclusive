// Configuración pública de la app web de Firebase. No es secreta: la
// seguridad de los datos la dan las reglas de Firestore (firestore.rules).
export const firebaseConfig = {
  apiKey: "AIzaSyCXoywiQdspN1MJQBmZqQtBr6Axwu2IElM",
  authDomain: "vaultshopxclusive.firebaseapp.com",
  projectId: "vaultshopxclusive",
  storageBucket: "vaultshopxclusive.firebasestorage.app",
  messagingSenderId: "214096197908",
  appId: "1:214096197908:web:164dc6a32ba698e302ea01",
};

// Clave pública VAPID para notificaciones push (Configuración del proyecto →
// Cloud Messaging → Certificados push web). También es pública.
export const vapidKey = "BDlqURPudNsjUgWtH86wvSlTclkOJTmS3BGOgzjouNwALzKbEunCBkODHvMC14ygd_Ytbf0odU0qkaxmHQq2pYE";
