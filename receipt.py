"""Itemized thermal-style receipt: HTML preview + real PDF (fpdf2) download."""
import html as _html

from fpdf import FPDF

from haggle_bot import money_fmt


def _latin(s):
    return str(s or "").encode("latin-1", "replace").decode("latin-1")


def _e(s):
    return _html.escape(str(s if s is not None else ""))


def _items_of(rec: dict) -> list:
    items = rec.get("items") or []
    if not items:
        items = [{"name": rec.get("item", "Item"), "qty": 1,
                  "price": float(rec.get("amount") or 0)}]
    return items


def receipt_html(rec: dict, merchant: str) -> str:
    refunded = str(rec.get("status", "")).upper() == "REFUNDED"
    stamp_style = "border-color:#dc2626;color:#dc2626;" if refunded else ""
    cur = rec.get("currency", "USD")
    subtotal, units, rows = 0.0, 0, ""
    for it in _items_of(rec):
        q, u = int(it.get("qty", 1)), float(it.get("price") or 0)
        line = round(u * q, 2)
        subtotal, units = round(subtotal + line, 2), units + q
        rows += (f"<tr><td>{_e(it['name'])}</td>"
                 f"<td class='rp-right'>{money_fmt(line, cur)}</td></tr>"
                 f"<tr><td class='rp-qtyline'>{q} × {money_fmt(u, cur)}"
                 f"{' each' if q > 1 else ''}</td><td></td></tr>\n")
    refund_line = (f"<b>Refund ID:</b> {_e(rec.get('refund_id'))}<br>"
                   if refunded and rec.get("refund_id") else "")
    total = _e(rec.get("amount_str") or f"{rec.get('amount', '')} {cur}".strip())
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Receipt {_e(rec.get('order_id'))}</title>
<style>
  body {{ margin:0; background:transparent; }}
  .receipt-page {{ font-family:'Courier New',Courier,monospace; color:#111; background:#fff;
    width:min(360px,100%); margin:0 auto; padding:22px 18px 26px; position:relative; }}
  .rp-center {{ text-align:center; }}
  .rp-store {{ font-size:17px; font-weight:700; letter-spacing:1.5px; }}
  .rp-sub {{ font-size:11px; margin-top:2px; color:#444; }}
  .rp-dash {{ border:0; border-top:1px dashed #9a9a9a; margin:10px 0; }}
  .rp-table {{ width:100%; border-collapse:collapse; font-size:12px; }}
  .rp-table td {{ padding:2.5px 0; vertical-align:top; }}
  .rp-right {{ text-align:right; white-space:nowrap; }}
  .rp-qtyline {{ color:#666; font-size:11px; padding-left:12px; }}
  .rp-meta td {{ font-size:11px; color:#333; }}
  .rp-total-row td {{ font-weight:700; font-size:14.5px; padding-top:5px; }}
  .rp-kv {{ font-size:11px; line-height:1.75; word-break:break-all; }}
  .rp-stamp {{ position:absolute; top:14px; right:10px; transform:rotate(8deg);
    border:3px solid #16a34a; color:#16a34a; font-weight:800; font-size:18px;
    padding:3px 12px; border-radius:6px; opacity:.85; background:rgba(255,255,255,.7); {stamp_style} }}
  .rp-barcode {{ height:40px; margin:12px auto 3px; width:80%;
    background:repeating-linear-gradient(90deg,#111 0 2px,transparent 2px 4px,#111 4px 7px,
               transparent 7px 9px,#111 9px 10px,transparent 10px 13px,#111 13px 15px,
               transparent 15px 18px); }}
  .rp-code {{ text-align:center; font-size:10.5px; letter-spacing:2px; }}
  .rp-foot {{ text-align:center; font-size:11px; margin-top:10px; line-height:1.6; color:#333; }}
</style></head>
<body><div class="receipt-page">
  <div class="rp-stamp">{_e("REFUNDED" if refunded else "PAID")}</div>
  <div class="rp-center">
    <div class="rp-store">{_e(merchant).upper()}</div>
    <div class="rp-sub">* items haggled · prices negotiated *</div>
  </div>
  <hr class="rp-dash">
  <table class="rp-table rp-meta">
    <tr><td>Date</td><td class="rp-right">{_e(str(rec.get('time', ''))[:19].replace('T', ' '))} UTC</td></tr>
    <tr><td>Served by</td><td class="rp-right">Haggle Bot</td></tr>
  </table>
  <hr class="rp-dash">
  <table class="rp-table">{rows}</table>
  <hr class="rp-dash">
  <table class="rp-table">
    <tr><td>Subtotal ({units} item{'s' if units != 1 else ''})</td>
        <td class="rp-right">{money_fmt(subtotal, cur)}</td></tr>
    <tr><td>Shipping</td><td class="rp-right">FREE</td></tr>
    <tr class="rp-total-row"><td>TOTAL</td><td class="rp-right">{total}</td></tr>
  </table>
  <hr class="rp-dash">
  <div class="rp-kv">
    <b>Payment:</b> PayPal — paid in full ✅<br>
    <b>Order ID:</b> {_e(rec.get('order_id'))}<br>
    <b>Capture ID:</b> {_e(rec.get('capture_id') or '—')}<br>
    {refund_line}<b>Payer:</b> {_e(rec.get('buyer_name') or '—')} &lt;{_e(rec.get('buyer_email') or '—')}&gt;
  </div>
  <div class="rp-barcode"></div>
  <div class="rp-code">{_e(rec.get('order_id'))}</div>
  <div class="rp-foot">Thank you for your business!<br>Keep this receipt for returns (14 days).<br>— {_e(merchant)} —</div>
</div></body></html>"""


def _dash(pdf):
    pdf.set_dash_pattern(dash=1.3, gap=1.3)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.set_dash_pattern()
    pdf.ln(4)


def receipt_pdf(rec: dict, merchant: str) -> bytes:
    refunded = str(rec.get("status", "")).upper() == "REFUNDED"
    cur = rec.get("currency", "USD")
    items = _items_of(rec)
    subtotal = round(sum(round(float(i["price"]) * int(i.get("qty", 1)), 2) for i in items), 2)
    units = sum(int(i.get("qty", 1)) for i in items)

    pdf = FPDF()
    pdf.set_margins(15, 12, 15)
    pdf.add_page()

    pdf.set_font("Courier", "B", 15)
    pdf.cell(0, 8, _latin(merchant.upper())[:36], align="C", ln=1)
    pdf.set_font("Courier", "", 9)
    pdf.cell(0, 5, "* items haggled - prices negotiated *", align="C", ln=1)
    pdf.ln(2)
    _dash(pdf)

    pdf.set_font("Courier", "", 10)
    pdf.cell(26, 6, "Date")
    pdf.cell(0, 6, f"{str(rec.get('time', ''))[:19].replace('T', ' ')} UTC", align="R", ln=1)
    pdf.cell(26, 6, "Served by")
    pdf.cell(0, 6, "Haggle Bot", align="R", ln=1)
    _dash(pdf)

    pdf.set_font("Courier", "B", 10)
    pdf.cell(80, 6, "ITEM")
    pdf.cell(14, 6, "QTY", align="R")
    pdf.cell(42, 6, "EACH", align="R")
    pdf.cell(44, 6, "AMOUNT", align="R", ln=1)
    pdf.set_font("Courier", "", 10)
    for it in items:
        q, u = int(it.get("qty", 1)), float(it.get("price") or 0)
        name = _latin(it["name"])
        name = name[:34] + ".." if len(name) > 36 else name
        pdf.cell(80, 6, name)
        pdf.cell(14, 6, str(q), align="R")
        pdf.cell(42, 6, _latin(money_fmt(u, cur)), align="R")
        pdf.cell(44, 6, _latin(money_fmt(round(u * q, 2), cur)), align="R", ln=1)
    pdf.ln(1)
    _dash(pdf)

    pdf.cell(114, 6, f"Subtotal ({units} item{'s' if units != 1 else ''})")
    pdf.cell(66, 6, _latin(money_fmt(subtotal, cur)), align="R", ln=1)
    pdf.cell(114, 6, "Shipping")
    pdf.cell(66, 6, "FREE", align="R", ln=1)
    pdf.set_font("Courier", "B", 13)
    pdf.cell(114, 9, "TOTAL")
    pdf.cell(66, 9, _latin(rec.get("amount_str")
                           or f"{rec.get('amount', '')} {cur}".strip()), align="R", ln=1)
    pdf.ln(1)
    _dash(pdf)

    pdf.set_font("Courier", "", 9)
    pdf.cell(0, 5, "Payment: PayPal - paid in full", ln=1)
    pdf.multi_cell(150, 5, f"Order ID:   {rec.get('order_id', '')}")
    if rec.get("capture_id"):
        pdf.multi_cell(150, 5, f"Capture ID: {rec['capture_id']}")
    if refunded and rec.get("refund_id"):
        pdf.set_text_color(185, 28, 28)
        pdf.multi_cell(150, 5, f"REFUNDED - refund ID: {rec.get('refund_id')}")
        pdf.set_text_color(0, 0, 0)
    if rec.get("buyer_name"):
        pdf.multi_cell(150, 5, f"Payer:      {rec['buyer_name']}  <{rec.get('buyer_email', '')}>")
    pdf.ln(3)

    x, y = pdf.l_margin + 10, pdf.get_y()
    oid = rec.get("order_id") or "HAGGLEMART"
    for i, ch in enumerate(oid[:24]):
        w = 0.8 + (ord(ch) % 4) * 0.55
        if i % 2 == 0:
            pdf.rect(x, y, w, 12, style="F")
        x += w + 0.6
    pdf.set_y(y + 13)
    pdf.cell(0, 5, oid, align="C", ln=1)
    pdf.ln(2)
    pdf.set_font("Courier", "I", 9)
    pdf.cell(0, 5, "Thank you for your business!", align="C", ln=1)
    pdf.cell(0, 5, "Keep this receipt for returns (14 days).", align="C", ln=1)
    return bytes(pdf.output())
