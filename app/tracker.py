"""Adding, refreshing and removing tracked products."""
import uuid
from datetime import datetime, timedelta, timezone

from . import config, demo, offers, resolve, serpapi
from .store import Settings, Store

MAX_HISTORY = 200
DAILY = timedelta(days=1)
store = Store(config.data_dir() / "products.json")
settings = Settings(config.data_dir() / "settings.json")


class TrackerError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def demo_mode():
    return not config.serpapi_key()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check_prices(name):
    key = config.serpapi_key()
    try:
        raw = serpapi.search(name, key, config.country()) if key else demo.search(name)
    except serpapi.SerpApiError as e:
        raise TrackerError(f"Price lookup failed: {e}", 502) from e
    return offers.build(raw, name)


def _apply(product, result):
    product.update(result, checked_at=_now(), demo=demo_mode())
    if result["lowest"]:
        product["history"].append(
            {"at": product["checked_at"], "price": result["lowest"]["price"], "store": result["lowest"]["store"]}
        )
        product["history"] = product["history"][-MAX_HISTORY:]
    return product


def list_products():
    return store.all()


def _get(product_id):
    product = store.get(product_id)
    if product is None:
        raise TrackerError("Product not found", 404)
    return product


def _find_by_name(name, exclude=None):
    key = name.casefold()
    return next((p for p in store.all() if p["name"].casefold() == key and p["id"] != exclude), None)


def add_product(query):
    """Return (product, created). Adding a name already tracked re-checks that product instead."""
    try:
        name, source_url, image = resolve.product_name(query)
    except resolve.ResolveError as e:
        raise TrackerError(str(e), 422) from e
    if existing := _find_by_name(name):
        return refresh_product(existing["id"]), False
    product = {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "source_url": source_url,
        "image": image,
        "created_at": _now(),
        "target_price": None,
        "history": [],
    }
    _apply(product, _check_prices(name))
    store.upsert(product)
    return product, True


def refresh_product(product_id):
    product = _get(product_id)
    _apply(product, _check_prices(product["name"]))
    store.upsert(product)
    return product


def update_product(product_id, changes):
    """Apply validated `name` / `target_price` changes. A new name is a new search, so history restarts."""
    product = _get(product_id)
    if "target_price" in changes:
        product["target_price"] = changes["target_price"]
    if "name" in changes and changes["name"] != product["name"]:
        if _find_by_name(changes["name"], exclude=product_id):
            raise TrackerError(f"You're already tracking “{changes['name']}”.", 409)
        result = _check_prices(changes["name"])
        product.update(name=changes["name"], history=[])
        _apply(product, result)
    store.upsert(product)
    return product


def refresh_due(now=None):
    """Re-check every product last checked a day or more ago. Returns the ids re-checked."""
    now = now or datetime.now(timezone.utc)
    checked = []
    for product in store.all():
        if now - datetime.fromisoformat(product["checked_at"]) < DAILY:
            continue
        try:
            refresh_product(product["id"])
        except TrackerError as e:
            print(f"Daily check skipped {product['name']!r}: {e}", flush=True)
            continue
        checked.append(product["id"])
    return checked


def delete_product(product_id):
    if not store.delete(product_id):
        raise TrackerError("Product not found", 404)
