import inventory
import mailer
import os

AUTO_EMAIL = os.getenv("AUTO_EMAIL", "true").lower() != "false"   # add near top


def fulfill(rec: dict):
    # 1) stock: decrement per receipt line (needs product_id)
    try:
        items = rec.get("items") or []
        items = [i for i in items if i.get("product_id")]
        if items:
            inventory.commit_sale(items)
    except Exception as e:
        print(f"[fulfill] stock: {e}")

    # 2) email the PDF receipt — only if PayPal provided the buyer's address
    try:
        to = rec.get("buyer_email")
        if to and mailer.enabled() and AUTO_EMAIL:
            merchant = os.getenv("MERCHANT_NAME", "HaggleMart")
            mailer.email_receipt_async(rec, merchant, to)
    except Exception as e:
        print(f"[fulfill] email: {e}")