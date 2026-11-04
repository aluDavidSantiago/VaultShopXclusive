"""Modelo de gustos a partir de las valoraciones (1-5 estrellas) de cada usuario.

Cada prenda se describe con rasgos sencillos (tipo, palabras del nombre, tags,
etiquetas de exclusividad y rango de precio). De las valoraciones se aprende un
peso por rasgo: cuánto sube o baja la nota cuando aparece. La nota prevista de
una prenda es 3 + la media ponderada de los pesos de sus rasgos conocidos.

web/app.js replica `features()` para ordenar el catálogo "Para ti" con los
mismos pesos, así que cualquier cambio aquí debe hacerse también allí.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Tags de la tienda que no dicen nada del estilo de la prenda.
NOISE_TAGS = re.compile(
    r"^(hover\d*|paid|new ?in.*|new|ss\d+|fw\d+|fall\d+|\d+%off|\d+-dto|ea#.*|.*drop.*|backinstock|bestseller.*|coldfam|regular)$"
)
NOISE_WORDS = {"and", "the", "with", "for", "man", "woman", "cold", "culture"}
PRICE_BUCKETS = [(30, "<30"), (60, "30-60"), (100, "60-100"), (float("inf"), "100+")]

SMOOTHING = 2.0  # cuántas valoraciones "neutras" se suponen por rasgo: evita conclusiones con 1 sola nota
MIN_RATINGS = 5  # por debajo de esto el perfil no es fiable y no se recomienda
NOTIFY_THRESHOLD = 4.0  # nota prevista mínima para "te puede gustar"


def features(product: dict) -> list[str]:
    """Rasgos de una prenda. `product` usa los campos guardados en Firestore."""
    feats = set()
    if product.get("product_type"):
        feats.add("type:" + product["product_type"].strip().lower())
    for word in re.findall(r"[a-záéíóúñ0-9]+", (product.get("title") or "").lower()):
        if len(word) >= 3 and word not in NOISE_WORDS:
            feats.add("word:" + word)
    for tag in product.get("tags") or []:
        tag = tag.strip().lower()
        if tag and not NOISE_TAGS.match(tag):
            feats.add("tag:" + tag)
    for label in product.get("exclusivity") or []:
        feats.add("label:" + label)
    price = float(product.get("min_price") or 0)
    if price:
        feats.add("price:" + next(name for limit, name in PRICE_BUCKETS if price < limit))
    return sorted(feats)


@dataclass
class TasteProfile:
    weights: dict[str, float] = field(default_factory=dict)  # rasgo -> efecto en estrellas
    counts: dict[str, int] = field(default_factory=dict)  # rasgo -> nº de prendas valoradas con él
    n_ratings: int = 0

    @classmethod
    def learn(cls, ratings: list[dict]) -> TasteProfile:
        """`ratings`: dicts con `stars` (1-5) y los campos de la prenda."""
        sums: dict[str, float] = {}
        counts: dict[str, int] = {}
        for r in ratings:
            delta = float(r["stars"]) - 3.0
            for f in features(r):
                sums[f] = sums.get(f, 0.0) + delta
                counts[f] = counts.get(f, 0) + 1
        weights = {f: sums[f] / (counts[f] + SMOOTHING) for f in sums}
        return cls(weights=weights, counts=counts, n_ratings=len(ratings))

    @property
    def ready(self) -> bool:
        return self.n_ratings >= MIN_RATINGS

    def predict(self, product: dict) -> float | None:
        """Nota prevista (1-5) o None si la prenda no comparte ningún rasgo con lo valorado."""
        known = [f for f in features(product) if f in self.weights]
        if not known:
            return None
        total = sum(self.counts[f] for f in known)
        score = 3.0 + sum(self.weights[f] * self.counts[f] for f in known) / total * 2
        return max(1.0, min(5.0, score))

    def reasons(self, product: dict, top: int = 2) -> list[str]:
        """Rasgos que más empujan la nota hacia arriba, en texto legible."""
        known = [(self.weights[f], f) for f in features(product) if self.weights.get(f, 0) > 0]
        return [_label(f) for _, f in sorted(known, reverse=True)[:top]]

    def to_dict(self) -> dict:
        # La web usa estos mismos pesos para ordenar "Para ti".
        return {"weights": {f: round(w, 4) for f, w in self.weights.items()},
                "counts": dict(self.counts), "n_ratings": self.n_ratings}


def _label(feature: str) -> str:
    kind, _, value = feature.partition(":")
    return {"type": value, "word": f'"{value}"', "tag": value, "label": value.replace("_", " "),
            "price": f"precio {value} €"}.get(kind, value)
