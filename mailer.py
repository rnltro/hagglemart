"""Email receipts via SMTP. Enabled when SMTP_HOST is set (secrets are bridged
to env vars in app.get_runtime, so webhook/poller threads see them too)."""
import os
import smtplib
import ssl
import threading
from email.message import EmailMessage


def enabled() -> bool:
    return bool(os.getenv("SMTP_HOST"))


def _conn() -> smtplib.SMTP:
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587") or 587)
    user, pw = os.getenv("SMTP_USER") or None, os.getenv("SMTP_PASS") or None
    if port == 465:
        s = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        s = smtplib.SMTP(host, port, timeout=30)
        try:
            s.starttls(context=ssl.create_default_context())
        except smtplib.SMTPException:
            pass
    if user and pw:
        s.login(user, pw)
    return s


def email_receipt(rec: dict, merchant: str, to_email: str, pdf_bytes: bytes = None):
    """Sends synchronously. Returns (ok, detail). Never raises."""
    try:
        if not enabled():
            return (False, "SMTP not configured (set SMTP_HOST to enable).")
        if not to_email:
            return (False, "No email address on this order — type one in the box.")

        msg = EmailMessage()
        msg["Subject"] = f"Your receipt from {merchant} — order {str(rec.get('order_id', ''))[:14]}"
        msg["From"] = os.getenv("SMTP_FROM") or os.getenv("SMTP_USER") or "receipts@hagglemart.local"
        msg["To"] = to_email.strip()
        msg.set_content(
            "Thanks for your purchase!\n\n"
            f"Item(s): {rec.get('item', '')}\n"
            f"Total: {rec.get('amount_str') or rec.get('amount', '')} {rec.get('currency', '')}\n"
            f"Order ID: {rec.get('order_id', '')}\n\n"
            "Your itemized PDF receipt is attached.\n")

        if pdf_bytes is None:
            try:
                from receipt import receipt_pdf
                pdf_bytes = receipt_pdf(rec, merchant)
            except Exception:
                pdf_bytes = None
        if pdf_bytes:
            msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf",
                               filename=f"receipt_{rec.get('order_id', 'receipt')}.pdf")

        with _conn() as s:
            s.send_message(msg)
        return (True, f"sent to {to_email.strip()}")
    except Exception as e:
        return (False, str(e)[:200])


def email_receipt_async(rec: dict, merchant: str, to_email: str):
    """Fire-and-forget for the capture paths (never blocks checkout)."""
    def run():
        ok, why = email_receipt(rec, merchant, to_email)
        print(f"[mailer] receipt {rec.get('order_id', '')}: {why}")
    threading.Thread(target=run, daemon=True, name="receipt-email").start()
