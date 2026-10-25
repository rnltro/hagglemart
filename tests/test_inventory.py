import json

import inventory


def _seed(tmp_path, monkeypatch, products):
    f = tmp_path / "products.json"
    f.write_text(json.dumps(products))
    monkeypatch.setenv("PRODUCTS_FILE", str(f))


def test_commit_sale_decrements_tracked_stock(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [{"id": "cam", "stock": 5}, {"id": "cof", "stock": None}])
    changed = inventory.commit_sale([{"product_id": "cam", "qty": 2}])
    assert changed == {"cam": 3}
    assert inventory.stock_of("cam") == 3


def test_commit_clamps_at_zero(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [{"id": "cam", "stock": 1}])
    inventory.commit_sale([{"product_id": "cam", "qty": 3}])
    assert inventory.stock_of("cam") == 0


def test_commit_skips_unlimited_and_unknown(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [{"id": "cof", "stock": None}])
    changed = inventory.commit_sale([{"product_id": "cof", "qty": 5},
                                     {"product_id": "ghost", "qty": 1}])
    assert changed == {} and inventory.stock_of("cof") is None


def test_mailer_disabled_without_smtp(monkeypatch):
    import mailer
    monkeypatch.delenv("SMTP_HOST", raising=False)
    ok, why = mailer.email_receipt({"order_id": "x"}, "M", "a@b.c")
    assert ok is False and "SMTP" in why
