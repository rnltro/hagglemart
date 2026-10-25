import json
import os
import re
import uuid

import streamlit as st
import streamlit.components.v1 as components

import cart
import fulfillment
import mailer
import poller
import store
import webhook_server
from haggle_bot import bundle_total, get_bot_reply, money_fmt, new_bot_state, suggest_offers
from paypal_client import PayPalClient, PayPalError, receipt_from_order
from receipt import receipt_html, receipt_pdf

st.set_page_config(page_title="HaggleMart — haggle, pay, done", page_icon="🤝", layout="centered")

PAGES = ["🛍️ Shop", "💬 Haggle & Checkout", "🧾 Receipts"]
PRODUCTS_FILE = "products.json"
MAX_QTY = cart.MAX_QTY
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# ---------------------------------------------------------------- config
def _load_secrets():
    try:
        return dict(st.secrets)
    except Exception:
        return {}

_SEC = _load_secrets()

def _cfg(key, default=""):
    v = _SEC.get(key)
    if v in (None, ""):
        v = os.getenv(key, default)
    return v

MERCHANT_NAME  = str(_cfg("MERCHANT_NAME", "HaggleMart"))
MERCHANT_PIN   = str(_cfg("MERCHANT_PIN", ""))          # merchant area is LOCKED without it
PUBLIC_APP_URL = str(_cfg("PUBLIC_APP_URL", "")).rstrip("/")

DEFAULT_PRODUCTS = [
    {"id": "film-camera", "name": "Vintage Film Camera (35mm SLR)",
     "description": "Fully serviced, new light seals, 50mm lens included. Test roll shot great.",
     "price": 180.0, "floor_price": 140.0, "stock": 5, "currency": "USD",
     "faq": {"shipping": "Ships in 1–2 business days with tracked delivery — the tracking number is sent right after payment.",
             "returns": "30-day returns. If it's not as described, return shipping is on me; otherwise the item just needs to come back in the same condition.",
             "condition": "Fully serviced: new light seals, cleaned lens, and an accurate light meter. A test roll came out great — see the listing photos.",
             "film": "It uses standard 35mm film — any brand works.",
             "battery": "Runs on one LR44 battery for the light meter; included."}},
    {"id": "coffee-bundle", "name": "Small-Batch Coffee Bundle (3 × 250g)",
     "description": "Roasted this week — choose your profile: bright, balanced, or dark.",
     "price": 45.0, "floor_price": 36.0, "stock": 12, "currency": "USD",
     "faq": {"shipping": "Roasted this week and ships same or next business day — typically 2–4 days door to door.",
             "returns": "14-day returns, no questions asked — just keep the bags sealed.",
             "condition": "Fresh roast — sealed within hours of roasting, never stale stock."}},
    {"id": "art-print", "name": "Limited Art Print (A2, signed)",
     "description": "Archival pigment ink on 300gsm cotton rag. Edition of 50, signed & numbered.",
     "price": 120.0, "floor_price": 90.0, "stock": 4, "currency": "USD",
     "faq": {"shipping": "Flat-packed with rigid backing and shipped with tracking within 2 business days.",
             "returns": "7-day returns — if it arrives damaged, I cover return shipping both ways.",
             "condition": "Mint condition — edition of 50, signed and numbered, stored flat since purchase."}},
]

def load_products():
    try:
        with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list) and data:
                return data
    except Exception:
        pass
    return [dict(p) for p in DEFAULT_PRODUCTS]

def save_products(products):
    try:
        with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
            json.dump(products, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False

# ----------------- runtime: DB + webhook + poller + email env bridge -----------------
@st.cache_resource
def get_runtime():
    if _cfg("DB_PATH"):
        store.set_db_path(str(_cfg("DB_PATH")))
    store.init_db()

    # bridge secrets → env so webhook/poller threads and the mailer see them
    for k in ("MERCHANT_NAME", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SMTP_FROM"):
        os.environ.setdefault(k, str(_cfg(k) or ""))

    def client_factory():
        cid, sec = _cfg("PAYPAL_CLIENT_ID"), _cfg("PAYPAL_SECRET")
        return PayPalClient(cid, sec, str(_cfg("PAYPAL_MODE", "sandbox")).lower()) \
            if (cid and sec) else None

    flags = {"webhook": False, "poller": False}
    if str(_cfg("ENABLE_POLLER", "true")).lower() == "true":
        try:
            poller.start_poller(client_factory, interval=int(_cfg("POLLER_INTERVAL", "60") or 60))
            flags["poller"] = True
        except Exception:
            pass
    insecure = str(_cfg("PAYPAL_INSECURE_WEBHOOKS", "false")).lower() == "true"
    if str(_cfg("ENABLE_WEBHOOK", "true")).lower() == "true" \
            and (_cfg("PAYPAL_WEBHOOK_ID") or insecure):
        try:
            webhook_server.start_webhook_server(
                client_factory=client_factory,
                webhook_id=str(_cfg("PAYPAL_WEBHOOK_ID", "")),
                port=int(_cfg("WEBHOOK_PORT", "8600") or 8600),
                insecure=insecure)
            flags["webhook"] = True
        except Exception:
            pass
    return flags

RUNTIME = get_runtime()

ss = st.session_state
for k, v in {"chat": [], "bot": None, "product_id": None, "deal": [], "pending": None,
             "last_receipt": None, "nav": PAGES[0], "processed_tokens": set(),
             "merchant_ok": False, "picker": False, "buyer_email": ""}.items():
    ss.setdefault(k, v)

# ---------------- auto-hidden side panel (buyer mode) ----------------
# The side panel is a shop-owner tool: while the Merchant area is locked, the
# whole sidebar — including the expand arrow — is hidden so buyers get the
# full window on every page. Without a configured MERCHANT_PIN there is no way
# to unlock, so the panel stays visible as a safe fallback (nothing inside is
# buyer-hostile since it was reduced to guide + status + reset).
_sidebar_visible = bool(ss.get("merchant_ok")) or not MERCHANT_PIN
if not _sidebar_visible:
    st.markdown(
        "<style>[data-testid='stSidebar'],[data-testid='stSidebarCollapsedControl'],"
        "[data-testid='stSidebarCollapseButton'],[data-testid='stExpandSidebarButton']"
        "{display:none !important;}</style>",
        unsafe_allow_html=True)

def paypal_client():
    cid = ss.get("sid_client_id") or _cfg("PAYPAL_CLIENT_ID")
    sec = ss.get("sid_client_secret") or _cfg("PAYPAL_SECRET")
    mode = (ss.get("sid_mode") or _cfg("PAYPAL_MODE", "sandbox")).lower()
    if not (cid and sec):
        return None
    return PayPalClient(cid, sec, mode)

def app_base_url():
    return "http://localhost:8501"

def current_product():
    return next((p for p in load_products() if p["id"] == ss["product_id"]), None)

def merchant_ok():
    return bool(MERCHANT_PIN) and bool(ss.get("merchant_ok"))

def cart_total():
    return cart.cart_total(ss["deal"])

def cart_currency():
    return cart.cart_currency(ss["deal"])

def deal_summary():
    return cart.deal_summary(ss["deal"])

def asking_of(line):
    p = next((q for q in load_products() if q["id"] == line["product_id"]), None)
    return round(float(p["price"]), 2) if p else round(float(line["price"]), 2)

def total_savings():
    return round(sum(max(0.0, asking_of(l) - l["price"]) * int(l["qty"]) for l in ss["deal"]), 2)

def _stock_problem(deal, products):
    for l in deal:
        p = next((q for q in products if q["id"] == l["product_id"]), None)
        if p and p.get("stock") is not None and int(l["qty"]) > int(p["stock"]):
            return (f"Only {int(p['stock'])} × {p['name']} left in stock — "
                    f"reduce the quantity in your deal first.")
    return None

def add_product_to_deal(q) -> bool:
    stock = q.get("stock")
    if stock is not None and int(stock) <= 0:
        st.toast(f"{q['name']} is sold out.", icon="🚫")
        return False
    if ss["pending"]:
        ss["pending"] = None
        st.toast("Checkout canceled (nothing was charged) — item added to your deal.", icon="✖️")
    line = next((l for l in ss["deal"] if l["product_id"] == q["id"]), None)
    if line:
        cap = MAX_QTY if stock is None else min(MAX_QTY, int(stock))
        if line["qty"] >= cap:
            st.toast(f"Only {cap} × {q['name']} available.", icon="⚠️")
            return False
        line["qty"] += 1
        st.toast(f"Added another ×1 — {q['name']}", icon="➕")
    else:
        ss["deal"].append(cart.new_line(q, q["price"], 1))
        st.toast("Added at asking price — tap 💬 on the cart line to haggle it lower.", icon="🧺")
    return True

# ---------------------------------------------------------------- chat flow
def process_message(text: str):
    p = current_product()
    if p is None:
        st.toast("Pick an item in the Shop tab first!", icon="🛍️")
        return

    ss["chat"].append({"role": "user", "text": text})

    # ---- email capture (anonymous buyers): an @-address in chat is a receipt destination
    m_email = EMAIL_RE.search(text or "")
    if m_email:
        ss["buyer_email"] = m_email.group(0).lower()
        ss["chat"].append({"role": "assistant", "text":
            f"📧 Got it — your receipt will go to **{ss['buyer_email']}**. "
            f"It's attached as a PDF, and you can always download it here too."})
        return

    # ---- instant FAQ (quick-action buttons & related questions): answers come
    #      straight from the product's faq dict and never consume a haggle round.
    #      Checked BEFORE cart editing so questions like "what's the return
    #      policy?" are never mistaken for a "return the item" cart edit.
    q = (text or "").strip().lower()
    _faq = p.get("faq") or {}
    _hit = None
    if any(w in q for w in ("ship", "deliver", "dispatch", "tracking", "arrive")):
        _hit = "shipping"
    elif any(w in q for w in ("return", "refund", "exchange", "money back")):
        _hit = "returns"
    elif any(w in q for w in ("condition", "wear", "used", "defect", "scratch",
                              "damage", "flaw", "broken", "working")):
        _hit = "condition"
    elif "film" in q:
        _hit = "film"
    elif "battery" in q:
        _hit = "battery"
    if _hit:
        _answer = _faq.get(_hit) or {
            "shipping": "Ships within 1–2 business days with tracked delivery — "
                        "the tracking number is sent right after payment.",
            "returns": "30-day returns — if it's not as described, return shipping "
                       "is on me; otherwise it just needs to come back as received.",
            "condition": "It's in the condition shown in the listing — fully working "
                         "and as pictured. Ask me about any specific detail!",
        }.get(_hit)
        if _answer:
            ss["chat"].append({"role": "assistant", "text": _answer})
            return

    # ---- cart editing / cancellation
    edit = cart.maybe_edit(ss["deal"], text, pending=bool(ss["pending"]))
    if edit is not None:
        reply, flags = edit
        if flags.get("edited") or flags.get("cleared"):
            if ss["pending"]:
                ss["pending"] = None
                st.toast("PayPal order canceled — create a new one with the updated total.", icon="✖️")
        ss["chat"].append({"role": "assistant", "text": reply})
        return

    # ---- normal bot reply
    if ss["bot"] is None:
        ss["bot"] = new_bot_state(p)
    reply = get_bot_reply(text, ss["bot"], p)
    ss["bot"] = reply["state"]
    ss["chat"].append({"role": "assistant", "text": reply["text"]})

    if reply["action"] == "accept":
        ss["deal"], note = cart.apply_accept(ss["deal"], p, reply["price"], reply.get("qty", 1))
        if note:
            st.toast(note, icon="⚠️")
        if ss["pending"]:
            ss["pending"] = None
            st.toast("Checkout canceled (nothing was charged) — new price applied. "
                     "Create a new order when ready.", icon="✖️")
        else:
            st.toast(f"Deal updated — running total {money_fmt(cart_total(), cart_currency())}",
                     icon="🤝")

def finish_receipt(order):
    rec = receipt_from_order(order)
    rec["buyer_email"] = rec.get("buyer_email") or ss.get("buyer_email") or ""
    if ss.get("deal"):
        rec["items"] = [{"product_id": l["product_id"], "name": l["item"],
                         "qty": int(l["qty"]), "price": round(float(l["price"]), 2)}
                        for l in ss["deal"]]
        rec["item"] = deal_summary()[:120]
    rec["amount_str"] = f"{rec['amount']} {rec['currency']}".strip()
    store.mark_completed(rec)
    fulfillment.fulfill(rec)      # stock decrement + receipt email (safe no-ops)
    ss["last_receipt"] = rec
    ss["pending"] = None
    ss["deal"] = []
    ss["nav"] = PAGES[2]

def handle_paypal_return(token):
    client = paypal_client()
    if not client:
        st.error("PayPal is not configured, so I can't verify this payment.")
        return
    row = store.get_order(token) or {}
    try:
        order = client.get_order(token)
        status = order.get("status")
        if status == "COMPLETED":
            finish_receipt(order)
            return
        if status == "APPROVED":
            try:
                rid = row.get("request_id") or str(uuid.uuid4())
                finish_receipt(client.capture_order(token, request_id=rid))
            except PayPalError as e:
                if "ALREADY_CAPTURED" in e.body.upper():
                    finish_receipt(client.get_order(token))
                elif "INSTRUMENT_DECLINED" in e.body.upper():
                    st.warning("Your payment method was declined in PayPal. Re-open the "
                               "PayPal link (Haggle & Checkout tab) and try another one.")
                else:
                    raise
            return
        ss["pending"] = None  # CREATED → buyer never approved
        st.toast("Payment wasn't completed — you can try checkout again anytime.", icon="⚠️")
    except PayPalError as e:
        st.error(f"PayPal error: {e}")

# PayPal redirects back with ?token=<order id> → capture server-side
token = st.query_params.get("token")
if token and token not in ss["processed_tokens"]:
    ss["processed_tokens"].add(token)
    handle_paypal_return(token)
    try:
        st.query_params.clear()
    except Exception:
        pass
    st.rerun()

# ---------------------------------------------------------------- ui patches

def offer_tag(v: float, price: float) -> str:
    """Quick-offer chip strategy label: 85% of asking -> "Start low",
    90% -> "Fair", 95% -> "Quick close". Derived from the VALUE (not the
    loop position) so labels stay correct when suggest_offers() drops a
    chip that falls below the seller's floor."""
    f = (v / price) if price else 0.95
    if f < 0.875:
        return "Start low"
    if f < 0.925:
        return "Fair"
    return "Quick close"


def _render_receipt(r: dict, open_card: bool = False) -> None:
    """One receipt card: expander header, receipt HTML, download buttons and
    merchant refund actions. Used once for the latest receipt and again
    (top level, closed by default) for older ones."""
    badge = "🔁 REFUNDED" if r.get("status") == "REFUNDED" else "✅"
    em_note = f" · 📧 {r.get('buyer_email')}" if r.get("buyer_email") else " · no email"
    label = (f"{badge} {r['item']} — {r.get('amount_str') or (str(r.get('amount','')) + ' ' + r.get('currency',''))}"
             f" · {str(r.get('time',''))[:19].replace('T', ' ')} UTC{em_note}")
    with st.expander(label, expanded=open_card):
        components.html(receipt_html(r, MERCHANT_NAME), height=760, scrolling=True)
        d1, d2 = st.columns(2)
        d1.download_button("📄 Save as PDF", data=receipt_pdf(r, MERCHANT_NAME),
                           file_name=f"receipt_{r['order_id']}.pdf", mime="application/pdf",
                           key=f"pdf_{r['order_id']}", use_container_width=True)
        d2.download_button("⬇️ Download HTML", data=receipt_html(r, MERCHANT_NAME),
                           file_name=f"receipt_{r['order_id']}.html", mime="text/html",
                           key=f"html_{r['order_id']}", use_container_width=True)

        if merchant_ok() and r.get("capture_id"):
            st.divider()
            st.markdown("🛠️ **Merchant actions**")
            if r.get("status") == "COMPLETED":
                rc1, rc2 = st.columns([1, 2])
                full = float(r.get("amount") or 0)
                amt = rc1.number_input("Refund amount", min_value=0.01,
                                       max_value=max(full, 0.01), value=full, step=0.01,
                                       key=f"rf_{r['order_id']}")
                note = rc2.text_input("Note to payer (optional)", key=f"rn_{r['order_id']}")
                if st.button(f"↩️ Issue refund — {amt:.2f} {r.get('currency','USD')}",
                             type="primary", key=f"rb_{r['order_id']}"):
                    client = paypal_client()
                    if client is None:
                        st.error("PayPal is not configured.")
                    else:
                        try:
                            resp = client.refund_capture(r["capture_id"], amount_value=amt,
                                                         currency=r.get("currency", "USD"),
                                                         note=note or None,
                                                         request_id=str(uuid.uuid4()))
                            store.mark_refunded(r["order_id"], resp.get("id", ""))
                            st.success(f"Refunded {amt:.2f} {r.get('currency','USD')} · "
                                       f"refund id `{resp.get('id','—')}`")
                            st.rerun()
                        except PayPalError as e:
                            if "ALREADY_REFUNDED" in e.body.upper() or "CAPTURE_FULLY_REFUNDED" in e.body.upper():
                                store.mark_refunded(r["order_id"], "existing")
                                st.warning("Capture was already refunded — status updated.")
                                st.rerun()
                            else:
                                st.error(f"Refund failed: {e}")


# ---------------------------------------------------------------- chat input (top-level)
prompt = st.chat_input("Offer, question, or “remove the coffee”…",
                       disabled=ss["product_id"] is None)
if prompt:
    process_message(prompt)
    st.rerun()

# ---------------------------------------------------------------- UI
st.title("🤝 HaggleMart")
st.caption(f"{MERCHANT_NAME} · offer → negotiate → pay with PayPal → instant receipt")

if ss.get("last_receipt"):
    st.success("✅ Payment confirmed by PayPal — receipt generated — see the 🧾 Receipts tab.")

_nav_idx = PAGES.index(ss["nav"]) if ss["nav"] in PAGES else 0
_nav_choice = st.radio("Navigation", PAGES, index=_nav_idx,
                       horizontal=True, label_visibility="collapsed")
if _nav_choice != ss["nav"]:
    ss["nav"] = _nav_choice
products = load_products()

# ---------------- SHOP ----------------
if ss["nav"] == PAGES[0]:
    left, right = st.columns([2, 1])
    left.subheader("🛍️ Today's items")
    right.caption("Agree a price with the bot — it flows into your deal cart.")

    cols = st.columns(3)
    for i, p in enumerate(products):
        in_deal = any(l["product_id"] == p["id"] for l in ss["deal"])
        stock = p.get("stock")
        with cols[i % 3]:
            with st.container(border=True):
                st.subheader(p["name"])
                st.write(p.get("description", ""))
                price_line = f"**Asking:** {money_fmt(float(p['price']), p.get('currency', 'USD'))}"
                if stock is not None:
                    price_line += (" · 🔴 **Sold out**" if int(stock) <= 0
                                   else f" · **{int(stock)} left**")
                st.markdown(price_line)
                if stock is not None and int(stock) <= 0:
                    st.button("Sold out", key=f"pick_{p['id']}", disabled=True,
                              use_container_width=True)
                elif st.button("💬 Haggle price" if in_deal else "💬 Haggle this",
                               key=f"pick_{p['id']}", use_container_width=True):
                    ss["product_id"] = p["id"]
                    ss["chat"], ss["bot"] = [], new_bot_state(p)
                    ss["picker"] = False
                    ss["nav"] = PAGES[1]
                    st.rerun()

    # ---- deal bar (always visible) with Add items toggle
    sav = total_savings()
    bar_l, bar_m, bar_r = st.columns([3, 1, 1])
    if ss["deal"]:
        bar_l.markdown(
            f"🧺 **Your deal:** {deal_summary()} — **{money_fmt(cart_total(), cart_currency())}**"
            + (f" · 🟢 saved {money_fmt(sav, cart_currency())}" if sav > 0 else ""))
    else:
        bar_l.markdown("🧺 Your deal is empty — add items or haggle one below.")
    if ss["pending"]:
        bar_m.markdown("🟡 *checkout in progress*")
    elif bar_m.button("➕ Add items", use_container_width=True):
        ss["picker"] = not ss["picker"]
        st.rerun()
    if ss["deal"] and bar_r.button("💬 Go to checkout", type="primary", use_container_width=True):
        ss["picker"] = False
        ss["nav"] = PAGES[1]
        st.rerun()

    if ss["picker"] and not ss["pending"]:
        others = [q for q in products if not any(l["product_id"] == q["id"] for l in ss["deal"])]
        if not others:
            st.caption("All products are already in your deal — tap 💬 on a line to haggle its price. 🎉")
        for q in others:
            stock = q.get("stock")
            note = ""
            if stock is not None:
                note = " · sold out" if int(stock) <= 0 else f" · {int(stock)} left"
            c1, c2 = st.columns([4, 1])
            c1.markdown(f"**{q['name']}** · asking {money_fmt(float(q['price']), q.get('currency','USD'))}"
                        f"{note} — you can haggle it lower after adding")
            sold_out = stock is not None and int(stock) <= 0
            if c2.button("Add" if not sold_out else "—", disabled=sold_out,
                         key=f"add_{q['id']}", use_container_width=True):
                add_product_to_deal(q)
                ss["picker"] = False
                st.rerun()

    # ---- merchant area: LOCKED unless a PIN is configured and entered
    with st.expander("🔐 Merchant area"):
        if not MERCHANT_PIN:
            st.error("Locked — no merchant PIN is configured. Set `MERCHANT_PIN` in "
                     "`.streamlit/secrets.toml` (or an env var) to enable catalog editing "
                     "and refunds. There is no default password.")
        elif not ss.get("merchant_ok"):
            with st.form("merchant_pin_form", clear_on_submit=True):
                pin = st.text_input("Merchant PIN", type="password")
                submitted = st.form_submit_button("Unlock", type="primary")
            if submitted:
                if pin.strip() == MERCHANT_PIN.strip():
                    ss["merchant_ok"] = True
                    st.rerun()
                elif pin:
                    st.error("Wrong PIN.")
            st.caption("Required for catalog editing, the orders table, refunds, "
                       "and resending receipts. Unlocking also reveals the "
                       "auto-hidden side panel (PayPal status, session reset).")
        else:
            st.success("Unlocked — remember to lock on shared machines.")
            if st.button("🔒 Lock now"):
                ss["merchant_ok"] = False
                st.rerun()
            st.divider()

            edits = []
            for i, p in enumerate(products):
                with st.container(border=True):
                    c1, c2 = st.columns(2)
                    name = c1.text_input("Name", p["name"], key=f"m_name_{i}")
                    price = c2.number_input("Asking", min_value=1.0, value=float(p["price"]),
                                            step=5.0, key=f"m_price_{i}")
                    floor = c1.number_input("Secret floor", min_value=1.0,
                                            value=float(p.get("floor_price", price * 0.8)),
                                            step=5.0, key=f"m_floor_{i}")
                    stock_raw = c2.text_input("Stock (blank = unlimited)",
                                              "" if p.get("stock") is None else str(int(p["stock"])),
                                              key=f"m_stock_{i}")
                    try:
                        stock_val = None if stock_raw.strip() == "" \
                            else max(0, int(float(stock_raw)))
                    except ValueError:
                        stock_val = p.get("stock")
                    desc = st.text_input("Description", p.get("description", ""), key=f"m_desc_{i}")
                    edits.append({**p, "name": name, "price": float(price),
                                  "floor_price": float(floor), "stock": stock_val,
                                  "description": desc})
            if st.button("💾 Save catalog"):
                (st.success("Catalog saved to products.json.") if save_products(edits)
                 else st.error("Couldn't write products.json — filesystem may be read-only."))
                st.rerun()
            st.divider()
            st.markdown("**Recent orders (all buyers, from the database)**")
            for o in store.list_orders(10):
                st.caption(f"`{o['order_id'][:18]}…` · **{o['status']}** · "
                           f"{o.get('item') or '—'} · {o.get('currency') or ''} "
                           f"{o.get('amount') if o.get('amount') is not None else '—'}")
            st.divider()
            st.markdown("**🩺 System status (diagnostics)**")
            st.caption(f"🗄️ DB: `{store.db_path()}`")
            flags = []
            if RUNTIME.get("poller"):
                flags.append("poller ✅")
            if RUNTIME.get("webhook"):
                flags.append(f"webhook ✅ :{_cfg('WEBHOOK_PORT', '8600')}")
            st.caption("Background capture: " + (", ".join(flags) if flags
                       else "⚠️ off — capture happens on redirect only"))
            st.caption("📧 Receipt email: " + (f"on ({_cfg('SMTP_HOST')})" if mailer.enabled()
                       else "off — set SMTP_HOST to enable"))
            st.caption("Secrets stay server-side.")
    if MERCHANT_PIN and not ss.get("merchant_ok"):
        st.caption("🔒 The shop-owner side panel is auto-hidden — it appears once "
                   "you unlock the Merchant area above.")

# ---------------- HAGGLE & CHECKOUT ----------------
elif ss["nav"] == PAGES[1]:
    p = current_product()
    if p is None:
        st.info("Pick an item in the 🛍️ Shop tab to start negotiating.")
    else:
        cur = p.get("currency", "USD")
        head_l, head_r = st.columns([3, 1])
        head_l.subheader(f"💬 {p['name']}")
        if head_r.button("🔄 Restart chat", use_container_width=True):
            ss["chat"], ss["bot"] = [], new_bot_state(p)
            ss["picker"] = False
            st.rerun()
        stock = p.get("stock")
        stock_note = ""
        if stock is not None:
            stock_note = " · 🔴 sold out" if int(stock) <= 0 else f" · {int(stock)} in stock"
        st.caption(f"Asking {money_fmt(float(p['price']), cur)}{stock_note} · "
                   f"automated negotiator — make an offer!")

        if not ss["chat"]:
            dl = next((l for l in ss["deal"] if l["product_id"] == p["id"]), None)
            note = ""
            if dl and dl["price"] < asking_of(dl):
                note = (f" It's in your deal at **{money_fmt(dl['price'], cur)}** — "
                        f"push lower or check out below.")
            two = bundle_total(float(p["price"]), float(p.get("floor_price", 0) or 0), 2)
            ss["chat"].append({"role": "assistant",
                "text": (f"Hi! I'm the negotiating assistant for the {p['name']} "
                         f"(asking {money_fmt(float(p['price']), cur)}).{note} "
                         f"Make me an offer — quantities welcome (*\"2 for "
                         f"{money_fmt(two, cur)}\"*) — or ask me anything.")})

        for m in ss["chat"]:
            with st.chat_message("assistant" if m["role"] == "assistant" else "user",
                                 avatar="🤖" if m["role"] == "assistant" else "🧑"):
                st.markdown(m["text"])

        # ---- quick actions
        with st.container(border=True):
            st.caption("🤔 Not sure where to start? Tap an offer — or type any price, anything goes.")
            offers = suggest_offers(p)
            b1, b2, b3, b4 = st.columns(4)
            clicked = None
            for col, v, key in zip((b1, b2, b3), offers, ("qo0", "qo1", "qo2")):
                if col.button(f"💵 {offer_tag(v, p.get('price', 0))} · {money_fmt(v, cur)}",
                              key=key, use_container_width=True):
                    clicked = f"I offer {money_fmt(v, cur)}"
            last = (ss["bot"] or {}).get("last_action")
            if b4.button("🤝 Take your offer", disabled=last not in ("counter", "decline"),
                         use_container_width=True, key="qdeal"):
                clicked = "deal"
            f1, f2, f3, _ = st.columns(4)
            for col, label, q, key in ((f1, "🚚 Shipping?", "when will it ship?", "qs"),
                                       (f2, "↩️ Returns?", "what's the return policy?", "qr"),
                                       (f3, "🧐 Condition?", "what condition is it in?", "qc")):
                if col.button(label, key=f"qf{key}", use_container_width=True):
                    clicked = q
            if clicked:
                process_message(clicked)
                st.rerun()

        # ---- deal cart
        if ss["deal"]:
            pend = bool(ss["pending"])
            sav = total_savings()
            with st.container(border=True):
                hc1, hc2 = st.columns([3, 1])
                hc1.markdown("#### 🧺 Your deal")
                if not pend and hc2.button("🗑️ Clear deal", use_container_width=True):
                    ss["deal"].clear(); ss["pending"] = None
                    st.toast("Deal cleared — nothing was charged.", icon="🗑️")
                    st.rerun()
                for i, l in enumerate(ss["deal"]):
                    haggled = l["price"] < asking_of(l) - 0.001
                    line_prod = next((q for q in products if q["id"] == l["product_id"]), None)
                    cap = MAX_QTY if (line_prod is None or line_prod.get("stock") is None) \
                        else min(MAX_QTY, int(line_prod["stock"]))
                    r1, r2 = st.columns([5, 3])
                    r1.markdown(
                        f"**{l['item']}** — {money_fmt(l['price'], l['currency'])} each · "
                        f"{'🟢 haggled' if haggled else '⚪ asking'} · "
                        f"line **{money_fmt(round(l['price'], 2) * int(l['qty']), l['currency'])}**")
                    if pend:
                        r2.caption(f"qty {l['qty']}")
                    else:
                        qa, qb, qc = r2.columns(3)
                        if qa.button("➖", key=f"qm_{i}", use_container_width=True,
                                     help="One less"):
                            l["qty"] = max(1, l["qty"] - 1); st.rerun()
                        qb.markdown(f"**{l['qty']}**")
                        if qc.button("➕", key=f"qp_{i}", use_container_width=True,
                                     help="Add one more"):
                            if l["qty"] >= cap:
                                st.toast(f"Only {cap} × {l['item']} available.", icon="⚠️")
                            else:
                                l["qty"] += 1
                            st.rerun()
                        q4, q5 = r2.columns(2)
                        if q4.button("💬 Haggle", key=f"qh_{i}", use_container_width=True,
                                     help="Haggle this price again"):
                            ss["product_id"] = l["product_id"]
                            ss["chat"], ss["bot"] = [], new_bot_state(
                                next(q for q in products if q["id"] == l["product_id"]))
                            ss["nav"] = PAGES[1]
                            st.rerun()
                        if q5.button("❌ Remove", key=f"qx_{i}", use_container_width=True):
                            ss["deal"].pop(i)
                            st.toast(f"Removed {l['item']} from your deal.", icon="❌")
                            st.rerun()
                if not pend:
                    if st.button("➕ Add items", key="cart_add"):
                        ss["picker"] = not ss["picker"]
                        st.rerun()
                    if ss["picker"]:
                        others = [q for q in products
                                  if not any(l["product_id"] == q["id"] for l in ss["deal"])]
                        for q in others:
                            qstock = q.get("stock")
                            qnote = ""
                            if qstock is not None:
                                qnote = " · sold out" if int(qstock) <= 0 else f" · {int(qstock)} left"
                            c1, c2 = st.columns([4, 1])
                            c1.markdown(f"**{q['name']}** · asking "
                                        f"{money_fmt(float(q['price']), q.get('currency','USD'))}{qnote}")
                            qsold = qstock is not None and int(qstock) <= 0
                            if c2.button("Add" if not qsold else "—", disabled=qsold,
                                         key=f"cadd_{q['id']}", use_container_width=True):
                                add_product_to_deal(q)
                                ss["picker"] = False
                                st.rerun()
                st.markdown(
                    f"**Total: {money_fmt(cart_total(), cart_currency())}**"
                    + (f" · 💰 you saved {money_fmt(sav, cart_currency())} by haggling" if sav > 0 else ""))

            # ---- checkout / cancel
            if ss["pending"]:
                row = store.get_order(ss["pending"]["order_id"]) or {}
                if row.get("status") == "COMPLETED":
                    ss["pending"] = None
                    st.success("Payment already confirmed (captured in the background) — "
                               "see the 🧾 Receipts tab.")
                    st.rerun()
                elif row.get("status") == "APPROVED":
                    st.warning("⏳ Payment approved on PayPal — the background poller/webhook will "
                               "capture it automatically. Approved payments can't be canceled here; "
                               "the merchant can refund after capture if needed (🧾 Receipts).")
                else:
                    amt = money_fmt(cart_total(), cart_currency())
                    st.subheader("🧾 Checkout")
                    st.markdown(f"Total to pay: **{amt}** · {len(ss['deal'])} item line"
                                f"{'s' if len(ss['deal']) > 1 else ''}"
                                + (f" · 💰 you saved {money_fmt(sav, cart_currency())}" if sav > 0 else ""))
                    st.caption("🧾 Your receipt appears on screen right after payment — "
                               "view and download it in the 🧾 Receipts tab.")
                    st.link_button(f"🅿️ Pay {amt} securely with PayPal",
                                   ss["pending"]["approve_url"], type="primary",
                                   use_container_width=True)
                    if st.button("✖️ Cancel checkout", use_container_width=True):
                        ss["pending"] = None
                        st.toast("Checkout canceled — nothing was charged. You can edit your deal now.",
                                 icon="✖️")
                        st.rerun()
                    st.caption("Canceling abandons the PayPal order — **no charge** — and unlocks "
                               "your deal for editing.")
            else:
                st.caption("🧾 After payment your receipt is generated instantly — "
                           "view and download it in the 🧾 Receipts tab.")
                if st.button(f"🅿️ Create PayPal order — {money_fmt(cart_total(), cart_currency())}",
                             type="primary", use_container_width=True):
                    problem = _stock_problem(ss["deal"], products)
                    if problem:
                        st.error(problem)
                    else:
                        client = paypal_client()
                        if client is None:
                            st.error("PayPal isn't configured yet — shop owner: add "
                                     "PAYPAL_CLIENT_ID and PAYPAL_SECRET to "
                                     ".streamlit/secrets.toml (or env vars), then restart.")
                        else:
                            rid = str(uuid.uuid4())
                            items = [{"name": l["item"], "qty": int(l["qty"]),
                                      "price": round(float(l["price"]), 2)} for l in ss["deal"]]
                            try:
                                order = client.create_order(
                                    amount_value=cart_total(), currency=cart_currency(),
                                    description=deal_summary()[:127], reference_id="cart",
                                    items=items,
                                    return_url=app_base_url(), cancel_url=app_base_url(),
                                    brand_name=MERCHANT_NAME, request_id=rid)
                                approve = next((l["href"] for l in order.get("links", [])
                                                if l.get("rel") == "approve"), "")
                                store.upsert_order(order["id"], request_id=rid,
                                                   item=deal_summary()[:120],
                                                   amount=cart_total(), currency=cart_currency(),
                                                   status="CREATED",
                                                   buyer_email=ss.get("buyer_email") or None)
                                ss["pending"] = {"order_id": order["id"], "approve_url": approve,
                                                 "request_id": rid}
                                st.rerun()
                            except PayPalError as e:
                                st.error(f"Could not create the PayPal order: {e}")
        elif ss["product_id"] and any(
                o.get("status") == "COMPLETED" and any(i.get("product_id") == p["id"]
                                                       for i in (o.get("items") or []))
                for o in store.list_orders(50)):
            st.success("✅ You've already paid for this item — see the 🧾 Receipts tab.")

# ---------------- RECEIPTS ----------------
else:
    st.subheader("🧾 Receipts")
    rows = store.list_receipts()
    if not rows:
        st.info("No receipts yet — they appear here automatically after a successful PayPal payment.")
    else:
        # newest first (ISO timestamps sort lexicographically); the just-paid receipt wins
        rows = sorted(rows, key=lambda r: str(r.get("time", "")), reverse=True)
        last_id = (ss.get("last_receipt") or {}).get("order_id")
        top = next((i for i, r in enumerate(rows) if r.get("order_id") == last_id), 0)
        rows[0], rows[top] = rows[top], rows[0]          # latest receipt goes on top
        latest, earlier = rows[0], rows[1:]

        _render_receipt(latest, open_card=True)          # the ONLY card shown by default
        if earlier and st.toggle(f"🗂️ Show earlier receipts ({len(earlier)})"):
            for r in earlier:
                _render_receipt(r)                       # closed by default (top level —
                                                         # Streamlit forbids nesting expanders)

# ---------------------------------------------------------------- sidebar
# AUTO-HIDDEN for buyers: with MERCHANT_PIN configured and the Merchant area
# locked, the CSS injected above removes the sidebar entirely (expand arrow
# included). It renders again only for the unlocked shop owner, showing the
# merchant caption, PayPal status, and the Reset button.
with st.sidebar:
    st.header("🏪 HaggleMart")
    if ss.get("merchant_ok"):
        st.caption("Shop-owner panel — buyers never see this side panel. Lock the "
                   "🔐 Merchant area (🛍️ Shop page) to hide it again.")
    else:
        st.caption("For buyers: 💬 agree a price with the bot in 💬 Haggle & Checkout, "
                   "🅿️ pay with PayPal, 🧾 get an instant receipt.")
    _pp_ok = bool(_cfg("PAYPAL_CLIENT_ID") and _cfg("PAYPAL_SECRET"))
    st.caption("🅿️ PayPal: **connected** · "
               f"{(_cfg('PAYPAL_MODE', 'sandbox') or 'sandbox').lower()}" if _pp_ok
               else "🅿️ PayPal: **not configured** — shop owner, check secrets setup.")
    st.divider()
    if st.button("🔄 Reset session", use_container_width=True,
                 help="Shop-owner tool: start over — clears this browser session's "
                      "chat, deal cart, and checkout state. Nothing is charged."):
        for k in list(ss.keys()):
            del ss[k]
        st.rerun()
