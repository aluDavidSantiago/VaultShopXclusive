import { initializeApp } from "https://www.gstatic.com/firebasejs/11.0.2/firebase-app.js";
import {
  getFirestore, collection, query, orderBy, limit, onSnapshot, doc, setDoc, deleteDoc, serverTimestamp,
} from "https://www.gstatic.com/firebasejs/11.0.2/firebase-firestore.js";
import { getAuth, signInAnonymously, onAuthStateChanged } from "https://www.gstatic.com/firebasejs/11.0.2/firebase-auth.js";
import {
  getMessaging, getToken, isSupported, onMessage,
} from "https://www.gstatic.com/firebasejs/11.0.2/firebase-messaging.js";
import { firebaseConfig, vapidKey } from "./firebase-config.js?v=__BUILD__";

const app = initializeApp(firebaseConfig);
const db = getFirestore(app);
const auth = getAuth(app);

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
const state = { products: [], events: [], uid: null, ratings: {}, ratingDocs: {}, tag: "", loaded: false };

// ---------- Sin zoom (pinza, doble toque, Ctrl+rueda, Ctrl +/-) ----------
for (const type of ["gesturestart", "gesturechange", "gestureend"]) {
  document.addEventListener(type, (e) => e.preventDefault(), { passive: false });
}
document.addEventListener("touchmove", (e) => { if (e.touches.length > 1) e.preventDefault(); }, { passive: false });
document.addEventListener("dblclick", (e) => e.preventDefault(), { passive: false });
addEventListener("wheel", (e) => { if (e.ctrlKey || e.metaKey) e.preventDefault(); }, { passive: false });
addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && ["+", "-", "=", "0"].includes(e.key)) e.preventDefault();
});

// ---------- Pequeñas animaciones ----------
function countUp(el, to) {
  const from = Number(el.dataset.v || 0);
  el.dataset.v = to;
  if (from === to) { el.textContent = to; return; }
  const start = performance.now();
  const dur = 900;
  const step = (now) => {
    const t = Math.min(1, (now - start) / dur);
    el.textContent = Math.round(from + (to - from) * (1 - (1 - t) ** 3));
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

let toastTimer;
function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2200);
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
const tagsHtml = (tags = []) => tags.map((t) => `<span class="tag ${esc(t)}">${esc(TAG_LABELS[t] || t)}</span>`).join("");
// Shopify sirve miniaturas con ?width=N; así no descargamos la imagen original.
const thumb = (u) => (safeUrl(u) === "#" ? "#" : `${u}${u.includes("?") ? "&" : "?"}width=400`);
const toDate = (ts) => (ts?.toDate ? ts.toDate() : ts ? new Date(ts) : null);

function timeAgo(date) {
  if (!date) return "";
  const s = Math.round((Date.now() - date.getTime()) / 1000);
  if (s < 60) return "hace un momento";
  if (s < 3600) return `hace ${Math.round(s / 60)} min`;
  if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
  return date.toLocaleDateString("es-ES", { day: "numeric", month: "short" });
}

// ---------- Actualización automática (sin reinstalar la app) ----------
// El despliegue sustituye __BUILD__ por la versión y publica version.json.
const BUILD = document.querySelector('meta[name="build"]')?.content || "__BUILD__";

async function checkForUpdate() {
  if (BUILD.startsWith("__")) return; // en local no hay versión
  try {
    const res = await fetch(`version.json?t=${Date.now()}`, { cache: "no-store" });
    const { version } = await res.json();
    if (!version || version === BUILD) {
      sessionStorage.removeItem("vsx-reload");
      return;
    }
    // Evita bucles si la caché aún sirve la versión vieja.
    const tries = Number(sessionStorage.getItem("vsx-reload") || 0);
    if (tries >= 2) return;
    sessionStorage.setItem("vsx-reload", String(tries + 1));
    toast("✨ Nueva versión, actualizando…");
    const reg = await navigator.serviceWorker?.getRegistration();
    await reg?.update().catch(() => {});
    setTimeout(() => location.reload(), 700);
  } catch { /* sin red: ya se comprobará luego */ }
}
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("firebase-messaging-sw.js").catch((err) => console.error("sw", err));
}
checkForUpdate();
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") checkForUpdate(); });

// ---------- Scroll: portada, sección anclada y revelados ----------
const motion = !!window.gsap && !!window.ScrollTrigger && !matchMedia("(prefers-reduced-motion: reduce)").matches;
if (motion) {
  document.documentElement.classList.add("anim");
  gsap.registerPlugin(ScrollTrigger);
  ScrollTrigger.config({ ignoreMobileResize: true });
}
let refreshTimer;
function refreshScroll() {
  if (!motion) return;
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => ScrollTrigger.refresh(), 120);
}

// Barra de progreso
(function progressBar() {
  const bar = $("progress");
  let ticking = false;
  addEventListener("scroll", () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      const max = document.documentElement.scrollHeight - innerHeight;
      bar.style.transform = `scaleX(${max > 0 ? scrollY / max : 0})`;
      ticking = false;
    });
  }, { passive: true });
})();

// Revelado de secciones y tarjetas al entrar en pantalla
const revealer = "IntersectionObserver" in window ? new IntersectionObserver((entries) => {
  for (const e of entries) {
    if (!e.isIntersecting) continue;
    e.target.classList.add("in");
    e.target.classList.remove("pre");
    revealer.unobserve(e.target);
  }
}, { rootMargin: "0px 0px -8% 0px", threshold: 0 }) : null; // 0: el catálogo es muy alto y nunca llegaría a un % visible

function observeCards() {
  if (!revealer) return;
  document.querySelectorAll("#products .card.pre").forEach((c) => revealer.observe(c));
  refreshScroll();
}
if (motion && revealer) document.querySelectorAll(".reveal").forEach((el) => revealer.observe(el));

// Portada: título letra a letra al entrar; al hacer scroll el collage se endereza y el texto se va.
// Cada letra va en dos capas: .rise (entrada al cargar) y .char (salida con el scroll).
// Así las dos animaciones no se pisan.
function splitChars(el) {
  for (const line of el.querySelectorAll(".line")) {
    line.innerHTML = [...line.textContent].map((c) =>
      `<span class="rise"><span class="char">${c === " " ? "&nbsp;" : esc(c)}</span></span>`).join("");
  }
}
if (motion) {
  splitChars($("hero-title"));
  gsap.from("#hero-title .rise", { yPercent: 110, opacity: 0, duration: 1.1, ease: "power4.out", stagger: 0.035, delay: 0.15 });
  gsap.from(["#hero-kicker", "#hero-sub", "#hero-scroll"], { y: 24, opacity: 0, duration: 1, ease: "power3.out", stagger: 0.12, delay: 0.6 });
  gsap.from("#stats .stat", {
    y: 50, opacity: 0, duration: 0.9, ease: "power3.out", stagger: 0.1,
    scrollTrigger: { trigger: "#stats", start: "top 88%" },
  });

  const tl = gsap.timeline({
    scrollTrigger: { trigger: "#hero", start: "top top", end: "+=110%", scrub: 0.8, pin: ".hero-stage", anticipatePin: 1 },
  });
  // Kicker, subtítulo y "Desliza" van dentro de .hero-fade (la entrada anima los hijos, el scroll el contenedor).
  tl.to("#hero-collage", { rotate: 0, scale: 0.82, ease: "none" }, 0)
    .to("#hero-title .line:first-child .char", { yPercent: -120, opacity: 0, stagger: 0.02, ease: "power2.in" }, 0.05)
    .to("#hero-title .line:last-child .char", { yPercent: 120, opacity: 0, stagger: 0.02, ease: "power2.in" }, 0.05)
    .to(".hero-fade", { opacity: 0, y: -30, ease: "power1.in" }, 0)
    .to(".hero-shade", { opacity: 0.55, ease: "none" }, 0);
}
$("hero-scroll").addEventListener("click", () => $("stats").scrollIntoView({ behavior: "smooth", block: "start" }));

// Collage con fotos del catálogo (exclusivos primero)
let heroFilled = false;
function renderHero() {
  if (heroFilled || !state.products.length) return;
  const withImg = state.products.filter((p) => p.image && p.available);
  const pick = [...withImg.filter((p) => exclusiveScore(p)), ...withImg.filter((p) => !exclusiveScore(p))].slice(0, 16);
  $("hero-collage").innerHTML = pick.map((p) =>
    `<figure><img src="${esc(thumb(p.image))}" alt="" decoding="async"></figure>`).join("");
  heroFilled = true;
  if (motion) {
    gsap.from("#hero-collage figure", { opacity: 0, scale: 0.85, duration: 1.2, ease: "power3.out", stagger: { each: 0.05, from: "center" } });
    // Cada foto se desplaza a su ritmo: efecto de profundidad.
    gsap.utils.toArray("#hero-collage figure").forEach((fig, i) => {
      gsap.to(fig, {
        yPercent: (i % 3 - 1) * 22, ease: "none",
        scrollTrigger: { trigger: "#hero", start: "top top", end: "+=110%", scrub: 1 },
      });
    });
  }
  refreshScroll();
}

// Sección anclada: "Para ti" si ya aprendió tus gustos; si no, exclusivos en stock.
let featuredTween = null;
let featuredKey = "";
function renderFeatured() {
  if (!state.products.length) return;
  const ready = profileReady();
  if (ready) buildModel();
  let items = [];
  if (ready) {
    items = state.products.filter((p) => p.available && p.image && !state.ratings[p.docId])
      .map((p) => ({ p, s: predict(p) })).filter(({ s }) => s >= 3.6).sort((a, b) => b.s - a.s).slice(0, 10);
  }
  const forYou = ready && items.length >= 3;
  if (!forYou) {
    items = state.products.filter((p) => p.available && p.image && exclusiveScore(p)).slice(0, 10).map((p) => ({ p, s: null }));
  }
  const key = (forYou ? "y" : "x") + items.map(({ p }) => p.docId).join();
  if (key === featuredKey) return;
  featuredKey = key;

  $("featured").hidden = items.length < 3;
  if (items.length < 3) { refreshScroll(); return; }
  $("featured-kicker").textContent = forYou ? "Seleccionado para ti" : "Lo más especial ahora";
  $("featured-title").textContent = forYou ? "Para ti" : "Exclusivos";
  $("featured-track").innerHTML = items.map(({ p, s }, i) => `
    <a class="feature-card" href="${esc(safeUrl(p.url))}" target="_blank" rel="noopener">
      <img src="${esc(thumb(p.image))}" alt="" loading="lazy" decoding="async">
      <span class="fc-num">${String(i + 1).padStart(2, "0")}</span>
      ${s !== null ? `<span class="for-you fc-score">✨ ${s.toFixed(1)}★</span>` : ""}
      <div class="fc-body">
        <div class="tags">${tagsHtml(p.exclusivity)}</div>
        <div class="fc-title">${esc(p.title)}</div>
        <div class="fc-meta"><span>${Number(p.min_price || 0).toFixed(2)} €</span><span>${((n) => `${n} talla${n === 1 ? "" : "s"}`)((p.variants || []).filter((v) => v.available).length)}</span></div>
      </div>
    </a>`).join("");

  if (!motion) return;
  featuredTween?.scrollTrigger?.kill();
  featuredTween?.kill();
  const track = $("featured-track");
  const distance = () => Math.max(0, track.scrollWidth - innerWidth);
  featuredTween = gsap.to(track, {
    x: () => -distance(), ease: "none",
    scrollTrigger: {
      trigger: "#featured", start: "top top", end: () => "+=" + distance(),
      pin: ".featured-pin", scrub: 0.8, invalidateOnRefresh: true, anticipatePin: 1,
    },
  });
  gsap.from(".featured-title", { yPercent: 60, opacity: 0, duration: 1, ease: "power3.out", scrollTrigger: { trigger: "#featured", start: "top 75%" } });
  gsap.from(".feature-card", { opacity: 0, y: 60, rotate: 3, duration: 0.9, ease: "power3.out", stagger: 0.08, scrollTrigger: { trigger: "#featured", start: "top 70%" } });
  refreshScroll();
}

// ---------- Modelo de gustos (réplica de monitor/taste.py) ----------

const NOISE_TAGS = /^(hover\d*|paid|new ?in.*|new|ss\d+|fw\d+|fall\d+|\d+%off|\d+-dto|ea#.*|.*drop.*|backinstock|bestseller.*|coldfam|regular)$/;
const NOISE_WORDS = new Set(["and", "the", "with", "for", "man", "woman", "cold", "culture"]);
const STYLE_LABELS = new Set(["vault", "limited", "special_finish", "collab"]);
const PRIOR = 1.5;
const MIN_RATINGS = 5;
const NOTIFY_THRESHOLD = 4.0;

function features(p) {
  const f = new Set();
  if (p.product_type) f.add("type:" + p.product_type.trim().toLowerCase());
  for (const w of (p.title || "").toLowerCase().match(/[a-záéíóúñ0-9]+/g) || []) {
    if (w.length >= 3 && !NOISE_WORDS.has(w)) f.add("word:" + w);
  }
  for (let t of p.tags || []) {
    t = t.trim().toLowerCase();
    if (t && !NOISE_TAGS.test(t)) f.add("tag:" + t);
  }
  for (const l of p.exclusivity || []) if (STYLE_LABELS.has(l)) f.add("label:" + l);
  return f;
}

// Se reconstruye cuando cambian el catálogo o las valoraciones.
let model = null;
function buildModel() {
  const df = new Map();
  for (const p of state.products) for (const f of features(p)) df.set(f, (df.get(f) || 0) + 1);
  const n = state.products.length;
  const idf = (f) => Math.log((n + 1) / ((df.get(f) || 0) + 1)) + 1;
  const vector = (p) => {
    const v = new Map([...features(p)].map((f) => [f, idf(f)]));
    const norm = Math.sqrt([...v.values()].reduce((s, w) => s + w * w, 0)) || 1;
    for (const [f, w] of v) v.set(f, w / norm);
    return v;
  };
  const rated = Object.values(state.ratingDocs).map((r) => [vector(r), r.stars]);
  model = {
    rated,
    predict(p) {
      const v = vector(p);
      let num = 0, den = 0;
      for (const [rv, stars] of rated) {
        let sim = 0;
        for (const [f, w] of v) if (rv.has(f)) sim += w * rv.get(f);
        num += sim * (stars - 3);
        den += sim;
      }
      return Math.max(1, Math.min(5, 3 + num / (den + PRIOR)));
    },
  };
}

const profileReady = () => Object.keys(state.ratings).length >= MIN_RATINGS;
const predict = (p) => model.predict(p);

// ---------- Render ----------

function starsHtml(p) {
  const mine = state.ratings[p.docId] || 0;
  return `<div class="stars" role="group" aria-label="Valorar">${[1, 2, 3, 4, 5].map((n) =>
    `<button type="button" data-rate="${n}" data-doc="${esc(p.docId)}" class="${n <= mine ? "on" : ""}" aria-label="${n} estrellas">★</button>`).join("")}</div>`;
}

const SKELETON = Array.from({ length: 8 }, () => `
  <div class="card skeleton"><div class="img-link"></div>
    <div class="body"><div class="line"></div><div class="line short"></div></div></div>`).join("");

// `animate`: solo cuando el usuario cambia filtros; las actualizaciones de datos no deben hacer parpadear la lista.
function renderProducts({ animate = false } = {}) {
  if (!state.loaded) { $("products").innerHTML = SKELETON; return; }
  const q = $("search").value.trim().toLowerCase();
  const tag = state.tag;
  const onlyStock = $("filter-stock").checked;
  const sort = $("sort").value;
  const ready = profileReady();
  if (ready) buildModel();

  let list = state.products
    .filter((p) => !q || p.title.toLowerCase().includes(q))
    .filter((p) => !tag || (tag === "exclusive" ? exclusiveScore(p) > 0 : (p.exclusivity || []).includes(tag)))
    .filter((p) => !onlyStock || p.available)
    .map((p) => ({ p, pred: ready ? predict(p) : null }));

  const byDrop = (a, b) => (b.p.drop || "").localeCompare(a.p.drop || "", undefined, { numeric: true });
  const byExclusive = (a, b) => exclusiveScore(b.p) - exclusiveScore(a.p) || Number(b.p.available) - Number(a.p.available) || byDrop(a, b);
  if (sort === "for-you" && ready) list.sort((a, b) => (b.pred ?? 0) - (a.pred ?? 0) || byExclusive(a, b));
  else if (sort === "unrated") list = list.filter(({ p }) => !state.ratings[p.docId]).sort(byDrop);
  else if (sort === "new") list.sort(byDrop);
  else list.sort(byExclusive);

  $("products").innerHTML = list.length
    ? list.map(({ p, pred }, i) => `
      <div class="card ${p.available ? "" : "out"} ${exclusiveScore(p) ? "excl" : ""} ${animate && motion ? "pre" : ""}" style="--d:${i % 4}" data-doc="${esc(p.docId)}">
        <a href="${esc(safeUrl(p.url))}" target="_blank" rel="noopener" class="img-link">
          ${p.image ? `<img src="${esc(thumb(p.image))}" alt="" loading="lazy" decoding="async" class="loading" onload="this.classList.remove('loading')" onerror="this.classList.remove('loading')">` : ""}
          ${pred !== null && pred >= NOTIFY_THRESHOLD ? `<span class="for-you">✨ ${pred.toFixed(1)}★</span>` : ""}
        </a>
        <div class="body">
          <div class="tags">${tagsHtml(p.exclusivity)}</div>
          <a class="title" href="${esc(safeUrl(p.url))}" target="_blank" rel="noopener">${esc(p.title)}</a>
          <div class="sizes">${(p.variants || []).map((v) => `<span class="size ${v.available ? "on" : "off"}">${esc(v.title)}</span>`).join("")}</div>
          <div class="meta"><span class="price">${Number(p.min_price || 0).toFixed(2)} €</span><span>${p.drop ? `${esc(p.drop)} · ` : ""}${p.available ? "En stock" : "Agotado"}</span></div>
          ${state.uid ? starsHtml(p) : ""}
        </div>
      </div>`).join("")
    : `<p class="empty">${state.products.length ? "Ningún producto coincide con los filtros." : "Aún no hay productos. Aparecerán cuando el monitor haga su primera lectura."}</p>`;
  if (animate && motion) observeCards();
  else refreshScroll();
}

// Actualiza las estrellas sin volver a pintar la lista.
function paintStars() {
  for (const group of document.querySelectorAll(".stars")) {
    const mine = state.ratings[group.firstElementChild?.dataset.doc] || 0;
    group.querySelectorAll("button").forEach((b, i) => b.classList.toggle("on", i < mine));
  }
}

function renderTaste() {
  const n = Object.keys(state.ratings).length;
  let text;
  if (!state.uid) text = "";
  else if (n < MIN_RATINGS) text = `Valora prendas con las estrellas para que aprenda tus gustos: ${n}/${MIN_RATINGS}. Prueba el orden "Por valorar".`;
  else text = `Aprendiendo de ${n} valoraciones. Te avisaré de restocks y novedades que prevea con ≥${NOTIFY_THRESHOLD}★ — cuanto más valores (también lo que no te gusta), mejor.`;
  $("taste").hidden = !text;
  $("taste").textContent = text;
  $("sort").querySelector('[value="for-you"]').disabled = !profileReady();
}

function renderEvents() {
  $("events").innerHTML = state.events.length
    ? state.events.map((e) => `
      <li class="event ${esc(e.type)}" data-doc="${esc(`${e.store_id}_${e.product_id}`)}" data-type="${esc(e.type)}">
        <span class="kind ${esc(e.type)}">${esc(EVENT_LABELS[e.type] || e.type)}</span>
        <a href="${esc(safeUrl(e.url))}" target="_blank" rel="noopener" data-sheet>${esc(e.title)}</a>
        ${e.detail ? `<div class="detail">${esc(e.detail)}</div>` : ""}
        <div class="tags">${tagsHtml(e.exclusivity)}</div>
        <time>${esc(timeAgo(toDate(e.ts)))}</time>
      </li>`).join("")
    : `<li class="empty">Sin actividad todavía.</li>`;
}

function renderStats() {
  countUp($("stat-total"), state.products.length);
  countUp($("stat-available"), state.products.filter((p) => p.available).length);
  countUp($("stat-exclusive"), state.products.filter((p) => exclusiveScore(p) > 0).length);
  // Desglose: qué etiquetas forman el número de "Exclusivos".
  const parts = [...EXCLUSIVE].map((t) => [t, state.products.filter((p) => (p.exclusivity || []).includes(t)).length]);
  const nonZero = parts.filter(([, n]) => n);
  $("stat-exclusive-detail").textContent = nonZero.length
    ? nonZero.map(([t, n]) => `${n} ${TAG_LABELS[t]}`).join(" · ")
    : "Vault · Limited · Collab · Acabado";
  countUp($("stat-events"), state.events.length);
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
  state.products = snap.docs.map((d) => ({ ...d.data(), docId: d.id }));
  const first = !state.loaded;
  state.loaded = true;
  const last = state.products.map((p) => toDate(p.updated_at)).filter(Boolean).sort((a, b) => b - a)[0];
  setStatus(last ? `En directo · último cambio ${timeAgo(last)}` : "En directo", "live");
  renderProducts({ animate: first });
  renderStats();
  renderHero();
  renderFeatured();
  if (first) openFromUrl();
}, onError);

onSnapshot(query(collection(db, "events"), orderBy("ts", "desc"), limit(50)), (snap) => {
  state.events = snap.docs.map((d) => d.data());
  renderEvents();
  renderStats();
}, onError);

for (const id of ["search", "filter-stock", "sort"]) $(id).addEventListener("input", () => renderProducts({ animate: true }));
function selectTag(tag) {
  state.tag = tag;
  let active;
  document.querySelectorAll(".chip").forEach((c) => {
    c.classList.toggle("active", c.dataset.tag === tag);
    if (c.dataset.tag === tag) active = c;
  });
  active?.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
  $("stats").querySelectorAll(".stat").forEach((s) => s.classList.toggle("active",
    (s.dataset.stat === "exclusive" && tag === "exclusive") || (s.dataset.stat === "stock" && $("filter-stock").checked)));
  renderProducts({ animate: true });
}

$("chips").addEventListener("click", (ev) => {
  const chip = ev.target.closest(".chip");
  if (chip) selectTag(chip.dataset.tag);
});
$("filter-stock").addEventListener("change", () => selectTag(state.tag));

// Los contadores también filtran.
$("stats").addEventListener("click", (ev) => {
  const stat = ev.target.closest(".stat")?.dataset.stat;
  if (!stat) return;
  if (stat === "events") {
    document.querySelector(".feed").scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  if (stat === "all") { $("filter-stock").checked = false; $("search").value = ""; selectTag(""); }
  if (stat === "stock") { $("filter-stock").checked = !$("filter-stock").checked; selectTag(state.tag); }
  if (stat === "exclusive") selectTag(state.tag === "exclusive" ? "" : "exclusive");
  document.querySelector(".catalog").scrollIntoView({ behavior: "smooth", block: "start" });
});
renderProducts();
renderEvents();

// ---------- Usuario anónimo y valoraciones ----------

onAuthStateChanged(auth, (user) => {
  if (!user) return;
  state.uid = user.uid;
  onSnapshot(collection(db, "users", user.uid, "ratings"), (snap) => {
    const firstLoad = !state.ratingsLoaded;
    state.ratingsLoaded = true;
    state.ratingDocs = Object.fromEntries(snap.docs.map((d) => [d.id, d.data()]));
    state.ratings = Object.fromEntries(snap.docs.map((d) => [d.id, d.data().stars]));
    renderTaste();
    if (profileReady() && !state.sortChosen) $("sort").value = "for-you";
    // La primera vez hay que pintar todo; después basta con repintar las estrellas
    // (salvo en "Para ti" / "Por valorar", donde el orden depende de las valoraciones).
    // Si la ficha se abrió antes de iniciar sesión, le faltan las estrellas.
    const openDoc = !$("sheet").hidden && document.querySelector(".card.spot")?.dataset.doc;
    if (firstLoad && openDoc) {
      const p = state.products.find((x) => x.docId === openDoc);
      if (p) $("sheet-stars").innerHTML = starsHtml(p);
    }
    if (firstLoad || ["for-you", "unrated"].includes($("sort").value)) renderProducts({ animate: firstLoad });
    else paintStars();
    renderFeatured();
  }, (err) => console.error("ratings", err));
  syncSubscriber();
});
signInAnonymously(auth).catch((err) => {
  console.error(err);
  $("taste").hidden = false;
  $("taste").textContent = "No se pudo iniciar la sesión anónima: activa \"Anónimo\" en Firebase → Authentication.";
});
$("sort").addEventListener("change", () => { state.sortChosen = true; });

document.addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button[data-rate]");
  if (!btn || !state.uid) return;
  const docId = btn.dataset.doc;
  const stars = Number(btn.dataset.rate);
  const p = state.products.find((x) => x.docId === docId);
  if (!p) return;
  const ref = doc(db, "users", state.uid, "ratings", docId);
  btn.classList.remove("bump");
  void btn.offsetWidth; // reinicia la animación
  btn.classList.add("bump");
  try {
    if (state.ratings[docId] === stars) {
      await deleteDoc(ref); // pulsar la misma estrella otra vez quita la valoración
      toast("Valoración quitada");
    } else {
      const n = Object.keys(state.ratings).length + 1;
      toast(`${"★".repeat(stars)} guardado` + (n < MIN_RATINGS ? ` · ${n}/${MIN_RATINGS} para aprender` : ""));
      await setDoc(ref, {
        stars, title: p.title, product_type: p.product_type || "", tags: (p.tags || []).slice(0, 60),
        exclusivity: p.exclusivity || [], min_price: Number(p.min_price || 0), rated_at: serverTimestamp(),
      });
    }
  } catch (err) {
    console.error(err);
    toast("No se pudo guardar: " + err.message);
  }
});

// ---------- Ficha de prenda (se abre desde la notificación o la actividad) ----------

const SHEET_EVENTS = { ...EVENT_LABELS, for_you: "Te puede gustar" };
const SHEET_ICONS = { new_product: "🆕", restock: "🔁", variant_restock: "📏", sold_out: "❌", variant_sold_out: "⚠️", price_change: "💸", for_you: "✨" };

function openSheet(docId, eventType = "") {
  const p = state.products.find((x) => x.docId === docId);
  if (!p) { toast("Esta prenda ya no está en el catálogo"); return; }
  const last = state.events.find((e) => `${e.store_id}_${e.product_id}` === docId && (!eventType || e.type === eventType));
  const type = eventType || last?.type || "";

  $("sheet-img").src = p.image ? `${p.image}${p.image.includes("?") ? "&" : "?"}width=900` : "";
  const ev = $("sheet-event");
  ev.className = `sheet-event ${esc(type)}`;
  ev.textContent = type ? `${SHEET_ICONS[type] || ""} ${SHEET_EVENTS[type] || type}${last?.ts ? " · " + timeAgo(toDate(last.ts)) : ""}` : "";
  $("sheet-tags").innerHTML = tagsHtml(p.exclusivity);
  $("sheet-title").textContent = p.title;

  const why = $("sheet-why");
  const detail = last?.detail || "";
  let whyText = detail;
  if (profileReady()) {
    buildModel();
    const s = predict(p);
    if (s >= NOTIFY_THRESHOLD) whyText = `✨ Nota prevista para ti: ${s.toFixed(1)}★` + (detail ? ` · ${detail}` : "");
  }
  why.hidden = !whyText;
  why.textContent = whyText;

  const sizes = p.variants || [];
  const avail = sizes.filter((v) => v.available).length;
  $("sheet-price").textContent = `${Number(p.min_price || 0).toFixed(2)} €`;
  $("sheet-stock").textContent = !p.available ? "Agotado"
    : sizes.length === 1 ? "Disponible"
    : `${avail} de ${sizes.length} tallas disponibles`;
  $("sheet-sizes").innerHTML = sizes.map((v) => `<span class="size ${v.available ? "on" : "off"}">${esc(v.title)}</span>`).join("");
  $("sheet-stars").innerHTML = state.uid ? starsHtml(p) : "";
  $("sheet-cta").href = safeUrl(p.url);
  // En iPhone/iPad: botón que copia el nombre y abre la app de Cold Culture con un atajo.
  $("sheet-app").hidden = !isIOS;
  $("sheet-app").dataset.title = p.title;

  const sheet = $("sheet");
  sheet.classList.remove("closing");
  sheet.hidden = false;
  document.body.style.overflow = "hidden";

  // Resalta la prenda en el catálogo para encontrarla al cerrar.
  document.querySelectorAll(".card.spot").forEach((c) => c.classList.remove("spot"));
  document.querySelector(`.card[data-doc="${CSS.escape(docId)}"]`)?.classList.add("spot");
}

function closeSheet() {
  const sheet = $("sheet");
  if (sheet.hidden || sheet.classList.contains("closing")) return;
  sheet.classList.add("closing");
  setTimeout(() => { sheet.hidden = true; sheet.classList.remove("closing"); document.body.style.overflow = ""; }, 280);
}

$("sheet").addEventListener("click", (ev) => { if (ev.target.closest("[data-close]")) closeSheet(); });

// La app de Cold Culture no acepta enlaces, pero la app Atajos de iOS puede abrirla:
// el usuario crea un atajo "Cold Culture" con la acción "Abrir app". Copiamos el nombre
// de la prenda para pegarlo en el buscador de la app.
const SHORTCUT_NAME = "Cold Culture";
$("sheet-app").addEventListener("click", async () => {
  const title = $("sheet-app").dataset.title || "";
  try { await navigator.clipboard.writeText(title); } catch { /* sin permiso: se abre igual */ }
  toast("📋 Nombre copiado · pégalo en el buscador de la app");
  setTimeout(() => { location.href = `shortcuts://run-shortcut?name=${encodeURIComponent(SHORTCUT_NAME)}`; }, 450);
});
addEventListener("keydown", (ev) => { if (ev.key === "Escape") closeSheet(); });

$("events").addEventListener("click", (ev) => {
  const li = ev.target.closest(".event[data-doc]");
  if (!li) return;
  ev.preventDefault(); // el enlace del título abre la ficha, no la tienda
  openSheet(li.dataset.doc, li.dataset.type);
});

// Enlaces de las notificaciones: ?p=<prenda>&e=<evento> abre su ficha; ?view=activity, la actividad.
function openFromUrl() {
  const params = new URLSearchParams(location.search);
  const docId = params.get("p");
  const view = params.get("view");
  if (!docId && !view) return;
  history.replaceState(null, "", location.pathname); // que al recargar no se vuelva a abrir
  if (docId) setTimeout(() => openSheet(docId, params.get("e") || ""), motion ? 350 : 0);
  else if (view === "activity") setTimeout(() => $("feed").scrollIntoView({ behavior: "smooth", block: "start" }), 400);
}


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
  btn.querySelector(".lbl").textContent = "Notificaciones activadas";
  btn.setAttribute("aria-label", "Notificaciones activadas");
  btn.classList.add("on");
}

function readToken() {
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
}

function saveToken(token) {
  try { localStorage.setItem(TOKEN_KEY, token); } catch { /* sin almacenamiento: no pasa nada */ }
}

async function registerToken() {
  const registration = await navigator.serviceWorker.register("firebase-messaging-sw.js");
  await navigator.serviceWorker.ready;
  const token = await getToken(getMessaging(app), { vapidKey, serviceWorkerRegistration: registration });
  await setDoc(doc(db, "subscribers", token), {
    created_at: serverTimestamp(), ua: navigator.userAgent.slice(0, 250), uid: state.uid || "",
  });
  saveToken(token);
  return token;
}

// Vincula este dispositivo con el usuario anónimo para recibir avisos personalizados.
async function syncSubscriber() {
  if (!readToken() || !("Notification" in window) || Notification.permission !== "granted") return;
  try {
    if (await isSupported()) await registerToken();
  } catch (err) {
    console.error("syncSubscriber", err);
  }
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
    await registerToken();
    markEnabled();
    notifyMsg("¡Listo! Te avisaré de lo exclusivo y de lo que encaje con tus gustos, aunque tengas la web cerrada.");
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
