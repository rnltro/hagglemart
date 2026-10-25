"""Stock tracking backed by products.json. Missing 'stock' = unlimited."""
import json
import os
import threading

_lock = threading.Lock()


def _path():
    return os.getenv("PRODUCTS_FILE", "products.json")


def _load():
    try:
        with open(_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save(products) -> bool:
    try:
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump(products, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


def stock_of(product_id: str):
    """int stock, or None = unlimited/unknown."""
    for p in _load():
        if p.get("id") == product_id:
            return p.get("stock")
    return None


def commit_sale(items: list) -> dict:
    """items: [{'product_id':..., 'qty':...}, ...] from a receipt.
    Decrements tracked stock (clamped at 0). Returns {product_id: new_stock}."""
    changed = {}
    with _lock:
        products = _load()
        for it in items or []:
            pid = it.get("product_id")
            q = int(it.get("qty", 1) or 1)
            if not pid:
                continue
            for p in products:
                if p.get("id") == pid and p.get("stock") is not None:
                    p["stock"] = max(0, int(p["stock"]) - q)
                    changed[pid] = p["stock"]
        if changed:
            _save(products)
    return changed
