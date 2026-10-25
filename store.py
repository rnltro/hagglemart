"""SQLite persistence: orders, receipts, webhook event dedupe.
Safe to call from Streamlit, the webhook thread, and the poller."""
import json
import os
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone

_DB_PATH = os.getenv("DB_PATH", "data/hagglemart.sqlite3")
_write_lock = threading.Lock()

def set_db_path(path):
    global _DB_PATH
    _DB_PATH = path

def db_path():
    return _DB_PATH

def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

def _connect():
    os.makedirs(os.path.dirname(_DB_PATH) or ".", exist_ok=True)
    con = sqlite3.connect(_DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with _write_lock, closing(_connect()) as con, con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS orders (
          order_id TEXT PRIMARY KEY, request_id TEXT, product_id TEXT, item TEXT,
          amount REAL, currency TEXT, status TEXT NOT NULL DEFAULT 'CREATED',
          capture_id TEXT, refund_id TEXT, buyer_name TEXT, buyer_email TEXT,
          receipt TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS events (
          event_id TEXT PRIMARY KEY, event_type TEXT, created_at TEXT);
        """)

def upsert_order(order_id, *, request_id=None, product_id=None, item=None,
                 amount=None, currency=None, status=None, buyer_email=None):
    now = _now()
    with _write_lock, closing(_connect()) as con, con:
        con.execute("""
            INSERT INTO orders (order_id, request_id, product_id, item, amount,
                                currency, status, buyer_email, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(order_id) DO UPDATE SET
              request_id=COALESCE(excluded.request_id, request_id),
              product_id=COALESCE(excluded.product_id, product_id),
              item=COALESCE(excluded.item, item),
              amount=COALESCE(excluded.amount, amount),
              currency=COALESCE(excluded.currency, currency),
              status=COALESCE(excluded.status, status),
              buyer_email=COALESCE(excluded.buyer_email, buyer_email),
              updated_at=excluded.updated_at
        """, (order_id, request_id, product_id, item, amount, currency,
              status or "CREATED", buyer_email, now, now))

def get_order(order_id):
    with closing(_connect()) as con:
        r = con.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        return dict(r) if r else None

def mark_completed(rec: dict):
    """rec = receipt snapshot (see paypal_client.receipt_from_order)."""
    now = _now()
    with _write_lock, closing(_connect()) as con, con:
        con.execute("""
            INSERT INTO orders (order_id, item, amount, currency, status, capture_id,
                                buyer_name, buyer_email, receipt, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(order_id) DO UPDATE SET
              status='COMPLETED', capture_id=excluded.capture_id,
              buyer_name=COALESCE(excluded.buyer_name, buyer_name),
              buyer_email=COALESCE(excluded.buyer_email, buyer_email),
              receipt=excluded.receipt, updated_at=excluded.updated_at
        """, (rec.get("order_id"), rec.get("item"), float(rec.get("amount") or 0),
              rec.get("currency", "USD"), "COMPLETED", rec.get("capture_id"),
              rec.get("buyer_name"), rec.get("buyer_email"), json.dumps(rec), now, now))

def mark_refunded(order_id, refund_id):
    with _write_lock, closing(_connect()) as con, con:
        con.execute("UPDATE orders SET status='REFUNDED', refund_id=?, updated_at=? WHERE order_id=?",
                    (refund_id, _now(), order_id))
        try:
            con.execute("""UPDATE orders
                           SET receipt=json_set(receipt,'$.status','REFUNDED','$.refund_id',?)
                           WHERE order_id=? AND receipt IS NOT NULL""",
                        (refund_id, order_id))
        except Exception:
            pass

def set_status(order_id, status):
    with _write_lock, closing(_connect()) as con, con:
        con.execute("UPDATE orders SET status=?, updated_at=? WHERE order_id=?",
                    (status, _now(), order_id))

def stale_created(minutes=5, limit=10):
    """CREATED orders idle for `minutes` → poller checks their real status
    at PayPal (buyer may have approved but never made it back)."""
    cutoff = datetime.now(timezone.utc).timestamp() - minutes * 60
    out = []
    with closing(_connect()) as con:
        rows = con.execute(
            "SELECT * FROM orders WHERE status='CREATED' ORDER BY updated_at LIMIT ?",
            (limit,)).fetchall()
    for r in rows:
        try:
            ts = datetime.strptime(r["updated_at"], "%Y-%m-%dT%H:%M:%S")\
                         .replace(tzinfo=timezone.utc).timestamp()
        except Exception:
            continue
        if ts <= cutoff:
            out.append(dict(r))
    return out

def find_by_capture(capture_id):
    with closing(_connect()) as con:
        r = con.execute("SELECT * FROM orders WHERE capture_id=?", (capture_id,)).fetchone()
        return dict(r) if r else None

def record_event(event_id, event_type):
    """True if new (should process); False = duplicate → skip."""
    try:
        with _write_lock, closing(_connect()) as con, con:
            con.execute("INSERT INTO events (event_id, event_type, created_at) VALUES (?,?,?)",
                        (event_id, event_type, _now()))
        return True
    except sqlite3.IntegrityError:
        return False

def list_receipts(limit=100):
    with closing(_connect()) as con:
        rows = con.execute("""SELECT * FROM orders
                              WHERE status IN ('COMPLETED','REFUNDED')
                              ORDER BY updated_at DESC LIMIT ?""", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            rec = json.loads(d["receipt"]) if d.get("receipt") else None
        except Exception:
            rec = None
        if rec is None:
            rec = {"order_id": d["order_id"], "capture_id": d.get("capture_id") or "",
                   "amount": f"{d.get('amount') or 0:.2f}", "currency": d.get("currency") or "USD",
                   "item": d.get("item") or "Item", "buyer_name": d.get("buyer_name") or "",
                   "buyer_email": d.get("buyer_email") or "", "time": d.get("updated_at") or ""}
        rec["status"] = d["status"]
        rec.setdefault("refund_id", d.get("refund_id"))
        rec.setdefault("buyer_email", d.get("buyer_email") or "")
        out.append(rec)
    return out

def list_orders(limit=50):
    with closing(_connect()) as con:
        rows = con.execute("SELECT * FROM orders ORDER BY updated_at DESC LIMIT ?",
                           (limit,)).fetchall()
    return [dict(r) for r in rows]
