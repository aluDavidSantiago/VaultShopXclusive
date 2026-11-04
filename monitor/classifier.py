"""Clasificación de exclusividad basada en reglas.

Primera capa, barata y determinista. Más adelante los productos ambiguos se
enviarán a la API de Claude para una segunda opinión.
"""

from __future__ import annotations

import re

from monitor.models import Product

# etiqueta -> patrones (se buscan en título, tags y product_type)
RULES: dict[str, list[str]] = {
    "vault": [r"\bvault\b"],
    "limited": [r"\blimited\b", r"\bltd\b", r"\bexclusive\b", r"\bnumbered\b", r"\bonly \d+\b"],
    "special_finish": [
        r"\bembroider(ed|y)\b", r"\bpuff\b", r"\bacid wash\b", r"\bdistress(ed)?\b",
        r"\bgarment dyed\b", r"\bfoil\b", r"\breflective\b", r"\bsuede\b", r"\bchenille\b",
    ],
    "collab": [r"\bx\b", r"\bcollab(oration)?\b"],
}

# Un producto con pocas variantes suele ser una tirada corta (p. ej. talla única de accesorio no cuenta).
SHORT_RUN_MAX_VARIANTS = 2


def classify(prod: Product) -> list[str]:
    text = " ".join([prod.title, prod.product_type, *prod.tags]).lower()
    labels = [label for label, patterns in RULES.items() if any(re.search(p, text) for p in patterns)]
    if prod.variants and 1 < len(prod.variants) <= SHORT_RUN_MAX_VARIANTS:
        labels.append("short_run")
    return labels
