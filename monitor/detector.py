"""Compara dos lecturas del catálogo y genera eventos."""

from __future__ import annotations

from monitor.classifier import classify
from monitor.models import Event, Product


def detect_changes(old: dict[str, Product], new: dict[str, Product]) -> list[Event]:
    """`old` y `new` son catálogos indexados por id de producto.

    Si `old` está vacío (primera ejecución) no se generan eventos: solo se
    guarda la línea base, para no notificar todo el catálogo como "nuevo".
    """
    if not old:
        return []

    events: list[Event] = []
    for pid, prod in new.items():
        prev = old.get(pid)
        if prev is None:
            events.append(_event("new_product", prod, _price_label(prod)))
            continue

        if prod.available and not prev.available:
            events.append(_event("restock", prod, _sizes_label(prod)))
        elif prev.available and not prod.available:
            events.append(_event("sold_out", prod))
        elif prod.available:
            # Mismo estado global: mirar tallas concretas.
            prev_variants = {v.id: v for v in prev.variants}
            back = [v.title for v in prod.variants if v.available and v.id in prev_variants and not prev_variants[v.id].available]
            gone = [v.title for v in prod.variants if not v.available and v.id in prev_variants and prev_variants[v.id].available]
            if back:
                events.append(_event("variant_restock", prod, ", ".join(back)))
            if gone:
                events.append(_event("variant_sold_out", prod, ", ".join(gone)))

        if prod.min_price != prev.min_price:
            events.append(_event("price_change", prod, f"{prev.min_price:.2f} -> {prod.min_price:.2f}"))

    for pid, prev in old.items():
        if pid not in new:
            events.append(_event("removed", prev))
    return events


def _event(type_: str, prod: Product, detail: str = "") -> Event:
    return Event(
        type=type_,
        store_id=prod.store_id,
        product_id=prod.id,
        title=prod.title,
        url=prod.url,
        detail=detail,
        exclusivity=classify(prod),
    )


def _price_label(prod: Product) -> str:
    return f"desde {prod.min_price:.2f}"


def _sizes_label(prod: Product) -> str:
    sizes = [v.title for v in prod.variants if v.available]
    return "tallas: " + ", ".join(sizes) if sizes else ""
