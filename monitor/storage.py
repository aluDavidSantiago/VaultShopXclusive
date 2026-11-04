"""Almacenamiento del estado del monitor: JSON local o Firestore."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from monitor.models import Event, Product

log = logging.getLogger("monitor")

KEY_FILE = "firebase-key.json"


def open_storage(data_dir: str = "data"):
    """Firestore si hay credenciales (variable FIREBASE_CREDENTIALS o firebase-key.json); si no, JSON local."""
    creds = os.environ.get("FIREBASE_CREDENTIALS", "").strip()
    if creds:
        log.info("Almacenamiento: Firestore (credenciales desde variable de entorno)")
        return FirestoreStorage(json.loads(creds))
    if Path(KEY_FILE).exists():
        log.info("Almacenamiento: Firestore (%s)", KEY_FILE)
        return FirestoreStorage(KEY_FILE)
    log.info("Almacenamiento: JSON local en %s/", data_dir)
    return LocalStorage(data_dir)


class LocalStorage:
    def __init__(self, root: Path | str = "data"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _state_path(self, store_id: str) -> Path:
        return self.root / f"state_{store_id}.json"

    def load_state(self, store_id: str) -> dict[str, Product]:
        path = self._state_path(store_id)
        if not path.exists():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {pid: Product.from_dict(p) for pid, p in raw.items()}

    def save_state(self, store_id: str, products: dict[str, Product], previous: dict[str, Product] | None = None) -> None:
        data = {pid: p.to_dict() for pid, p in products.items()}
        self._state_path(store_id).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def append_events(self, events: list[Event]) -> None:
        """Eventos en JSON Lines: una línea por evento, con marca de tiempo UTC."""
        now = datetime.now(timezone.utc).isoformat()
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps({"ts": now, **e.to_dict()}, ensure_ascii=False) + "\n")

    def append_snapshot(self, store_id: str, products: dict[str, Product]) -> None:
        """Histórico de disponibilidad por variante: la base para predecir restocks."""
        now = datetime.now(timezone.utc).isoformat()
        row = {
            "ts": now,
            "stock": {pid: [v.id for v in p.variants if v.available] for pid, p in products.items()},
        }
        with (self.root / f"history_{store_id}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")


class FirestoreStorage:
    """Colecciones:
    - products/{store_id}_{product_id}: estado actual de cada producto
    - events/{auto}: cambios detectados
    - snapshots/{auto}: disponibilidad por variante en cada ejecución (histórico)
    """

    BATCH_LIMIT = 400  # Firestore admite 500 operaciones por batch

    def __init__(self, credentials: str | dict):
        import firebase_admin
        from firebase_admin import credentials as fb_credentials
        from firebase_admin import firestore

        if not firebase_admin._apps:
            firebase_admin.initialize_app(fb_credentials.Certificate(credentials))
        self.db = firestore.client()

    @staticmethod
    def _doc_id(store_id: str, product_id: str) -> str:
        return f"{store_id}_{product_id}"

    def load_state(self, store_id: str) -> dict[str, Product]:
        from google.cloud.firestore_v1.base_query import FieldFilter

        docs = self.db.collection("products").where(filter=FieldFilter("store_id", "==", store_id)).stream()
        products = {}
        self._stored_labels: dict[str, tuple] = {}
        for doc in docs:
            data = doc.to_dict()
            for derived in ("updated_at", "available", "min_price", "drop"):
                data.pop(derived, None)
            # Se guardan aparte para reescribir el producto si cambian las reglas del clasificador.
            self._stored_labels[data["id"]] = tuple(data.pop("exclusivity", []))
            products[data["id"]] = Product.from_dict(data)
        return products

    def save_state(self, store_id: str, products: dict[str, Product], previous: dict[str, Product] | None = None) -> None:
        """Escribe solo los productos nuevos o cambiados y borra los retirados, para ahorrar escrituras."""
        from monitor.classifier import classify, drop_id

        previous = previous or {}
        stored_labels = getattr(self, "_stored_labels", {})
        now = datetime.now(timezone.utc)
        ops = []
        for pid, prod in products.items():
            labels = classify(prod)
            if previous.get(pid) != prod or stored_labels.get(pid) != tuple(labels):
                ops.append(("set", pid, {
                    **prod.to_dict(),
                    "available": prod.available,
                    "min_price": prod.min_price,
                    "exclusivity": labels,
                    "drop": drop_id(prod),
                    "updated_at": now,
                }))
        ops.extend(("delete", pid, None) for pid in previous if pid not in products)
        self._commit(ops, store_id)

    def _commit(self, ops: list, store_id: str) -> None:
        col = self.db.collection("products")
        for i in range(0, len(ops), self.BATCH_LIMIT):
            batch = self.db.batch()
            for op, pid, data in ops[i:i + self.BATCH_LIMIT]:
                ref = col.document(self._doc_id(store_id, pid))
                batch.set(ref, data) if op == "set" else batch.delete(ref)
            batch.commit()

    def append_events(self, events: list[Event]) -> None:
        now = datetime.now(timezone.utc)
        for i in range(0, len(events), self.BATCH_LIMIT):
            batch = self.db.batch()
            for e in events[i:i + self.BATCH_LIMIT]:
                batch.set(self.db.collection("events").document(), {"ts": now, **e.to_dict()})
            batch.commit()

    def append_snapshot(self, store_id: str, products: dict[str, Product]) -> None:
        self.db.collection("snapshots").add({
            "ts": datetime.now(timezone.utc),
            "store_id": store_id,
            "stock": {pid: [v.id for v in p.variants if v.available] for pid, p in products.items()},
        })
