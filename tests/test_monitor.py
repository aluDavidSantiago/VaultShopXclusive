import httpx
import pytest

from monitor.adapters import RobotsDisallowed, ShopifyAdapter
from monitor.classifier import classify, drop_id
from monitor.detector import detect_changes
from monitor.models import Product, Variant
from monitor.storage import LocalStorage

STORE = {"id": "test", "url": "https://shop.test", "request_delay": 0}
UA = "VaultShopXclusive-test"


def raw_product(pid, title, sizes, tags=""):
    return {
        "id": pid,
        "title": title,
        "handle": title.lower().replace(" ", "-"),
        "product_type": "Hoodie",
        "tags": tags,
        "images": [{"src": "https://cdn.test/img.jpg"}],
        "variants": [
            {"id": pid * 10 + i, "title": s, "price": "120.00", "available": avail, "sku": ""}
            for i, (s, avail) in enumerate(sizes)
        ],
    }


def make_client(products, robots="User-agent: *\nDisallow: /checkout\n"):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        if request.url.path == "/products.json":
            page = int(request.url.params.get("page", 1))
            return httpx.Response(200, json={"products": products if page == 1 else []})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def product(pid, sizes, title="Basic Tee", price=50.0):
    return Product(
        id=pid, store_id="test", title=title, handle="h", url="u",
        variants=[Variant(id=f"{pid}-{s}", title=s, price=price, available=a) for s, a in sizes],
    )


def test_shopify_adapter_parses_products():
    client = make_client([raw_product(1, "Vault Hoodie", [("S", True), ("M", False)], tags="vault, fw26")])
    products = ShopifyAdapter(STORE, client, UA).fetch_products()
    assert len(products) == 1
    p = products[0]
    assert p.url == "https://shop.test/products/vault-hoodie"
    assert p.tags == ["vault", "fw26"]
    assert p.available and p.min_price == 120.0


def test_shopify_adapter_respects_robots():
    client = make_client([], robots="User-agent: *\nDisallow: /products.json\n")
    with pytest.raises(RobotsDisallowed):
        ShopifyAdapter(STORE, client, UA).fetch_products()


def test_shopify_adapter_retries_on_429():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"products": [raw_product(1, "Tee", [("S", True)])]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert len(ShopifyAdapter(STORE, client, UA).fetch_products()) == 1
    assert calls["n"] == 2


def test_first_run_produces_no_events():
    assert detect_changes({}, {"1": product("1", [("S", True)])}) == []


def test_detects_new_restock_soldout_and_sizes():
    old = {
        "1": product("1", [("S", False), ("M", False)]),
        "2": product("2", [("S", True)]),
        "3": product("3", [("S", True), ("M", False), ("L", True)]),
        "4": product("4", [("S", True)]),
    }
    new = {
        "1": product("1", [("S", True), ("M", False)]),
        "2": product("2", [("S", False)]),
        "3": product("3", [("S", False), ("M", True), ("L", True)]),
        "4": product("4", [("S", True)], price=40.0),
        "5": product("5", [("S", True)], title="Limited Vault Jacket"),
    }
    events = {(e.type, e.product_id): e for e in detect_changes(old, new)}
    assert ("restock", "1") in events
    assert ("sold_out", "2") in events
    assert events[("variant_restock", "3")].detail == "M"
    assert events[("variant_sold_out", "3")].detail == "S"
    assert events[("price_change", "4")].detail == "50.00 -> 40.00"
    assert set(events[("new_product", "5")].exclusivity) >= {"vault", "limited"}


def test_detects_removed_product():
    events = detect_changes({"1": product("1", [("S", True)])}, {"2": product("2", [("S", True)])})
    assert {e.type for e in events} == {"removed", "new_product"}


def test_classifier():
    assert classify(product("1", [("S", True)], title="Embroidered Puff Hoodie")) == ["special_finish"]
    assert classify(product("1", [("S", True)], title="Basic Tee")) == []


def test_classifier_store_tags_and_collab():
    p = product("1", [("S", True)], title="X-KEVIL KEYCHAIN")
    assert "collab" not in classify(p)
    assert "collab" in classify(product("1", [("S", True)], title="COLD CULTURE x NIKE TEE"))
    p.tags = ["backinstock", "30%off", "2026julio_drop29", "drop16y17"]
    assert set(classify(p)) == {"back_in_stock", "sale"}
    assert drop_id(p) == "drop29"


def test_storage_roundtrip(tmp_path):
    storage = LocalStorage(tmp_path)
    state = {"1": product("1", [("S", True), ("M", False)])}
    storage.save_state("test", state)
    assert storage.load_state("test") == state


def event(type_, title="Tee", labels=()):
    from monitor.models import Event
    return Event(type=type_, store_id="test", product_id=title, title=title, url="https://shop.test/p", exclusivity=list(labels))


def test_should_notify():
    from monitor.notify import should_notify
    assert should_notify(event("restock"))
    assert should_notify(event("new_product"))
    assert not should_notify(event("sold_out"))
    assert should_notify(event("sold_out", labels=["vault"]))
    assert not should_notify(event("price_change", labels=["sale"]))


def test_format_event():
    from monitor.notify import format_event
    e = event("restock", "VAULT HOODIE", ["vault", "sale"])
    e.detail = "tallas: S, M"
    assert format_event(e) == ("🔁 RESTOCK · VAULT", "VAULT HOODIE\ntallas: S, M")


def test_send_events_individual_and_summary():
    from monitor.notify import MAX_NOTIFICATIONS, PushNotifier

    class FakeNotifier(PushNotifier):
        def __init__(self):
            self.sent = []

        def send(self, title, body, link="", image=""):
            self.sent.append((title, body, link))
            return 1

    n = FakeNotifier()
    assert n.send_events([event("restock", "A"), event("sold_out", "B"), event("new_product", "C", ["collab"])]) == 2
    assert n.sent[0][0].startswith("🆕 Nuevo · COLLAB")  # los exclusivos primero

    n = FakeNotifier()
    many = [event("restock", f"P{i}") for i in range(MAX_NOTIFICATIONS + 1)]
    assert n.send_events(many, "Cold Culture") == MAX_NOTIFICATIONS + 1
    assert len(n.sent) == 1 and n.sent[0][0] == f"{MAX_NOTIFICATIONS + 1} cambios en Cold Culture"
