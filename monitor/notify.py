"""Notificaciones push personalizadas con Firebase Cloud Messaging (FCM).

Los dispositivos se suscriben desde el dashboard (botón "Activar notificaciones"),
que guarda su token y el uid anónimo del usuario en `subscribers`. Cada usuario
valora prendas en `users/{uid}/ratings`; con eso y el catálogo se calcula qué
prendas le gustarán (monitor/taste.py; la web hace el mismo cálculo para "Para ti").

Solo se notifican restocks y productos nuevos, y de ellos:
  - lo exclusivo (vault, limited, collab, acabado especial), siempre;
  - lo que el perfil predice que le gustará (>= NOTIFY_THRESHOLD estrellas);
  - lo que el usuario ya valoró con 4-5 estrellas y vuelve.
Lo que valoró con 1-2 estrellas no se le notifica nunca.

Uso:
  python -m monitor.notify --test        # notificación de prueba a todos los dispositivos
  python -m monitor.notify --recommend   # envía a cada usuario su mejor recomendación actual
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass

from monitor.classifier import classify
from monitor.models import Event, Product
from monitor.taste import NOTIFY_THRESHOLD, TasteModel

log = logging.getLogger("monitor")

DASHBOARD_URL = "https://aludavidsantiago.github.io/VaultShopXclusive/"
ICON_URL = DASHBOARD_URL + "icon-192.png"

NOTIFY_TYPES = {"new_product", "restock", "variant_restock"}
EXCLUSIVE_LABELS = {"vault", "limited", "special_finish", "collab"}
MAX_NOTIFICATIONS = 5  # por usuario y ciclo; si hay más, se manda un resumen

TITLES = {"new_product": "🆕 Nuevo", "restock": "🔁 Restock", "variant_restock": "📏 Vuelve talla"}
LABELS = {"vault": "VAULT", "limited": "LIMITED", "special_finish": "ACABADO", "collab": "COLLAB"}


def product_doc(prod: Product) -> dict:
    """Los mismos campos que se guardan en Firestore, para calcular rasgos."""
    return {**prod.to_dict(), "exclusivity": classify(prod), "min_price": prod.min_price}


@dataclass
class Alert:
    event: Event
    reason: str  # "exclusive" | "for_you" | "loved"
    stars: float | None = None
    why: tuple[str, ...] = ()

    def format(self) -> tuple[str, str]:
        e = self.event
        kind = TITLES.get(e.type, e.type)
        labels = [LABELS[t] for t in e.exclusivity if t in LABELS]
        if self.reason == "exclusive":
            title = f"💎 {kind} · " + " · ".join(labels)
        elif self.reason == "loved":
            title = f"❤️ {kind} · algo que valoraste"
        else:
            title = f"✨ Te puede gustar · {kind}"
        body = e.title + (f"\n{e.detail}" if e.detail else "")
        if self.reason == "for_you" and self.stars is not None:
            body += f"\n≈{self.stars:.1f}★" + (f" · te gusta {', '.join(self.why)}" if self.why else "")
        return title, body


def pick_alerts(events: list[Event], products: dict[str, dict], profile: TasteModel | None,
                rated: dict[str, int]) -> list[Alert]:
    """Decide qué eventos notificar a un usuario.

    `products`: product_id -> doc de la prenda; `rated`: product_id -> estrellas que le dio.
    """
    alerts = []
    for e in events:
        if e.type not in NOTIFY_TYPES:
            continue
        stars_given = rated.get(e.product_id)
        if stars_given is not None and stars_given <= 2:
            continue
        if EXCLUSIVE_LABELS & set(e.exclusivity):
            alerts.append(Alert(e, "exclusive"))
        elif stars_given is not None and stars_given >= 4:
            alerts.append(Alert(e, "loved"))
        elif profile and profile.ready and (doc := products.get(e.product_id)):
            pred = profile.predict(doc)
            if pred >= NOTIFY_THRESHOLD:
                alerts.append(Alert(e, "for_you", pred, tuple(profile.reasons(doc))))
    order = {"exclusive": 0, "loved": 1, "for_you": 2}
    alerts.sort(key=lambda a: (order[a.reason], -(a.stars or 0)))
    return alerts


class PushNotifier:
    BATCH = 500  # máximo de tokens por envío en FCM

    def __init__(self, db):
        from firebase_admin import messaging

        self.messaging = messaging
        self.db = db

    # --- datos de usuarios ---

    def subscribers(self) -> dict[str, list[str]]:
        """uid -> tokens. Los dispositivos sin uid (versiones antiguas) van bajo ""."""
        users: dict[str, list[str]] = {}
        for doc in self.db.collection("subscribers").stream():
            users.setdefault(doc.to_dict().get("uid", ""), []).append(doc.id)
        return users

    def ratings(self, uid: str) -> list[dict]:
        if not uid:
            return []
        return [{**d.to_dict(), "doc_id": d.id} for d in self.db.collection("users").document(uid).collection("ratings").stream()]

    def taste(self, uid: str, catalog: list[dict]) -> tuple[TasteModel, dict[str, int]]:
        """Modelo de gustos del usuario y sus valoraciones por producto."""
        ratings = self.ratings(uid)
        profile = TasteModel(ratings, catalog)
        rated = {r["doc_id"].split("_", 1)[-1]: int(r["stars"]) for r in ratings}
        return profile, rated

    # --- envío ---

    def send(self, title: str, body: str, link: str = DASHBOARD_URL, image: str = "", tokens: list[str] | None = None) -> int:
        """Envía a `tokens` (o a todos). Devuelve cuántos dispositivos lo recibieron."""
        m = self.messaging
        if tokens is None:
            tokens = [t for ts in self.subscribers().values() for t in ts]
        delivered = 0
        for i in range(0, len(tokens), self.BATCH):
            batch = tokens[i:i + self.BATCH]
            message = m.MulticastMessage(
                tokens=batch,
                webpush=m.WebpushConfig(
                    notification=m.WebpushNotification(title=title, body=body, icon=ICON_URL, image=image or None),
                    fcm_options=m.WebpushFCMOptions(link=link if link.startswith("https://") else DASHBOARD_URL),
                ),
            )
            resp = m.send_each_for_multicast(message)
            delivered += resp.success_count
            for token, r in zip(batch, resp.responses):
                if not r.success and isinstance(r.exception, (m.UnregisteredError, m.SenderIdMismatchError)):
                    self.db.collection("subscribers").document(token).delete()
                elif not r.success:
                    log.warning("FCM: fallo al enviar a un dispositivo: %s", r.exception)
        return delivered

    def send_alerts(self, alerts: list[Alert], tokens: list[str], store_name: str = "") -> None:
        if len(alerts) > MAX_NOTIFICATIONS:
            names = ", ".join(a.event.title for a in alerts[:3])
            self.send(f"{len(alerts)} prendas para ti en {store_name or 'la tienda'}", f"{names}…", DASHBOARD_URL, tokens=tokens)
            return
        for a in alerts:
            title, body = a.format()
            self.send(title, body, a.event.url, a.event.image, tokens=tokens)

    def notify_events(self, events: list[Event], products: dict[str, Product], store_name: str = "") -> int:
        """Actualiza los perfiles y envía a cada usuario sus avisos. Devuelve el total de avisos."""
        docs = {pid: product_doc(p) for pid, p in products.items()}
        catalog = list(docs.values())
        total = 0
        for uid, tokens in self.subscribers().items():
            profile, rated = self.taste(uid, catalog)
            alerts = pick_alerts(events, docs, profile, rated)
            if alerts:
                self.send_alerts(alerts, tokens, store_name)
                total += len(alerts)
        return total


def _notifier():
    from monitor.storage import FirestoreStorage, open_storage

    storage = open_storage()
    if not isinstance(storage, FirestoreStorage):
        print("Hacen falta credenciales de Firebase (firebase-key.json o FIREBASE_CREDENTIALS)")
        return None
    return PushNotifier(storage.db)


def send_test() -> int:
    notifier = _notifier()
    if notifier is None:
        return 1
    n = sum(len(t) for t in notifier.subscribers().values())
    if n == 0:
        print("No hay dispositivos suscritos. Abre el dashboard y pulsa 'Activar notificaciones'.")
        return 1
    delivered = notifier.send("🧪 Prueba · VaultShopXclusive", "Si ves esto, las notificaciones funcionan ✅")
    print(f"Notificación de prueba entregada a {delivered} de {n} dispositivos")
    return 0 if delivered else 1


def send_recommendations() -> int:
    """A cada usuario con perfil listo, su prenda en stock mejor valorada por el modelo (que no haya puntuado)."""
    notifier = _notifier()
    if notifier is None:
        return 1
    products = [d.to_dict() for d in notifier.db.collection("products").stream()]
    sent = 0
    for uid, tokens in notifier.subscribers().items():
        profile, rated = notifier.taste(uid, products)
        if not profile.ready:
            print(f"Usuario {uid[:6] or '(sin uid)'}: {profile.n_ratings} valoraciones, necesita al menos 5")
            continue
        scored = [(profile.predict(p), p) for p in products if p.get("available") and p["id"] not in rated]
        if not scored:
            continue
        stars, best = max(scored, key=lambda x: x[0])
        why = profile.reasons(best)
        body = f"{best['title']}\n≈{stars:.1f}★" + (f" · te gusta {', '.join(why)}" if why else "")
        notifier.send("✨ Esta prenda te puede gustar", body, best["url"], best.get("image", ""), tokens=tokens)
        print(f"Usuario {uid[:6]}: recomendado {best['title']} (≈{stars:.1f}★)")
        sent += 1
    return 0 if sent else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Notificaciones push")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--test", action="store_true")
    group.add_argument("--recommend", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # la consola de Windows no imprime "≈" ni emojis
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(send_test() if args.test else send_recommendations())


if __name__ == "__main__":
    main()
