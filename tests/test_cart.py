import cart

CAM = {"id": "cam", "name": "Vintage Camera", "price": 180.0, "currency": "USD", "stock": 5}
COF = {"id": "cof", "name": "Coffee Bundle", "price": 45.0, "currency": "USD", "stock": 12}


def line(p, qty=1, price=None):
    return cart.new_line(p, price if price is not None else p["price"], qty)


def test_total_currency_summary():
    deal = [line(CAM, 1), line(COF, 2, 40.0)]
    assert cart.cart_total(deal) == 260.0
    assert cart.cart_currency(deal) == "USD"
    assert cart.deal_summary(deal) == "1× Vintage Camera + 2× Coffee Bundle"


def test_apply_accept_creates_then_merges():
    deal = []
    cart.apply_accept(deal, CAM, 150.0, 1)
    assert deal[0]["price"] == 150.0 and deal[0]["qty"] == 1
    cart.apply_accept(deal, CAM, 145.0, 1)
    assert len(deal) == 1 and deal[0]["price"] == 145.0
    cart.apply_accept(deal, COF, 40.0, 2)
    assert len(deal) == 2 and deal[1]["qty"] == 2


def test_apply_accept_caps_to_stock():
    deal = []
    _, note = cart.apply_accept(deal, dict(CAM, stock=1), 150.0, 3)
    assert deal[0]["qty"] == 1 and "only 1 left" in note


def test_apply_accept_caps_to_max_qty():
    deal = []
    cart.apply_accept(deal, dict(CAM, stock=None), 150.0, 99)
    assert deal[0]["qty"] == cart.MAX_QTY


def test_maybe_edit_ignores_normal_chat():
    deal = [line(CAM)]
    assert cart.maybe_edit(deal, "Would you take $150?") is None


def test_remove_by_name():
    deal = [line(CAM), line(COF)]
    _, flags = cart.maybe_edit(deal, "remove the coffee")
    assert flags.get("edited") and len(deal) == 1 and deal[0]["product_id"] == "cam"


def test_reduce_to_n():
    deal = [line(CAM, 3)]
    _, flags = cart.maybe_edit(deal, "just 1 camera please")
    assert flags.get("edited") and deal[0]["qty"] == 1


def test_reduce_to_zero_removes_line():
    deal = [line(CAM, 2)]
    _, flags = cart.maybe_edit(deal, "just 0 cameras")
    assert flags.get("edited") and deal == []


def test_clear_everything():
    deal = [line(CAM)]
    _, flags = cart.maybe_edit(deal, "cancel everything")
    assert flags.get("cleared") and deal == []


def test_cancel_checkout_keeps_deal():
    deal = [line(CAM)]
    _, flags = cart.maybe_edit(deal, "cancel the paypal order", pending=True)
    assert flags.get("canceled_checkout") and len(deal) == 1


def test_nothing_to_cancel_when_empty():
    deal = []
    reply, flags = cart.maybe_edit(deal, "cancel")
    assert flags.get("nothing") and "haven't agreed" in reply


def test_find_line_word_matching():
    deal = [line(COF)]
    assert cart.find_line(deal, "drop the coffee") == 0
    assert cart.find_line(deal, "remove the camera") == -1
