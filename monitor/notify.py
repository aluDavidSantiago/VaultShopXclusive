"""Notificaciones push al navegador/móvil con Firebase Cloud Messaging (FCM).

Los dispositivos se suscriben desde el dashboard (botón "Activar notificaciones"),
que guarda su token en la colección `subscribers` de Firestore. El monitor envía
a todos esos tokens y borra los que ya no son válidos.

Uso:
  python -m monitor.notify --test    # envía una notificación de prueba a todos los suscritos
"""

from __future__ import annotations

import argparse
import logging
import sys

from monitor.models import Event

log = logging.getLogger("monitor")

DASHBOARD_URL = "https://aludavidsantiago.github.io/VaultShopXclusive/"
ICON_URL = DASHBOARD_URL + "icon-192.png"

# Tipos de evento que se notifican siempre.
NOTIFY_TYPES = {"new_product", "restock", "variant_restock"}
# Además se notifica cualquier evento (incluso agotados) de productos con estas etiquetas.
EXCLUSIVE_LABELS = {"vault", "limited", "special_finish", "collab"}
# Si un ciclo genera más avisos que esto, se manda un único resumen.
MAX_NOTIFICATIONS = 5

TITLES = {
    "new_product": "🆕 Nuevo",
    "restock": "🔁 RESTOCK",
    "variant_restock": "📏 Vuelve talla",
    "sold_out": "❌ Agotado",
    "variant_sold_out": "⚠️ Talla agotada",
    "price_change": "💸 Precio",
    "removed": "🗑 Retirado",
}
LABELS = {"vault": "VAULT", "limited": "LIMITED", "special_finish": "ACABADO", "collab": "COLLAB"}


def should_notify(event: Event) -> bool:
    return event.type in NOTIFY_TYPES or bool(EXCLUSIVE_LABELS & set(event.exclusivity))


def format_event(event: Event) -> tuple[str, str]:
    """Devuelve (título, cuerpo) de la notificación."""
    labels = [LABELS[t] for t in event.exclusivity if t in LABELS]
    title = TITLES.get(event.type, event.type) + (" · " + " · ".join(labels) if labels else "")
    body = event.title + (f"\n{event.detail}" if event.detail else "")
    return title, body


class PushNotifier:
    BATCH = 500  # máximo de tokens por envío en FCM

    def __init__(self, db):
        from firebase_admin import messaging

        self.messaging = messaging
        self.db = db

    def tokens(self) -> list[str]:
        return [doc.id for doc in self.db.collection("subscribers").stream()]

    def send(self, title: str, body: str, link: str = DASHBOARD_URL, image: str = "") -> int:
        """Envía a todos los suscritos. Devuelve cuántos dispositivos lo recibieron."""
        m = self.messaging
        tokens = self.tokens()
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

    def send_events(self, events: list[Event], store_name: str = "") -> int:
        """Envía los eventos relevantes. Devuelve cuántos eventos se notificaron."""
        relevant = [e for e in events if should_notify(e)]
        if not relevant:
            return 0
        # Los exclusivos primero.
        relevant.sort(key=lambda e: not (EXCLUSIVE_LABELS & set(e.exclusivity)))

        if len(relevant) > MAX_NOTIFICATIONS:
            names = ", ".join(e.title for e in relevant[:3])
            self.send(f"{len(relevant)} cambios en {store_name or 'la tienda'}", f"{names}…", DASHBOARD_URL)
        else:
            for e in relevant:
                title, body = format_event(e)
                self.send(title, body, e.url, e.image)
        return len(relevant)


def send_test() -> int:
    from monitor.storage import FirestoreStorage, open_storage

    storage = open_storage()
    if not isinstance(storage, FirestoreStorage):
        print("Hacen falta credenciales de Firebase (firebase-key.json o FIREBASE_CREDENTIALS)")
        return 1
    notifier = PushNotifier(storage.db)
    n = len(notifier.tokens())
    if n == 0:
        print("No hay dispositivos suscritos. Abre el dashboard y pulsa 'Activar notificaciones'.")
        return 1
    sample = Event(
        type="restock", store_id="test", product_id="0", title="PRODUCTO DE PRUEBA",
        url=DASHBOARD_URL, detail="Si ves esto, las notificaciones funcionan ✅",
        exclusivity=["vault", "limited"],
    )
    title, body = format_event(sample)
    delivered = notifier.send("🧪 " + title, body, DASHBOARD_URL)
    print(f"Notificación de prueba entregada a {delivered} de {n} dispositivos")
    return 0 if delivered else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Notificaciones push")
    parser.add_argument("--test", action="store_true", required=True)
    parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(send_test())


if __name__ == "__main__":
    main()
