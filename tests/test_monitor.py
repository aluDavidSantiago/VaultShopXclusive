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



def event(type_, title="Tee", labels=(), pid=None):
    from monitor.models import Event
    return Event(type=type_, store_id="test", product_id=pid or title, title=title, url="https://shop.test/p", exclusivity=list(labels))


def doc(title, ptype="Hoodies", price=80.0, tags=(), labels=()):
    return {"title": title, "product_type": ptype, "min_price": price, "tags": list(tags), "exclusivity": list(labels)}


CATALOG = [doc(t, ty, pr) for t, ty, pr in [
    ("DISTRESS HOODIE BLACK", "Hoodies", 80), ("DISTRESS HOODIE GREY", "Hoodies", 80), ("DISTRESS TEE NAVY", "Tees", 40),
    ("BASIC HOODIE BLUE", "Hoodies", 70), ("PLAIN HOODIE RED", "Hoodies", 70), ("LOGO HOODIE GREEN", "Hoodies", 70),
    ("BASIC SOCKS WHITE", "Socks", 15), ("LOGO SOCKS BLACK", "Socks", 15), ("STRIPE SOCKS RED", "Socks", 15),
    ("SILVER RING", "Jewelry", 30), ("GOLD CHAIN", "Jewelry", 40), ("LEATHER BELT", "Belts", 35),
]] + [doc(f"{w} HOODIE", "Hoodies", 70) for w in ("ORBIT", "REBEL", "EXPLORE", "STORM", "AERO", "SCRIPT", "GRID", "WAVE", "FLAME", "STAR")]
# Como en la tienda real: "hoodie" es muy común y "distress" es raro.


def test_taste_features_skip_store_noise():
    from monitor.taste import features
    f = features(doc("DISTRESS HOODIE GREY", tags=["hover2", "30%off", "2026julio_drop29", "hoodies", "ea#hoodies"], labels=["sale", "collab"]))
    assert f == {"tag:hoodies", "type:hoodies", "word:distress", "word:grey", "word:hoodie", "label:collab"}


def test_taste_rare_features_matter_more():
    from monitor.taste import TasteModel
    ratings = [{**doc(f"DISTRESS HOODIE {c}"), "stars": 5} for c in ("BLACK", "GREY", "CAMO", "PINK", "NAVY")]
    m = TasteModel(ratings, CATALOG)
    assert m.ready
    distress, plain, ring = (m.predict(doc(t, ty)) for t, ty in [("DISTRESS TEE NAVY", "Tees"), ("PLAIN HOODIE RED", "Hoodies"), ("SILVER RING", "Jewelry")])
    assert distress > plain > ring
    assert distress >= 4 > plain
    assert ring == 3  # sin parecido: neutra
    assert '"distress"' in m.reasons(doc("DISTRESS TEE NAVY", "Tees"))


def test_taste_learns_dislikes():
    from monitor.taste import TasteModel
    ratings = [{**doc("BASIC SOCKS WHITE", "Socks", 15), "stars": 1}, {**doc("LOGO SOCKS BLACK", "Socks", 15), "stars": 1},
               {**doc("DISTRESS HOODIE BLACK"), "stars": 5}]
    m = TasteModel(ratings, CATALOG)
    assert m.predict(doc("STRIPE SOCKS RED", "Socks", 15)) < 2.5
    assert not m.ready


def test_pick_alerts():
    from monitor.notify import pick_alerts
    from monitor.taste import TasteModel
    profile = TasteModel([{**doc(f"DISTRESS HOODIE {c}"), "stars": 5} for c in ("BLACK", "GREY", "CAMO", "PINK", "NAVY")], CATALOG)
    products = {"h": doc("DISTRESS HOODIE NAVY"), "s": doc("PLAIN SOCKS", "Socks", 10)}
    events = [
        event("restock", "DISTRESS HOODIE NAVY", pid="h"),      # le gustará
        event("restock", "PLAIN SOCKS", pid="s"),               # no encaja
        event("new_product", "VAULT JACKET", ["vault"], pid="v"),  # exclusivo: siempre
        event("sold_out", "VAULT TEE", ["vault"], pid="t"),     # agotados: nunca
        event("restock", "OLD FAV", pid="f"),                   # valorado 5
        event("restock", "HATED", ["collab"], pid="x"),         # valorado 1: nunca, ni exclusivo
    ]
    alerts = pick_alerts(events, products, profile, {"f": 5, "x": 1})
    assert [(a.event.product_id, a.reason) for a in alerts] == [("v", "exclusive"), ("f", "loved"), ("h", "for_you")]
    title, body = alerts[2].format()
    assert title.startswith("✨ Te puede gustar") and "★" in body


def test_pick_alerts_without_profile_only_exclusives():
    from monitor.notify import pick_alerts
    events = [event("restock", "A", pid="a"), event("new_product", "B", ["collab"], pid="b")]
    assert [a.event.product_id for a in pick_alerts(events, {}, None, {})] == ["b"]


class FakeFirestore:
    """Lo mínimo de Firestore para probar FirestoreStorage sin red: cuenta lecturas y escrituras."""

    def __init__(self):
        self.data, self.reads, self.writes = {}, 0, 0

    def collection(self, name):
        db = self

        class Doc:
            def __init__(self, doc_id):
                self.id = doc_id
            def to_dict(self):
                return db.data[name][self.id]

        class Ref:
            def __init__(self, doc_id):
                self.doc_id = doc_id

        class Col:
            def document(self, doc_id):
                return Ref(doc_id)
            def where(self, filter):
                field, value = filter.field_path, filter.value
                return Query(lambda d: d.get(field) == value)
            def stream(self):
                return Query(lambda d: True).stream()

        class Query:
            def __init__(self, pred):
                self.pred = pred
            def stream(self):
                for doc_id, d in db.data.get(name, {}).items():
                    if self.pred(d):
                        db.reads += 1
                        yield Doc(doc_id)

        Col.name = name
        return Col()

    def batch(self):
        db = self

        class Batch:
            ops = []
            def set(self, ref, data):
                self.ops.append(("set", ref, data))
            def delete(self, ref):
                self.ops.append(("del", ref, None))
            def commit(self):
                for op, ref, data in self.ops:
                    db.writes += 1
                    if op == "set":
                        db.data.setdefault("catalog", {})[ref.doc_id] = data
                    else:
                        db.data.get("catalog", {}).pop(ref.doc_id, None)
                self.ops.clear()

        b = Batch()
        b.ops = []
        return b


def test_firestore_catalog_in_chunks():
    from monitor.storage import FirestoreStorage

    s = FirestoreStorage.__new__(FirestoreStorage)
    s.db, s._loaded, s._chunks = FakeFirestore(), {}, {}
    s.CHUNK_BYTES = 2_000  # trozos pequeños para que haya varios

    products = {str(i): product(str(i), [("S", True), ("M", i % 2 == 0)], title=f"TEE {i}") for i in range(30)}
    assert s.load_state("shop") == {}
    s.save_state("shop", products)
    n_chunks = len(s.db.data["catalog"])
    assert n_chunks > 1 and s.db.writes == n_chunks

    # Releer: una lectura por trozo, no por prenda, y el mismo catálogo.
    s2 = FirestoreStorage.__new__(FirestoreStorage)
    s2.db, s2._loaded, s2._chunks = s.db, {}, {}
    s2.CHUNK_BYTES = s.CHUNK_BYTES
    s.db.reads = s.db.writes = 0
    assert s2.load_state("shop") == products
    assert s.db.reads == n_chunks

    # Sin cambios no se escribe nada; con un cambio, solo su trozo.
    s2.save_state("shop", products)
    assert s.db.writes == 0
    changed = dict(products)
    changed["5"] = product("5", [("S", False), ("M", False)], title="TEE 5")
    s2.save_state("shop", changed)
    assert s.db.writes == 1


def test_transient_errors_do_not_fail_the_run():
    from monitor.main import is_transient

    def http_error(code):
        req = httpx.Request("GET", "https://shop.test")
        return httpx.HTTPStatusError("x", request=req, response=httpx.Response(code, request=req))

    class ResourceExhausted(Exception):
        pass

    assert is_transient(http_error(429)) and is_transient(http_error(503))
    assert is_transient(ResourceExhausted("Quota exceeded"))
    assert not is_transient(http_error(404))
    assert not is_transient(KeyError("id"))
