"""FastAPI webhook receiver for PayPal events, run in a daemon thread.

Dashboard setup: developer.paypal.com → Webhooks → Add Webhook
  URL:    https://your-host/paypal/webhook
  Events: CHECKOUT.ORDER.APPROVED, PAYMENT.CAPTURE.COMPLETED, PAYMENT.CAPTURE.REFUNDED
  → copy the Webhook ID into PAYPAL_WEBHOOK_ID.
"""
import json
import threading
import uuid

import requests
from fastapi import FastAPI, Request, Response

import fulfillment
import store
from paypal_client import PayPalClient, PayPalError, receipt_from_order

_lock = threading.Lock()
_started = False


def _verify(client, webhook_id: str, raw: bytes, headers) -> bool:
    payload = {
        "auth_algo": headers.get("paypal-auth-algo", ""),
        "cert_url": headers.get("paypal-cert-url", ""),
        "transmission_id": headers.get("paypal-transmission-id", ""),
        "transmission_sig": headers.get("paypal-transmission-sig", ""),
        "transmission_time": headers.get("paypal-transmission-time", ""),
        "webhook_id": webhook_id,
        "webhook_event": json.loads(raw.decode("utf-8")),
    }
    r = requests.post(f"{client.base}/v1/notifications/verify-webhook-signature",
                      headers=client._headers(), json=payload, timeout=30)
    if r.status_code >= 400:
        return False
    return r.json().get("verification_status") == "SUCCESS"


def _merge_email(rec: dict, order_id: str) -> dict:
    if not rec.get("buyer_email"):
        row = store.get_order(order_id) or {}
        if row.get("buyer_email"):
            rec["buyer_email"] = row["buyer_email"]
    return rec


def _capture_and_record(client, order_id: str, request_id=None):
    try:
        captured = client.capture_order(order_id, request_id=request_id or str(uuid.uuid4()))
    except PayPalError as e:
        if "ALREADY_CAPTURED" in e.body.upper():
            captured = client.get_order(order_id)
        elif "INSTRUMENT_DECLINED" in e.body.upper():
            return False
        else:
            raise
    rec = _merge_email(receipt_from_order(captured), order_id)
    store.mark_completed(rec)
    fulfillment.fulfill(rec)
    return True


def create_app(*, client_factory, webhook_id: str, insecure: bool = False) -> FastAPI:
    app = FastAPI()

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.post("/paypal/webhook")
    async def paypal_webhook(request: Request):
        raw = await request.body()
        try:
            event = json.loads(raw.decode("utf-8"))
        except Exception:
            return Response(status_code=400)

        client = client_factory()
        if client is None:
            return Response(status_code=503)

        if not (insecure and not webhook_id):
            if not webhook_id or not _verify(client, webhook_id, raw, request.headers):
                return Response(status_code=401)

        etype, eid = event.get("event_type", ""), event.get("id", "")
        if eid and not store.record_event(eid, etype):
            return {"status": "duplicate"}

        res = event.get("resource", {}) or {}
        try:
            if etype == "CHECKOUT.ORDER.APPROVED":
                order_id = res.get("id")
                if order_id and store.get_order(order_id):        # only our orders
                    row = store.get_order(order_id) or {}
                    store.upsert_order(order_id, status="APPROVED")
                    order = client.get_order(order_id)
                    if order.get("status") == "COMPLETED":
                        rec = _merge_email(receipt_from_order(order), order_id)
                        store.mark_completed(rec)
                        fulfillment.fulfill(rec)
                    elif order.get("status") == "APPROVED":
                        _capture_and_record(client, order_id, row.get("request_id"))
            elif etype == "PAYMENT.CAPTURE.COMPLETED":
                oid = (res.get("supplementary_data", {}).get("related_ids", {}) or {}).get("order_id")
                if oid:
                    rec = _merge_email(receipt_from_order(client.get_order(oid)), oid)
                    store.mark_completed(rec)
                    fulfillment.fulfill(rec)
                else:
                    row = store.find_by_capture(res.get("id", ""))
                    if row:
                        store.set_status(row["order_id"], "COMPLETED")
            elif etype == "PAYMENT.CAPTURE.REFUNDED":
                oid = (res.get("supplementary_data", {}).get("related_ids", {}) or {}).get("order_id")
                if not oid:
                    row = store.find_by_capture(res.get("id", ""))
                    oid = row["order_id"] if row else None
                if oid:
                    store.mark_refunded(oid, res.get("id", ""))
        except PayPalError as e:
            return Response(status_code=500, content=str(e)[:500])
        return {"status": "ok"}

    return app


def start_webhook_server(*, client_factory, webhook_id: str, port: int = 8600,
                         insecure: bool = False):
    global _started
    with _lock:
        if _started:
            return
        app = create_app(client_factory=client_factory, webhook_id=webhook_id, insecure=insecure)
        import uvicorn
        server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=int(port),
                                               log_level="warning"))
        threading.Thread(target=server.run, daemon=True, name="paypal-webhook").start()
        _started = True
