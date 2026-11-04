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
    - catalog/{store_id}_{n}: el catálogo de la tienda repartido en pocos documentos grandes
      (campo `products`). Leer el estado cuesta ~3 lecturas en vez de una por prenda, lo que
      mantiene el monitor y el dashboard dentro de la cuota gratuita de Firestore.
    - events/{auto}: cambios detectados
    - snapshots/{auto}: disponibilidad por variante en cada ejecución (histórico)
    """

    BATCH_LIMIT = 400  # Firestore admite 500 operaciones por batch
    CHUNK_BYTES = 600_000  # margen bajo el límite de 1 MiB por documento

    def __init__(self, credentials: str | dict):
        import firebase_admin
        from firebase_admin import credentials as fb_credentials
        from firebase_admin import firestore

        if not firebase_admin._apps:
            firebase_admin.initialize_app(fb_credentials.Certificate(credentials))
        self.db = firestore.client()
        self._loaded: dict[str, dict[str, dict]] = {}  # store_id -> id -> entrada guardada
        self._chunks: dict[str, dict[str, list]] = {}  # store_id -> doc id -> productos

    def catalog_docs(self, store_id: str | None = None) -> list[dict]:
        """Prendas guardadas (con campos derivados: exclusivity, available, min_price, drop...)."""
        from google.cloud.firestore_v1.base_query import FieldFilter

        col = self.db.collection("catalog")
        query = col.where(filter=FieldFilter("store_id", "==", store_id)) if store_id else col
        items = []
        for doc in query.stream():
            data = doc.to_dict()
            if store_id:
                self._chunks.setdefault(store_id, {})[doc.id] = data.get("products", [])
            items.extend(data.get("products", []))
        return items

    def load_state(self, store_id: str) -> dict[str, Product]:
        self._chunks[store_id] = {}
        entries = {e["id"]: e for e in self.catalog_docs(store_id)}
        self._loaded[store_id] = entries
        fields = set(Product.__dataclass_fields__)
        return {pid: Product.from_dict({k: v for k, v in e.items() if k in fields}) for pid, e in entries.items()}

    def save_state(self, store_id: str, products: dict[str, Product], previous: dict[str, Product] | None = None) -> None:
        """Reparte el catálogo en documentos y escribe solo los que han cambiado."""
        from monitor.classifier import classify, drop_id

        loaded = self._loaded.get(store_id, {})
        now = datetime.now(timezone.utc).isoformat()
        entries = []
        for pid in sorted(products):
            prod = products[pid]
            entry = {**prod.to_dict(), "available": prod.available, "min_price": prod.min_price,
                     "exclusivity": classify(prod), "drop": drop_id(prod)}
            old = loaded.get(pid)
            unchanged = old is not None and all(old.get(k) == v for k, v in entry.items())
            entry["updated_at"] = old.get("updated_at", now) if unchanged else now
            entries.append(entry)

        chunks, current, size = [], [], 0
        for e in entries:
            n = len(json.dumps(e, ensure_ascii=False))
            if current and size + n > self.CHUNK_BYTES:
                chunks.append(current)
                current, size = [], 0
            current.append(e)
            size += n
        if current:
            chunks.append(current)

        old_chunks = self._chunks.get(store_id, {})
        new_chunks = {f"{store_id}_{i}": c for i, c in enumerate(chunks)}
        batch = self.db.batch()
        writes = 0
        for doc_id, items in new_chunks.items():
            if old_chunks.get(doc_id) != items:
                batch.set(self.db.collection("catalog").document(doc_id),
                          {"store_id": store_id, "products": items, "updated_at": now})
                writes += 1
        for doc_id in old_chunks.keys() - new_chunks.keys():
            batch.delete(self.db.collection("catalog").document(doc_id))
            writes += 1
        if writes:
            batch.commit()
        self._chunks[store_id] = new_chunks
        self._loaded[store_id] = {e["id"]: e for e in entries}

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
