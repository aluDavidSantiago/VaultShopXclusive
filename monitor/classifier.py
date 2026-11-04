"""Clasificación de exclusividad basada en reglas.

Primera capa, barata y determinista. Más adelante los productos ambiguos se
enviarán a la API de Claude para una segunda opinión.
"""

from __future__ import annotations

import re

from monitor.models import Product

# etiqueta -> patrones buscados en título, product_type y tags
RULES: dict[str, list[str]] = {
    "vault": [r"\bvault\b"],
    "limited": [r"\blimited\b", r"\bltd\b", r"\bexclusive\b", r"\bnumbered\b", r"\bonly \d+\b", r"\b1 of \d+\b"],
    "special_finish": [
        r"\bembroider(ed|y)\b", r"\bpuff\b", r"\bacid wash\b", r"\bdistress(ed)?\b",
        r"\bgarment dyed\b", r"\bfoil\b", r"\breflective\b", r"\bsuede\b", r"\bchenille\b",
        r"\bstrass\b", r"\brhinestones?\b", r"\bstudded\b", r"\brivets?\b",
    ],
    # "Marca x Marca" con espacios; evita falsos positivos como "X-KEVIL".
    "collab": [r"\w \bx\b \w", r"\bcollab(oration)?\b"],
}

# Tags propios de la tienda (Cold Culture) que aportan señal.
TAG_RULES: dict[str, list[str]] = {
    "back_in_stock": [r"^backinstock$", r"^back in stock$"],
    "bestseller": [r"^bestseller", r"^best ?seller"],
    "sale": [r"^\d+%off$", r"^\d+-dto$"],
}
DROP_TAG = re.compile(r"(?:^|_)drop\s?(\d+)", re.I)

# Un producto con 2 variantes suele ser una tirada corta (talla única no cuenta).
SHORT_RUN_MAX_VARIANTS = 2


def classify(prod: Product) -> list[str]:
    text = " ".join([prod.title, prod.product_type, *prod.tags]).lower()
    labels = [label for label, patterns in RULES.items() if any(re.search(p, text) for p in patterns)]

    tags = [t.strip().lower() for t in prod.tags]
    labels += [label for label, patterns in TAG_RULES.items() if any(re.search(p, t) for p in patterns for t in tags)]

    if prod.variants and 1 < len(prod.variants) <= SHORT_RUN_MAX_VARIANTS:
        labels.append("short_run")
    return labels


def drop_id(prod: Product) -> str:
    """Identificador del lanzamiento según los tags (p. ej. "2026agosto_drop33" -> "drop33")."""
    nums = [m.group(1) for t in prod.tags if (m := DROP_TAG.search(t))]
    return f"drop{max(nums, key=int)}" if nums else ""
