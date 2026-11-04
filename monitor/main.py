"""Punto de entrada: python -m monitor.main [--config config/stores.yaml] [--data data]"""

from __future__ import annotations

import argparse
import logging
import sys

import httpx
import yaml

from monitor.adapters import ADAPTERS
from monitor.detector import detect_changes
from monitor.notify import PushNotifier
from monitor.storage import FirestoreStorage, open_storage

log = logging.getLogger("monitor")


def run(config_path: str, data_dir: str) -> int:
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    user_agent = config["user_agent"]
    storage = open_storage(data_dir)
    notifier = PushNotifier(storage.db) if isinstance(storage, FirestoreStorage) else None
    failures = 0

    with httpx.Client(headers={"User-Agent": user_agent}, timeout=20, follow_redirects=True) as client:
        for store in config["stores"]:
            adapter_cls = ADAPTERS.get(store["platform"])
            if adapter_cls is None:
                log.error("%s: plataforma desconocida '%s'", store["id"], store["platform"])
                failures += 1
                continue
            try:
                products = adapter_cls(store, client, user_agent).fetch_products()
            except Exception as exc:  # una tienda caída no debe parar las demás
                log.error("%s: error al leer la tienda: %s", store["id"], exc)
                failures += 1
                continue

            new = {p.id: p for p in products}
            old = storage.load_state(store["id"])
            events = detect_changes(old, new)
            storage.save_state(store["id"], new, old)
            storage.append_snapshot(store["id"], new)
            storage.append_events(events)

            if not old:
                log.info("%s: línea base guardada (%d productos)", store["id"], len(new))
            log.info("%s: %d productos, %d eventos", store["id"], len(new), len(events))
            for e in events:
                tags = f" [{', '.join(e.exclusivity)}]" if e.exclusivity else ""
                log.info("  %-16s %s%s %s", e.type, e.title, tags, e.detail)

            if notifier and events:
                try:
                    sent = notifier.send_events(events, store.get("name", store["id"]))
                    log.info("%s: %d eventos notificados", store["id"], sent)
                except Exception as exc:  # un fallo de FCM no debe perder los datos ya guardados
                    log.error("%s: error al notificar: %s", store["id"], exc)

    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor de tiendas")
    parser.add_argument("--config", default="config/stores.yaml")
    parser.add_argument("--data", default="data")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(run(args.config, args.data))


if __name__ == "__main__":
    main()
