"""Modelos de datos comunes a todas las tiendas."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Variant:
    id: str
    title: str
    price: float
    available: bool
    sku: str = ""


@dataclass
class Product:
    id: str
    store_id: str
    title: str
    handle: str
    url: str
    product_type: str = ""
    vendor: str = ""
    tags: list[str] = field(default_factory=list)
    image: str = ""
    published_at: str = ""
    variants: list[Variant] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return any(v.available for v in self.variants)

    @property
    def min_price(self) -> float:
        return min((v.price for v in self.variants), default=0.0)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Product:
        variants = [Variant(**v) for v in data.get("variants", [])]
        return cls(**{**data, "variants": variants})


@dataclass
class Event:
    """Un cambio detectado entre dos lecturas de la tienda."""

    type: str  # new_product | restock | sold_out | price_change | variant_restock | variant_sold_out | removed
    store_id: str
    product_id: str
    title: str
    url: str
    detail: str = ""
    exclusivity: list[str] = field(default_factory=list)
    image: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
