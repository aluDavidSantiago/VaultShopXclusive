"""Almacenamiento local en JSON. Se sustituirá por Firestore más adelante."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from monitor.models import Event, Product


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

    def save_state(self, store_id: str, products: dict[str, Product]) -> None:
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
