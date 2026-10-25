import random

import haggle_bot as hb


def product(**over):
    p = {"id": "cam", "name": "Vintage Camera", "price": 180.0,
         "floor_price": 140.0, "currency": "USD", "faq": {}, "stock": None}
    p.update(over)
    return p


def test_pure_number_is_an_offer():
    r = hb.get_bot_reply("150", hb.new_bot_state(product()), product())
    assert r["action"] in ("counter", "accept")


def test_first_reply_never_accepts_a_lowball():
    for offer in ("$1", "10", "50", "100"):
        r = hb.get_bot_reply(f"I offer {offer}", hb.new_bot_state(product()), product())
        if r["action"] == "accept":
            assert r["price"] >= 140.0


def test_floor_is_never_breached_over_a_long_haggle():
    for seed in range(30):
        random.seed(seed)
        s = hb.new_bot_state(product())
        for _ in range(12):
            offer = round(random.uniform(20, 180), 2)
            r = hb.get_bot_reply(f"{offer}", s, product())
            s = r["state"]
            if r["action"] == "accept":
                assert r["price"] >= 140.0 - 1e-9
                break
            if r["action"] == "counter":
                assert 140.0 <= r["price"] <= 180.0


def test_agreement_after_counter_accepts_the_counter():
    r = hb.get_bot_reply("Would you take $150?", hb.new_bot_state(product()), product())
    if r["action"] == "counter":
        r2 = hb.get_bot_reply("deal", r["state"], product())
        assert r2["action"] == "accept" and r2["price"] == r["price"]


def test_probing_lowers_monotonically_and_never_below_floor():
    s = hb.new_bot_state(product())
    seen = []
    for _ in range(8):
        r = hb.get_bot_reply("what's your best price?", s, product())
        s = r["state"]
        if r["action"] == "counter":
            seen.append(r["price"])
        if s.get("final"):
            break
    assert seen == sorted(seen, reverse=True)
    assert seen[-1] == 140.0


def test_randomized_paths_vary_between_sessions():
    paths = {tuple(hb.new_bot_state(product())["path"]) for _ in range(20)}
    assert len(paths) > 1


def test_path_endpoints_are_ask_and_floor():
    st = hb.new_bot_state(product())
    assert st["path"][0] == 180.0 and st["path"][-1] == 140.0


def test_faq_hit():
    r = hb.get_bot_reply("when will it ship?", hb.new_bot_state(product()), product())
    assert r["action"] == "info" and "business days" in r["text"]


def test_two_for_total_at_asking_accepts_with_qty_2():
    r = hb.get_bot_reply("2 for $400", hb.new_bot_state(product()), product())
    assert r["action"] == "accept" and r["price"] == 180.0 and r["qty"] == 2


def test_each_keyword_means_per_unit_and_respects_floor():
    r = hb.get_bot_reply("3 for $120 each", hb.new_bot_state(product()), product())
    assert r["action"] == "counter" and r["price"] >= 140.0


def test_deal_after_qty_counter_carries_qty():
    r = hb.get_bot_reply("2 for $300", hb.new_bot_state(product()), product())
    if r["action"] == "counter":
        r2 = hb.get_bot_reply("deal", r["state"], product())
        assert r2["action"] == "accept" and r2["qty"] == 2 and r2["price"] == r["price"]


def test_stock_caps_accepted_qty():
    r = hb.get_bot_reply("2 for $400", hb.new_bot_state(product(stock=1)), product(stock=1))
    assert r["action"] == "accept" and r["qty"] == 1 and "capped" in r["text"]


def test_sold_out_never_accepts():
    r = hb.get_bot_reply("I'll pay full price $180", hb.new_bot_state(product(stock=0)),
                         product(stock=0))
    assert r["action"] == "info" and "sold out" in r["text"].lower()


def test_fixed_price_item_refuses_then_accepts_at_ask():
    fx = product(floor_price=120.0)
    r = hb.get_bot_reply("Would you take $100?", hb.new_bot_state(fx), fx)
    assert r["action"] == "info"
    r2 = hb.get_bot_reply("$120", r["state"], fx)
    assert r2["action"] == "accept" and r2["price"] == 120.0
