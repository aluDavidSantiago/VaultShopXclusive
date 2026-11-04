"""Modelo de gustos a partir de las valoraciones (1-5 estrellas) de cada usuario.

Cada prenda se describe con rasgos (tipo, palabras del nombre, tags de estilo y
etiquetas de exclusividad). Los rasgos raros en el catálogo pesan más (IDF):
"distress" dice más de tu gusto que "hoodie". La nota prevista de una prenda es
una media de tus valoraciones ponderada por lo parecida que es a cada prenda
valorada, encogida hacia 3 cuando no se parece a nada (PRIOR).

web/app.js replica este módulo para ordenar "Para ti" en el navegador, así que
cualquier cambio aquí debe hacerse también allí.
"""

from __future__ import annotations

import math
import re

# Tags de la tienda que no dicen nada del estilo de la prenda.
NOISE_TAGS = re.compile(
    r"^(hover\d*|paid|new ?in.*|new|ss\d+|fw\d+|fall\d+|\d+%off|\d+-dto|ea#.*|.*drop.*|backinstock|bestseller.*|coldfam|regular)$"
)
NOISE_WORDS = {"and", "the", "with", "for", "man", "woman", "cold", "culture"}
# Etiquetas del clasificador que describen estilo (rebaja, bestseller... no son gusto).
STYLE_LABELS = {"vault", "limited", "special_finish", "collab"}

PRIOR = 1.5  # "parecido ficticio" con una prenda de 3★: sin parecido claro, la nota se queda cerca de 3
MIN_RATINGS = 5  # por debajo de esto el perfil no es fiable y no se recomienda
NOTIFY_THRESHOLD = 4.0  # nota prevista mínima para "te puede gustar"


def features(product: dict) -> set[str]:
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
        if label in STYLE_LABELS:
            feats.add("label:" + label)
    return feats


class TasteModel:
    def __init__(self, ratings: list[dict], catalog: list[dict]):
        """`ratings`: dicts con `stars` y los campos de la prenda; `catalog`: prendas actuales (para el IDF)."""
        df: dict[str, int] = {}
        for p in catalog:
            for f in features(p):
                df[f] = df.get(f, 0) + 1
        n = len(catalog)
        self._idf = lambda f: math.log((n + 1) / (df.get(f, 0) + 1)) + 1
        self.rated = [(self._vector(r), float(r["stars"])) for r in ratings]
        self.n_ratings = len(ratings)

    def _vector(self, product: dict) -> dict[str, float]:
        vec = {f: self._idf(f) for f in features(product)}
        norm = math.sqrt(sum(w * w for w in vec.values())) or 1.0
        return {f: w / norm for f, w in vec.items()}

    @property
    def ready(self) -> bool:
        return self.n_ratings >= MIN_RATINGS

    def predict(self, product: dict) -> float:
        """Nota prevista (1-5)."""
        vec = self._vector(product)
        num = den = 0.0
        for rvec, stars in self.rated:
            sim = sum(w * rvec[f] for f, w in vec.items() if f in rvec)
            num += sim * (stars - 3)
            den += sim
        return max(1.0, min(5.0, 3 + num / (den + PRIOR)))

    def reasons(self, product: dict, top: int = 2) -> list[str]:
        """Rasgos más distintivos que comparte con prendas que te gustaron (4-5★)."""
        vec = self._vector(product)
        liked = set().union(*(set(rvec) for rvec, stars in self.rated if stars >= 4)) if self.rated else set()
        shared = sorted((f for f in vec if f in liked), key=lambda f: -vec[f])
        return [_label(f) for f in shared[:top]]


def _label(feature: str) -> str:
    kind, _, value = feature.partition(":")
    return {"type": value, "word": f'"{value}"', "tag": value, "label": value.replace("_", " ")}.get(kind, value)
