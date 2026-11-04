import { initializeApp } from "https://www.gstatic.com/firebasejs/11.0.2/firebase-app.js";
import {
  getFirestore, collection, query, orderBy, limit, onSnapshot, doc, setDoc, serverTimestamp,
} from "https://www.gstatic.com/firebasejs/11.0.2/firebase-firestore.js";
import {
  getMessaging, getToken, isSupported, onMessage,
} from "https://www.gstatic.com/firebasejs/11.0.2/firebase-messaging.js";
import { firebaseConfig, vapidKey } from "./firebase-config.js";

const app = initializeApp(firebaseConfig);
const db = getFirestore(app);

const EVENT_LABELS = {
  new_product: "Nuevo",
  restock: "Restock",
  variant_restock: "Vuelve talla",
  sold_out: "Agotado",
  variant_sold_out: "Talla agotada",
  price_change: "Precio",
  removed: "Retirado",
};
const TAG_LABELS = {
  vault: "Vault", limited: "Limited", special_finish: "Acabado", collab: "Collab", short_run: "Tirada corta",
  back_in_stock: "Back in stock", bestseller: "Bestseller", sale: "Rebaja",
};
// Etiquetas que cuentan como "exclusivo" en los contadores y la ordenación.
const EXCLUSIVE = new Set(["vault", "limited", "special_finish", "collab"]);
const exclusiveScore = (p) => (p.exclusivity || []).filter((t) => EXCLUSIVE.has(t)).length;

const $ = (id) => document.getElementById(id);
const state = { products: [], events: [] };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
const tagsHtml = (tags = []) => tags.map((t) => `<span class="tag ${esc(t)}">${esc(TAG_LABELS[t] || t)}</span>`).join("");
// Shopify sirve miniaturas con ?width=N; así no descargamos la imagen original.
const thumb = (u) => (safeUrl(u) === "#" ? "#" : `${u}${u.includes("?") ? "&" : "?"}width=400`);
const toDate =(ts) => (ts?.toDate ? ts.toDate() : ts ? new Date(ts) : null);

function timeAgo(date) {
  if (!date) return "";
  const s = Math.round((Date.now() - date.getTime()) / 1000);
  if (s < 60) return "hace un momento";
  if (s < 3600) return `hace ${Math.round(s / 60)} min`;
  if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
  return date.toLocaleDateString("es-ES", { day: "numeric", month: "short" });
}

function renderProducts() {
  const q = $("search").value.trim().toLowerCase();
  const tag = $("filter-tag").value;
  const onlyStock = $("filter-stock").checked;

  const list = state.products
    .filter((p) => !q || p.title.toLowerCase().includes(q))
    .filter((p) => !tag || (p.exclusivity || []).includes(tag))
    .filter((p) => !onlyStock || p.available)
    .sort((a, b) => exclusiveScore(b) - exclusiveScore(a) || Number(b.available) - Number(a.available) || (b.drop || "").localeCompare(a.drop || "", undefined, { numeric: true }));

  $("products").innerHTML = list.length
    ? list.map((p) => `
      <a class="card ${p.available ? "" : "out"}" href="${esc(safeUrl(p.url))}" target="_blank" rel="noopener">
        ${p.image ? `<img src="${esc(thumb(p.image))}" alt="" loading="lazy">` : `<img alt="">`}
        <div class="body">
          <div class="tags">${tagsHtml(p.exclusivity)}</div>
          <div class="title">${esc(p.title)}</div>
          <div class="sizes">${(p.variants || []).map((v) => `<span class="size ${v.available ? "on" : "off"}">${esc(v.title)}</span>`).join("")}</div>
          <div class="meta"><span class="price">${Number(p.min_price || 0).toFixed(2)} €</span><span>${p.drop ? `${esc(p.drop)} · ` : ""}${p.available ? "En stock" : "Agotado"}</span></div>
        </div>
      </a>`).join("")
    : `<p class="empty">${state.products.length ? "Ningún producto coincide con los filtros." : "Aún no hay productos. Aparecerán cuando el monitor haga su primera lectura."}</p>`;
}

function renderEvents() {
  $("events").innerHTML = state.events.length
    ? state.events.map((e) => `
      <li class="event">
        <span class="kind ${esc(e.type)}">${esc(EVENT_LABELS[e.type] || e.type)}</span>
        <a href="${esc(safeUrl(e.url))}" target="_blank" rel="noopener">${esc(e.title)}</a>
        ${e.detail ? `<div class="detail">${esc(e.detail)}</div>` : ""}
        <div class="tags">${tagsHtml(e.exclusivity)}</div>
        <time>${esc(timeAgo(toDate(e.ts)))}</time>
      </li>`).join("")
    : `<li class="empty">Sin actividad todavía.</li>`;
}

function renderStats() {
  $("stat-total").textContent = state.products.length;
  $("stat-available").textContent = state.products.filter((p) => p.available).length;
  $("stat-exclusive").textContent = state.products.filter((p) => exclusiveScore(p) > 0).length;
  $("stat-events").textContent = state.events.length;
}

function setStatus(text, kind) {
  $("status").textContent = text;
  $("live-dot").className = `dot ${kind || ""}`;
}

function onError(err) {
  console.error(err);
  const msg = err.code === "permission-denied"
    ? "Sin permiso de lectura: publica las reglas de firestore.rules en la consola de Firebase."
    : `Error de conexión: ${err.message}`;
  setStatus(msg, "error");
}

onSnapshot(collection(db, "products"), (snap) => {
  state.products = snap.docs.map((d) => d.data());
  const last = state.products.map((p) => toDate(p.updated_at)).filter(Boolean).sort((a, b) => b - a)[0];
  setStatus(last ? `En directo · último cambio ${timeAgo(last)}` : "En directo", "live");
  renderProducts();
  renderStats();
}, onError);

onSnapshot(query(collection(db, "events"), orderBy("ts", "desc"), limit(50)), (snap) => {
  state.events = snap.docs.map((d) => d.data());
  renderEvents();
  renderStats();
}, onError);

for (const id of ["search", "filter-tag", "filter-stock"]) $(id).addEventListener("input", renderProducts);
renderProducts();
renderEvents();

// ---------- Notificaciones push (Firebase Cloud Messaging) ----------

const TOKEN_KEY = "vsx-push-token";
const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
const isStandalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;

function notifyMsg(text, isError = false) {
  const el = $("notify-msg");
  el.hidden = !text;
  el.textContent = text || "";
  el.className = `notice ${isError ? "error" : ""}`;
}

function markEnabled() {
  const btn = $("notify-btn");
  btn.textContent = "🔔 Notificaciones activadas";
  btn.classList.add("on");
}

function readToken() {
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
}

function saveToken(token) {
  try { localStorage.setItem(TOKEN_KEY, token); } catch { /* sin almacenamiento: no pasa nada */ }
}

async function enableNotifications() {
  const btn = $("notify-btn");
  if (isIOS && !isStandalone) {
    notifyMsg("En iPhone: pulsa Compartir → \"Añadir a pantalla de inicio\", abre la web desde ese icono y vuelve a pulsar el botón.");
    return;
  }
  if (!vapidKey) {
    notifyMsg("Falta configurar la clave VAPID en firebase-config.js.", true);
    return;
  }
  if (!(await isSupported())) {
    notifyMsg("Este navegador no admite notificaciones push. Prueba con Chrome.", true);
    return;
  }
  btn.disabled = true;
  try {
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      notifyMsg("Has bloqueado las notificaciones. Actívalas en los ajustes del sitio del navegador y vuelve a intentarlo.", true);
      return;
    }
    const registration = await navigator.serviceWorker.register("firebase-messaging-sw.js");
    await navigator.serviceWorker.ready;
    const token = await getToken(getMessaging(app), { vapidKey, serviceWorkerRegistration: registration });
    await setDoc(doc(db, "subscribers", token), { created_at: serverTimestamp(), ua: navigator.userAgent.slice(0, 250) });
    saveToken(token);
    markEnabled();
    notifyMsg("¡Listo! Te avisaremos de restocks y productos exclusivos aunque tengas la web cerrada.");
  } catch (err) {
    console.error(err);
    notifyMsg(`No se pudieron activar: ${err.message}`, true);
  } finally {
    btn.disabled = false;
  }
}

$("notify-btn").addEventListener("click", enableNotifications);

if (readToken() && "Notification" in window && Notification.permission === "granted") {
  markEnabled();
  // Con la web abierta, FCM no muestra la notificación: la mostramos nosotros.
  isSupported().then((ok) => ok && onMessage(getMessaging(app), async ({ notification, fcmOptions }) => {
    const reg = await navigator.serviceWorker.getRegistration();
    reg?.showNotification(notification?.title || "VaultShopXclusive", {
      body: notification?.body, icon: "icon-192.png", image: notification?.image, data: { url: fcmOptions?.link },
    });
  }));
}
