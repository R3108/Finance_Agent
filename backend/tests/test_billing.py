"""Razorpay billing against a fake Razorpay API (no network)."""
import dataclasses
import hashlib
import hmac
import itertools
import json
import re
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app import auth, billing, config, mailer

KEY_ID, KEY_SECRET, WEBHOOK_SECRET = "rzp_test_abc", "key-secret", "hook-secret"


class FakeRazorpay:
    """Just enough of the Razorpay REST API for the subscription flow."""

    def __init__(self):
        self.ids = (uuid.uuid4().hex[:14] for _ in itertools.count())  # unique like real Razorpay ids
        self.subs: dict[str, dict] = {}
        self.orders: dict[str, dict] = {}
        self.payments: dict[str, dict] = {}
        self.calls: list[tuple[str, str, dict | None]] = []
        self.subscriptions_enabled = True

    def __call__(self, method, url, json=None, auth=None, timeout=None, verify=None):
        assert auth == (KEY_ID, KEY_SECRET)
        path = url.removeprefix(billing.API)
        self.calls.append((method, path, json))
        if path.startswith(("/plans", "/subscriptions")) and not self.subscriptions_enabled:
            return Resp({"error": "Unauthorized"}, 401)   # what Razorpay answers when the product is off
        if (method, path) == ("GET", "/subscriptions?count=1"):
            return Resp({"entity": "collection", "count": 0, "items": []})
        if (method, path) == ("POST", "/orders"):
            oid = f"order_{next(self.ids)}"
            self.orders[oid] = {"id": oid, "amount": json["amount"], "currency": json["currency"], "status": "created"}
            return Resp(self.orders[oid])
        if m := re.fullmatch(r"/payments/(\w+)/capture", path):
            self.payments[m[1]]["status"] = "captured"
            return Resp(self.payments[m[1]])
        if (m := re.fullmatch(r"/payments/(\w+)", path)) and m[1] in self.payments:
            return Resp(self.payments[m[1]])
        if (method, path) == ("POST", "/plans"):
            assert json["item"]["currency"] == "INR" and json["item"]["amount"] in (29900, 269900)
            return Resp({"id": f"plan_{next(self.ids)}"})
        if (method, path) == ("POST", "/subscriptions"):
            sid = f"sub_{next(self.ids)}"
            self.subs[sid] = {"id": sid, "status": "created", "current_start": None, "current_end": None,
                              "short_url": f"https://rzp.io/i/{sid}", "notes": json["notes"]}
            return Resp(self.subs[sid])
        if m := re.fullmatch(r"/subscriptions/(\w+)/cancel", path):
            return Resp(self.subs[m[1]])  # cancel_at_cycle_end: stays active until the period ends
        if m := re.fullmatch(r"/subscriptions/(\w+)", path):
            return Resp(self.subs[m[1]])
        if m := re.fullmatch(r"/payments/(\w+)", path):
            return Resp({"id": m[1], "amount": 29900, "currency": "INR", "status": "captured", "method": "upi",
                         "created_at": int(time.time())})
        return Resp({"error": {"description": "not found"}}, 404)

    def set(self, sid, status, days_left=30):
        now = int(time.time())
        self.subs[sid].update(status=status, current_start=now - 86400, current_end=now + days_left * 86400)

    def pay_order(self, order_id, status="captured", amount=None):
        """The customer pays an order in Checkout; returns the payment id Checkout would hand the browser."""
        pid = f"pay_{next(self.ids)}"
        self.payments[pid] = {"id": pid, "order_id": order_id, "amount": amount or self.orders[order_id]["amount"],
                              "currency": "INR", "status": status, "method": "upi", "created_at": int(time.time())}
        return pid


class Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code, self.text = data, status, str(data)

    def json(self):
        return self._data


@pytest.fixture()
def rzp(monkeypatch, tmp_path):
    from app import main
    env = tmp_path / ".env"
    env.write_text(f"RAZORPAY_KEY_ID={KEY_ID}\nRAZORPAY_KEY_SECRET={KEY_SECRET}\nRAZORPAY_WEBHOOK_SECRET={WEBHOOK_SECRET}\n")
    s = dataclasses.replace(config.settings, env_file=env)
    monkeypatch.setattr(config, "settings", s)
    monkeypatch.setattr(main, "settings", s)
    fake = FakeRazorpay()
    monkeypatch.setattr(billing.httpx, "request", fake)
    monkeypatch.setattr(billing, "_probe", {"key": None, "at": 0.0, "ok": False})  # fresh availability probe per test
    from app.db import get_conn
    with get_conn() as conn:  # plan ids cached by an earlier test belong to an earlier fake
        conn.execute("DELETE FROM billing_plans")
    return fake


@pytest.fixture()
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, text, html=None: sent.append(text))
    return sent


def verified_user(app, outbox, email):
    c = TestClient(app)
    c.post("/api/auth/signup", json={"name": "Payer", "email": email, "password": "correct horse battery"})
    token = re.search(r"verify-email\?token=([\w-]+)", outbox[-1]).group(1)
    assert c.post("/api/auth/verify", json={"token": token}).status_code == 200
    return c


def sign(payment_id, sub_id, secret=KEY_SECRET):
    return hmac.new(secret.encode(), f"{payment_id}|{sub_id}".encode(), hashlib.sha256).hexdigest()


def webhook(c, event, sub_id, payment=None, event_id=None, secret=WEBHOOK_SECRET):
    payload = {"subscription": {"entity": {"id": sub_id}}}
    if payment:
        payload["payment"] = {"entity": payment}
    body = json.dumps({"entity": "event", "event": event, "payload": payload}).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return c.post("/api/billing/webhook", content=body, headers={"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": event_id or event + sub_id})


@pytest.fixture()
def app(seeded):
    from app.main import app
    auth._failures.clear()
    return app


def test_not_configured(app, outbox):
    c = verified_user(app, outbox, "nokeys@example.com")
    st = c.get("/api/billing").json()
    assert st["configured"] is False and st["subscription"] is None
    assert c.post("/api/billing/checkout", json={"plan": "pro", "period": "monthly"}).status_code == 503


def test_checkout_verify_unlocks_pro_instantly(app, outbox, rzp):
    c = verified_user(app, outbox, "payer@example.com")
    co = c.post("/api/billing/checkout", json={"plan": "pro", "period": "monthly"}).json()
    assert co["key_id"] == KEY_ID and co["amount"] == 29900 and co["currency"] == "INR" and co["prefill"]["email"] == "payer@example.com"
    # closing the popup and trying again reuses the unpaid subscription (and the cached Razorpay plan)
    again = c.post("/api/billing/checkout", json={"plan": "pro", "period": "monthly"}).json()
    assert again["subscription_id"] == co["subscription_id"]
    assert [p for m, p, _ in rzp.calls if m == "POST"] == ["/plans", "/subscriptions"]

    sid = co["subscription_id"]
    rzp.set(sid, "active")
    bad = c.post("/api/billing/verify", json={"razorpay_payment_id": "pay_1", "razorpay_subscription_id": sid, "razorpay_signature": "0" * 64})
    assert bad.status_code == 400 and c.get("/api/me").json()["plan"] == "free"

    ok = c.post("/api/billing/verify", json={"razorpay_payment_id": "pay_1", "razorpay_subscription_id": sid,
                                             "razorpay_signature": sign("pay_1", sid)})
    assert ok.status_code == 200
    st = ok.json()
    assert st["plan"] == "pro" and st["subscription"]["status"] == "active" and st["subscription"]["has_access"]
    assert st["payments"][0]["amount"] == 299.0 and st["payments"][0]["method"] == "upi"
    assert c.get("/api/me").json()["plan"] == "pro"
    assert c.get("/api/debts/plan").status_code == 200                                    # Pro feature unlocked
    assert c.post("/api/billing/checkout", json={"plan": "pro", "period": "annual"}).status_code == 409


def test_checkout_guards(app, outbox, rzp):
    unverified = TestClient(app)
    unverified.post("/api/auth/signup", json={"name": "U", "email": "unverified@example.com", "password": "correct horse battery"})
    assert unverified.post("/api/billing/checkout", json={"plan": "pro", "period": "monthly"}).status_code == 403
    demo = TestClient(app)
    demo.post("/api/auth/demo")
    assert demo.post("/api/billing/checkout", json={"plan": "pro", "period": "monthly"}).status_code == 403
    owner = verified_user(app, outbox, "owner@example.com")
    sid = owner.post("/api/billing/checkout", json={"plan": "pro", "period": "monthly"}).json()["subscription_id"]
    thief = verified_user(app, outbox, "thief@example.com")
    r = thief.post("/api/billing/verify", json={"razorpay_payment_id": "pay_x", "razorpay_subscription_id": sid,
                                                "razorpay_signature": sign("pay_x", sid)})
    assert r.status_code == 404                                                           # can't claim someone else's payment


def test_webhooks_drive_renewals_and_failures(app, outbox, rzp):
    c = verified_user(app, outbox, "hooks@example.com")
    sid = c.post("/api/billing/checkout", json={"plan": "pro", "period": "annual"}).json()["subscription_id"]
    anon = TestClient(app)

    rzp.set(sid, "active", days_left=365)
    assert webhook(anon, "subscription.activated", sid, secret="wrong").status_code == 400  # forged
    assert webhook(anon, "subscription.activated", sid).json()["applied"] is True
    assert c.get("/api/me").json()["plan"] == "pro"

    pay = {"id": "pay_renew", "amount": 269900, "currency": "INR", "status": "captured", "method": "card", "created_at": int(time.time())}
    webhook(anon, "subscription.charged", sid, payment=pay)
    assert webhook(anon, "subscription.charged", sid, payment=pay).json()["duplicate"] is True   # retried delivery
    assert [p["id"] for p in c.get("/api/billing").json()["payments"]] == ["pay_renew"]

    rzp.set(sid, "pending")                                                                  # renewal failing, retries left
    webhook(anon, "subscription.pending", sid)
    assert c.get("/api/me").json()["plan"] == "pro"
    rzp.set(sid, "halted")                                                                   # retries exhausted
    webhook(anon, "subscription.halted", sid)
    assert c.get("/api/me").json()["plan"] == "free"
    assert c.get("/api/debts/plan").status_code == 402


def test_webhook_needs_secret(app, rzp, monkeypatch, tmp_path):
    from app import main
    env = tmp_path / "nohook.env"
    env.write_text(f"RAZORPAY_KEY_ID={KEY_ID}\nRAZORPAY_KEY_SECRET={KEY_SECRET}\n")
    s = dataclasses.replace(config.settings, env_file=env)
    monkeypatch.setattr(config, "settings", s)
    monkeypatch.setattr(main, "settings", s)
    assert webhook(TestClient(app), "subscription.activated", "sub_x").status_code == 503


def test_cancel_keeps_access_until_period_end_then_reconciles(app, outbox, rzp):
    c = verified_user(app, outbox, "cancel@example.com")
    sid = c.post("/api/billing/checkout", json={"plan": "pro", "period": "monthly"}).json()["subscription_id"]
    rzp.set(sid, "active")
    c.post("/api/billing/verify", json={"razorpay_payment_id": "pay_c", "razorpay_subscription_id": sid,
                                        "razorpay_signature": sign("pay_c", sid)})
    st = c.post("/api/billing/cancel").json()
    assert ("POST", f"/subscriptions/{sid}/cancel") in [(m, p) for m, p, _ in rzp.calls]
    assert st["subscription"]["cancel_at_period_end"] and st["plan"] == "pro"               # paid-up month continues
    # the period ends and Razorpay cancels it, but the webhook never arrives: viewing billing reconciles
    rzp.subs[sid].update(status="cancelled", current_end=int(time.time()) - 60)
    from app.db import get_conn
    with get_conn() as conn:
        conn.execute("UPDATE billing_subscriptions SET current_end = ? WHERE id = ?", (int(time.time()) - 60, sid))
    assert c.get("/api/billing").json()["plan"] == "free"
    assert c.post("/api/billing/cancel").status_code == 404


def test_catalog_prices_in_inr(app, outbox):
    c = verified_user(app, outbox, "prices@example.com")
    cat = c.get("/api/plans").json()
    pro = next(p for p in cat["plans"] if p["id"] == "pro")
    assert cat["currency"] == "INR" and pro["price_monthly"] == 299.0 and pro["price_annual"] == 2699.0
    assert pro["annual_saving_pct"] == 25


# ----------------------------------------------------------------------------- prepaid passes (Subscriptions disabled)

def sign_order(order_id, payment_id, secret=KEY_SECRET):
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def buy_pass(c, rzp, period="monthly", **pay):
    co = c.post("/api/billing/checkout", json={"plan": "pro", "period": period}).json()
    assert co["mode"] == "order", co
    pid = rzp.pay_order(co["order_id"], **pay)
    r = c.post("/api/billing/verify", json={"razorpay_payment_id": pid, "razorpay_order_id": co["order_id"],
                                            "razorpay_signature": sign_order(co["order_id"], pid)})
    return co, pid, r


def month_later(ts):
    import pandas as pd
    return int((pd.Timestamp(ts, unit="s") + pd.DateOffset(months=1)).timestamp())


def test_falls_back_to_prepaid_when_subscriptions_disabled(app, outbox, rzp):
    rzp.subscriptions_enabled = False
    c = verified_user(app, outbox, "prepaid@example.com")
    assert c.get("/api/billing").json()["mode"] == "prepaid"
    before = int(time.time())
    co, pid, r = buy_pass(c, rzp)
    assert co["amount"] == 29900 and co["description"] == "Pro · 1 month"
    order_body = next(b for m, p, b in rzp.calls if (m, p) == ("POST", "/orders"))
    assert order_body["amount"] == 29900 and order_body["currency"] == "INR"            # price set by the server
    st = r.json()
    assert r.status_code == 200 and st["plan"] == "pro" and st["pass"]["days_left"] >= 27
    assert month_later(before) - 5 <= st["pass"]["ends_at"] <= month_later(int(time.time())) + 5
    assert c.get("/api/me").json()["plan"] == "pro" and c.get("/api/debts/plan").status_code == 200
    assert st["payments"][0]["id"] == pid and st["payments"][0]["status"] == "captured"
    assert not any(p.startswith(("/plans", "/subscriptions")) and m == "POST" for m, p, _ in rzp.calls)


def test_prepaid_rejects_forgery_and_mismatch(app, outbox, rzp):
    rzp.subscriptions_enabled = False
    c = verified_user(app, outbox, "forge@example.com")
    co = c.post("/api/billing/checkout", json={"plan": "pro", "period": "annual"}).json()
    pid = rzp.pay_order(co["order_id"])
    bad = c.post("/api/billing/verify", json={"razorpay_payment_id": pid, "razorpay_order_id": co["order_id"], "razorpay_signature": "0" * 64})
    assert bad.status_code == 400
    cheap = rzp.pay_order(co["order_id"], amount=100)                                    # paid 1 rupee for a 2,699 order
    r = c.post("/api/billing/verify", json={"razorpay_payment_id": cheap, "razorpay_order_id": co["order_id"],
                                            "razorpay_signature": sign_order(co["order_id"], cheap)})
    assert r.status_code == 400 and c.get("/api/me").json()["plan"] == "free"
    other = verified_user(app, outbox, "other@example.com")                               # can't claim someone else's order
    assert other.post("/api/billing/verify", json={"razorpay_payment_id": pid, "razorpay_order_id": co["order_id"],
                                                   "razorpay_signature": sign_order(co["order_id"], pid)}).status_code == 404


def test_prepaid_captures_authorized_payments(app, outbox, rzp):
    rzp.subscriptions_enabled = False
    c = verified_user(app, outbox, "capture@example.com")
    co, pid, r = buy_pass(c, rzp, status="authorized")
    assert ("POST", f"/payments/{pid}/capture") in [(m, p) for m, p, _ in rzp.calls]
    assert r.json()["plan"] == "pro"


def test_renewing_early_stacks_and_expiry_drops_to_free(app, outbox, rzp):
    rzp.subscriptions_enabled = False
    c = verified_user(app, outbox, "stack@example.com")
    _, _, first = buy_pass(c, rzp)
    end1 = first.json()["pass"]["ends_at"]
    _, _, second = buy_pass(c, rzp)                                                     # renew right away
    p = second.json()["pass"]
    assert p["passes"] == 2 and abs(p["ends_at"] - month_later(end1)) <= 5              # starts where the first ends
    from app.db import get_conn
    with get_conn() as conn:                                                              # time passes: both expired
        conn.execute("UPDATE billing_passes SET starts_at = starts_at - 90*86400, ends_at = ends_at - 90*86400 "
                     "WHERE user_id = (SELECT id FROM users WHERE email = 'stack@example.com')")
    assert c.get("/api/me").json()["plan"] == "free"
    assert c.get("/api/debts/plan").status_code == 402
    assert c.get("/api/billing").json()["pass"] is None


def test_order_paid_webhook_activates_when_tab_closed(app, outbox, rzp):
    rzp.subscriptions_enabled = False
    c = verified_user(app, outbox, "closedtab@example.com")
    co = c.post("/api/billing/checkout", json={"plan": "pro", "period": "monthly"}).json()
    pid = rzp.pay_order(co["order_id"])                                                  # paid, but browser never calls verify
    pay = rzp.payments[pid]
    body = json.dumps({"event": "order.paid", "payload": {"payment": {"entity": pay}, "order": {"entity": {"id": co["order_id"]}}}}).encode()
    sig = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    anon = TestClient(app)
    assert anon.post("/api/billing/webhook", content=body, headers={"X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": "evt_tab"}).json()["applied"]
    assert c.get("/api/me").json()["plan"] == "pro"
    ends = c.get("/api/billing").json()["pass"]["ends_at"]
    # the browser's verify arriving late must not add a second month
    c.post("/api/billing/verify", json={"razorpay_payment_id": pid, "razorpay_order_id": co["order_id"],
                                        "razorpay_signature": sign_order(co["order_id"], pid)})
    assert c.get("/api/billing").json()["pass"]["ends_at"] == ends


def test_renewal_reminders(app, outbox, rzp):
    rzp.subscriptions_enabled = False
    from app.db import get_conn
    c = verified_user(app, outbox, "remind@example.com")
    buy_pass(c, rzp)
    with get_conn() as conn:                                                              # 2 days left
        conn.execute("UPDATE billing_passes SET ends_at = ? WHERE user_id = (SELECT id FROM users WHERE email = 'remind@example.com')",
                     (int(time.time()) + 2 * 86400,))
    before = len(outbox)
    assert billing.send_renewal_reminders() >= 1
    assert len(outbox) > before and "Renew Pro" in outbox[-1] and "/plans" in outbox[-1]
    sent = len(outbox)
    billing.send_renewal_reminders()
    assert len(outbox) == sent                                                            # never twice
    # someone who already renewed gets nothing
    d = verified_user(app, outbox, "renewed@example.com")
    buy_pass(d, rzp)
    with get_conn() as conn:
        uid = conn.execute("SELECT id FROM users WHERE email = 'renewed@example.com'").fetchone()[0]
        conn.execute("UPDATE billing_passes SET ends_at = ? WHERE user_id = ?", (int(time.time()) + 2 * 86400, uid))
    buy_pass(d, rzp)
    sent = len(outbox)
    billing.send_renewal_reminders()
    assert len(outbox) == sent


def test_switches_to_autorenew_once_subscriptions_enabled(app, outbox, rzp):
    rzp.subscriptions_enabled = False
    c = verified_user(app, outbox, "switch@example.com")
    _, _, r = buy_pass(c, rzp)
    pass_end = r.json()["pass"]["ends_at"]
    rzp.subscriptions_enabled = True                                                      # Razorpay fixes the account
    billing._probe.update(at=0.0)                                                         # (the cache would expire in 10 min)
    assert c.get("/api/billing").json()["mode"] == "subscription"
    co = c.post("/api/billing/checkout", json={"plan": "pro", "period": "monthly"}).json()
    assert co["mode"] == "subscription"
    sub_body = next(b for m, p, b in rzp.calls if (m, p) == ("POST", "/subscriptions"))
    assert sub_body["start_at"] == pass_end                                               # no double charge for paid days
