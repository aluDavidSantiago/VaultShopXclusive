"""Adaptador para tiendas Shopify usando el endpoint público /products.json."""

from __future__ import annotations

import time

from monitor.adapters.base import StoreAdapter
from monitor.models import Product, Variant

PAGE_SIZE = 250  # máximo que permite Shopify
MAX_PAGES = 40  # tope de seguridad: 10.000 productos


class ShopifyAdapter(StoreAdapter):
    def fetch_products(self) -> list[Product]:
        self.check_robots("/products.json")
        products: list[Product] = []
        for page in range(1, MAX_PAGES + 1):
            if page > 1:
                time.sleep(self.delay)
            resp = self.client.get(
                f"{self.base_url}/products.json",
                params={"limit": PAGE_SIZE, "page": page},
            )
            resp.raise_for_status()
            batch = resp.json().get("products", [])
            products.extend(self.parse_product(p) for p in batch)
            if len(batch) < PAGE_SIZE:
                break
        return products

    def parse_product(self, raw: dict) -> Product:
        tags = raw.get("tags") or []
        if isinstance(tags, str):  # algunas tiendas devuelven "a, b, c"
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        images = raw.get("images") or []
        return Product(
            id=str(raw["id"]),
            store_id=self.store_id,
            title=raw.get("title", ""),
            handle=raw.get("handle", ""),
            url=f"{self.base_url}/products/{raw.get('handle', '')}",
            product_type=raw.get("product_type") or "",
            vendor=raw.get("vendor") or "",
            tags=tags,
            image=images[0].get("src", "") if images else "",
            published_at=raw.get("published_at") or "",
            variants=[
                Variant(
                    id=str(v["id"]),
                    title=v.get("title", ""),
                    price=float(v.get("price") or 0),
                    available=bool(v.get("available", False)),
                    sku=v.get("sku") or "",
                )
                for v in raw.get("variants", [])
            ],
        )
