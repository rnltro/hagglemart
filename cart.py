"""Pure cart logic — no Streamlit, no I/O. Fully unit-testable."""
import re

from haggle_bot import money_fmt

MAX_QTY = 9

CANCEL_RE = re.compile(
    r"\b(cancel|remove|drop|delete|reduce|lower|fewer|less|clear|empty|start over|"
    r"just|only|change my mind|don'?t want|do not want)\b", re.I)
CLEAR_RE = re.compile(r"(everything|all|cart|deal|start over|clear|empty)", re.I)
CHECKOUT_CANCEL_RE = re.compile(r"(cancel|checkout|order|payment|paypal|stop|abort)", re.I)
REDUCE_RE = re.compile(r"(only|just|reduce|lower|less|fewer|change|set)", re.I)


def new_line(product: dict, price: float, qty: int = 1) -> dict:
    return {"product_id": product["id"], "item": product["name"],
            "price": round(float(price), 2), "qty": int(qty),
            "currency": product.get("currency", "USD")}


def cart_total(deal: list) -> float:
    return round(sum(round(l["price"], 2) * int(l["qty"]) for l in deal), 2)


def cart_currency(deal: list) -> str:
    return deal[0].get("currency", "USD") if deal else "USD"


def deal_summary(deal: list) -> str:
    return " + ".join(f"{l['qty']}× {l['item']}" for l in deal)


def find_line(deal: list, text: str) -> int:
    t = (text or "").lower()
    for i, l in enumerate(deal):
        words = [w for w in re.split(r"\W+", l["item"].lower()) if len(w) > 3]
        if any(w in t for w in words):
            return i
    return -1


def apply_accept(deal: list, product: dict, price: float, qty: int = 1):
    """Merge an agreed price/qty into the deal. Returns (deal, note).
    Caps qty to MAX_QTY and to the product's stock (if tracked)."""
    qty = max(1, min(MAX_QTY, int(qty or 1)))
    note = ""
    stock = product.get("stock")
    if stock is not None:
        stock = int(stock)
        if stock <= 0:
            return deal, f"{product['name']} is sold out."
        if qty > stock:
            qty = stock
            note = f"only {stock} left — quantity capped"
    line = next((l for l in deal if l["product_id"] == product["id"]), None)
    if line:
        line["price"] = round(float(price), 2)
        if qty > 1:
            line["qty"] = min(MAX_QTY, qty)
        line["qty"] = max(1, line["qty"])
    else:
        deal.append(new_line(product, price, qty))
    return deal, note


def maybe_edit(deal: list, text: str, pending: bool = False):
    """Cancel/reduce chat commands. Returns None if `text` isn't a cart-edit
    command; otherwise (reply_text, flags). Mutates deal in place."""
    low = (text or "").lower().strip()
    if not CANCEL_RE.search(low):
        return None

    idx = find_line(deal, text)
    if idx >= 0:
        line = deal[idx]
        num = re.search(r"(\d+)", text)
        if REDUCE_RE.search(low) and num:
            n = max(0, min(MAX_QTY, int(num.group(1))))
            if n == 0:
                deal.pop(idx)
                return f"✅ Removed **{line['item']}** from your deal.", {"edited": True}
            line["qty"] = n
            return (f"✅ Updated — **{line['item']}** ×{n}. "
                    f"New total **{money_fmt(cart_total(deal), cart_currency(deal))}**.",
                    {"edited": True})
        deal.pop(idx)
        reply = (f"✅ Removed **{line['item']}**."
                 + (f" New total **{money_fmt(cart_total(deal), cart_currency(deal))}**." if deal
                    else " Your deal is empty now."))
        return reply, {"edited": True}

    if CLEAR_RE.search(low):
        had = len(deal) > 0 or pending
        deal.clear()
        return ("🧺 Done — deal cleared and checkout canceled. **Nothing was charged.**"
                if had else
                "Nothing to cancel — your deal list is already empty."), {"cleared": True}

    if pending and CHECKOUT_CANCEL_RE.search(low):
        return ("✖️ Checkout canceled — the PayPal order was abandoned **before approval, "
                "so nothing was charged**. Your deal is still saved."), {"canceled_checkout": True}

    if deal:
        names = ", ".join(f"{l['qty']}× {l['item']}" for l in deal)
        return (f"Sure — which item should I remove or change? Your deal: **{names}**. "
                f"Try *\"remove the coffee\"* or *\"just 1 camera\"*."), {"which": True}

    return ("Nothing to cancel — you haven't agreed on any items yet. "
            "Make an offer and we'll start there!"), {"nothing": True}
