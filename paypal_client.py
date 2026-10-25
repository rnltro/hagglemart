"""Minimal PayPal Orders v2 client (server-side only — the secret never reaches the browser)."""
import base64
from datetime import datetime, timedelta, timezone

import requests

API_BASES = {"sandbox": "https://api-m.sandbox.paypal.com",
             "live": "https://api-m.paypal.com"}


class PayPalError(Exception):
    def __init__(self, status, body):
        self.status, self.body = status, body
        super().__init__(f"PayPal API error {status}: {body[:300]}")


class PayPalClient:
    def __init__(self, client_id, client_secret, mode="sandbox"):
        self.client_id, self.client_secret = client_id, client_secret
        self.base = API_BASES.get(mode, API_BASES["sandbox"])
        self._token, self._exp = None, datetime.now(timezone.utc)

    def _token_now(self):
        if self._token and datetime.now(timezone.utc) < self._exp:
            return self._token
        auth = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        r = requests.post(f"{self.base}/v1/oauth2/token",
                          headers={"Authorization": f"Basic {auth}"},
                          data={"grant_type": "client_credentials"}, timeout=30)
        if r.status_code >= 400:
            raise PayPalError(r.status_code, r.text)
        d = r.json()
        self._token = d["access_token"]
        self._exp = datetime.now(timezone.utc) + timedelta(seconds=int(d.get("expires_in", 3600)) - 120)
        return self._token

    def _headers(self, request_id=None):
        h = {"Authorization": f"Bearer {self._token_now()}",
             "Content-Type": "application/json"}
        if request_id:
            h["PayPal-Request-Id"] = request_id
        return h

    @staticmethod
    def _check(r):
        if r.status_code >= 400:
            raise PayPalError(r.status_code, r.text)
        return r.json()

    def create_order(self, *, amount_value, currency, description, reference_id,
                     return_url, cancel_url, brand_name, request_id=None, items=None):
        pu = {"reference_id": str(reference_id)[:256],
              "description": str(description)[:127],
              "amount": {"currency_code": currency, "value": f"{float(amount_value):.2f}"}}
        if items:
            pu["amount"]["breakdown"] = {"item_total": {"currency_code": currency,
                                                        "value": f"{float(amount_value):.2f}"}}
            pu["items"] = [{"name": str(i["name"])[:127],
                            "quantity": str(int(i["qty"])),
                            "unit_amount": {"currency_code": currency,
                                            "value": f"{float(i['price']):.2f}"}} for i in items]
        payload = {"intent": "CAPTURE", "purchase_units": [pu],
                   "application_context": {
                       "brand_name": str(brand_name)[:32],
                       "shipping_preference": "NO_SHIPPING",
                       "user_action": "PAY_NOW",
                       "return_url": return_url, "cancel_url": cancel_url}}
        r = requests.post(f"{self.base}/v2/checkout/orders",
                          headers=self._headers(request_id), json=payload, timeout=30)
        return self._check(r)

    def get_order(self, order_id):
        r = requests.get(f"{self.base}/v2/checkout/orders/{order_id}",
                         headers=self._headers(), timeout=30)
        return self._check(r)

    def capture_order(self, order_id, request_id=None):
        r = requests.post(f"{self.base}/v2/checkout/orders/{order_id}/capture",
                          headers=self._headers(request_id), timeout=30)
        return self._check(r)

    def refund_capture(self, capture_id, amount_value=None, currency=None,
                       note=None, request_id=None):
        """Full refund if amount_value is None, partial otherwise."""
        body = {}
        if amount_value is not None:
            body["amount"] = {"value": f"{float(amount_value):.2f}",
                              "currency": currency or "USD"}
        if note:
            body["note_to_payer"] = str(note)[:255]
        r = requests.post(f"{self.base}/v2/payments/captures/{capture_id}/refund",
                          headers=self._headers(request_id), json=body, timeout=30)
        return self._check(r)


def receipt_from_order(order: dict) -> dict:
    """Receipt snapshot from a PayPal order object (shared by app, webhook, poller)."""
    pu = (order.get("purchase_units") or [{}])[0]
    cap = (pu.get("payments", {}).get("captures") or [{}])[0]
    payer = order.get("payer", {}) or {}
    name = payer.get("name", {}) or {}
    amt = cap.get("amount", {}) or {}
    return {
        "order_id": order.get("id", ""),
        "capture_id": cap.get("id", ""),
        "status": cap.get("status", order.get("status", "")),
        "amount": amt.get("value", ""),
        "currency": amt.get("currency_code", "USD"),
        "item": pu.get("description") or pu.get("reference_id") or "Item",
        "buyer_name": " ".join(x for x in [name.get("given_name"), name.get("surname")] if x),
        "buyer_email": payer.get("email_address", ""),
        "time": cap.get("create_time") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
    }
