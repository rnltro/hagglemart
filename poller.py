"""Background poller: captures APPROVED orders whose buyer never returned."""
import threading
import time
import traceback

import fulfillment
import store
from paypal_client import PayPalError, receipt_from_order

_lock = threading.Lock()
_started = False


def _merge_email(rec: dict, order_id: str) -> dict:
    """Keep the email the buyer gave in-chat/at checkout if PayPal didn't return one."""
    if not rec.get("buyer_email"):
        row = store.get_order(order_id) or {}
        if row.get("buyer_email"):
            rec["buyer_email"] = row["buyer_email"]
    return rec


def _tick(client_factory):
    client = client_factory()
    if client is None:
        return
    for row in store.stale_created(minutes=5):
        oid = row["order_id"]
        try:
            order = client.get_order(oid)
        except PayPalError:
            continue
        status = order.get("status")
        if status == "APPROVED":
            try:
                captured = client.capture_order(oid, request_id=row.get("request_id"))
            except PayPalError as e:
                if "ALREADY_CAPTURED" not in e.body.upper():
                    print(f"[poller] {oid} capture failed: {e}")
                    continue
                captured = order
            rec = _merge_email(receipt_from_order(captured), oid)
            store.mark_completed(rec)
            fulfillment.fulfill(rec)
            print(f"[poller] captured {oid} (reconciled from CREATED)")
        elif status == "COMPLETED":
            rec = _merge_email(receipt_from_order(order), oid)
            store.mark_completed(rec)
            fulfillment.fulfill(rec)
            print(f"[poller] recorded {oid} (already captured elsewhere)")


def start_poller(client_factory, interval=60):
    global _started
    with _lock:
        if _started:
            return
        _started = True

    def loop():
        while True:
            try:
                _tick(client_factory)
            except Exception:
                traceback.print_exc()
            time.sleep(max(20, int(interval)))

    threading.Thread(target=loop, daemon=True, name="paypal-poller").start()
