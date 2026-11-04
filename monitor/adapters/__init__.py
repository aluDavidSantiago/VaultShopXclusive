from monitor.adapters.base import RobotsDisallowed, StoreAdapter
from monitor.adapters.shopify import ShopifyAdapter

ADAPTERS: dict[str, type[StoreAdapter]] = {
    "shopify": ShopifyAdapter,
}

__all__ = ["ADAPTERS", "RobotsDisallowed", "ShopifyAdapter", "StoreAdapter"]
