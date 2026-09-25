"""Plan billing with Razorpay: auto-renewing Subscriptions, or prepaid passes when Subscriptions is unavailable.

Mode is chosen per checkout by `subscriptions_available()` (a cached, read-only probe of the Subscriptions API):

Subscription mode (UPI Autopay / card / eMandate, renews automatically)
  checkout -> Razorpay subscription; `verify_checkout` checks HMAC(payment_id|subscription_id) and re-fetches the
  subscription; webhooks (`subscription.*`) apply renewals, retries, halts and cancellations.

Prepaid mode (any one-time method; works on every Razorpay account)
  checkout -> Razorpay order for 1 month or 1 year; `verify_checkout` checks HMAC(order_id|payment_id), confirms the
  payment belongs to that order with the exact amount (capturing it if needed) and activates a pass. A renewal
  bought early starts where the current pass ends. `order.paid` webhooks activate passes whose browser tab closed
  before verification. `send_renewal_reminders` emails 3 days before a pass runs out.

Prices always come from `plans.PLANS` (paise), never from the browser. `users.plan` is derived: the best tier
among subscriptions and passes that grant access right now, re-evaluated on every request (`refresh_plan`), so
an expired pass drops to Free on its own.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone

import httpx
import pandas as pd

from . import config, mailer, plans
from .db import get_conn
from .net import TLS_VERIFY

log = logging.getLogger("ledgerly.billing")

API = "https://api.razorpay.com/v1"
PERIODS = {"monthly": ("monthly", "price_monthly", 120, 1), "annual": ("yearly", "price_annual", 10, 12)}
#           period: (razorpay period, price key, subscription total_count, months of access per pass)
ACCESS_STATUSES = {"authenticated", "active", "pending"}   # pending = renewal failing but Razorpay is still retrying
TERMINAL = {"cancelled", "completed", "expired", "halted"}
REMINDER_DAYS = 3


class BillingError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


SUBSCRIPTIONS_DISABLED = ("Razorpay Subscriptions is not enabled on this Razorpay account yet "
                          "(Dashboard → Subscriptions).")


# ----------------------------------------------------------------------------- Razorpay API

def _api(method: str, path: str, payload: dict | None = None) -> dict:
    s = config.settings
    if not s.billing_enabled:
        raise BillingError("Payments are currently unavailable.", 503)
    try:
        r = httpx.request(method, f"{API}{path}", json=payload, auth=(s.razorpay_key_id, s.razorpay_key_secret), timeout=20,
                          verify=TLS_VERIFY)
    except httpx.HTTPError as exc:
        raise BillingError("Couldn't reach Razorpay. Please try again.", 502) from exc
    if r.status_code == 401 and path.startswith(("/plans", "/subscriptions")):
        # valid keys but the Subscriptions product isn't enabled on the account (a bad key fails on every endpoint)
        raise BillingError(f"Payments are temporarily unavailable. {SUBSCRIPTIONS_DISABLED}", 503)
    if r.status_code >= 400:
        try:
            desc = r.json()["error"]["description"]
        except (ValueError, KeyError, TypeError):
            desc = r.text[:200]
        raise BillingError(f"Razorpay error: {desc}", 502)
    return r.json()


_probe: dict = {"key": None, "at": 0.0, "ok": False}


def subscriptions_available() -> bool:
    """Can this Razorpay account create subscriptions? Read-only probe, cached 10 min (1 min after a network error)."""
    s = config.settings
    if not s.billing_enabled:
        return False
    ttl = 600 if _probe.get("definitive") else 60
    if _probe["key"] == s.razorpay_key_id and time.time() - _probe["at"] < ttl:
        return _probe["ok"]
    try:
        _api("GET", "/subscriptions?count=1")
        ok, definitive = True, True
    except BillingError as exc:
        ok, definitive = False, exc.status == 503   # 503 = Subscriptions disabled (a real answer); 502 = network
    _probe.update(key=s.razorpay_key_id, at=time.time(), ok=ok, definitive=definitive)
    return ok


def fetch_subscription(sub_id: str) -> dict:
    return _api("GET", f"/subscriptions/{sub_id}")


def _plan_id(tier: str, period: str) -> str:
    """The Razorpay plan for this tier/period/price, created once and cached."""
    rzp_period, price_key, _, _ = PERIODS[period]
    amount = plans.PLANS[tier][price_key]
    key = f"{tier}|{period}|{amount}|{plans.CURRENCY}"
    with get_conn() as conn:
        row = conn.execute("SELECT plan_id FROM billing_plans WHERE key = ?", (key,)).fetchone()
    if row:
        return row["plan_id"]
    name = f"Ledgerly {plans.PLANS[tier]['name']} ({'Monthly' if period == 'monthly' else 'Yearly'})"
    plan = _api("POST", "/plans", {"period": rzp_period, "interval": 1, "notes": {"tier": tier, "period": period},
                                   "item": {"name": name, "amount": amount, "currency": plans.CURRENCY}})
    with get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO billing_plans (key, plan_id) VALUES (?,?)", (key, plan["id"]))
    return plan["id"]


# ----------------------------------------------------------------------------- entitlement

def _now() -> int:
    return int(time.time())


def _entitled(status: str, current_end: int | None) -> bool:
    if status in ACCESS_STATUSES:
        return True
    return status == "cancelled" and bool(current_end) and current_end > _now()  # paid-up until period end


def _active_pass_end(conn, user_id: int) -> int | None:
    row = conn.execute("SELECT MAX(ends_at) AS e FROM billing_passes WHERE user_id = ? AND status = 'paid' AND ends_at > ?",
                       (user_id, _now())).fetchone()
    return row["e"]


def recompute_plan(user_id: int) -> str:
    """users.plan = best tier among subscriptions and passes granting access now; Free when none do.
    Users with no completed billing at all (demo sandboxes, admin grants) are left alone."""
    now = _now()
    with get_conn() as conn:
        subs = conn.execute("SELECT tier, status, current_end FROM billing_subscriptions WHERE user_id = ? AND status != 'created'",
                            (user_id,)).fetchall()
        passes = conn.execute("SELECT tier, starts_at, ends_at FROM billing_passes WHERE user_id = ? AND status = 'paid'",
                              (user_id,)).fetchall()
        current = conn.execute("SELECT plan FROM users WHERE id = ?", (user_id,)).fetchone()["plan"]
        if not subs and not passes:
            return current
        tiers = [s["tier"] for s in subs if _entitled(s["status"], s["current_end"])]
        tiers += [p["tier"] for p in passes if p["starts_at"] <= now < p["ends_at"]]
        plan = max(tiers, key=lambda t: plans.RANK[t]) if tiers else "free"
        if plan != current:
            conn.execute("UPDATE users SET plan = ? WHERE id = ?", (plan, user_id))
    return plan


def refresh_plan(user_id: int) -> str:
    """Called on every request: lets a pass expire (or a stacked one begin) exactly on time, no cron needed."""
    return recompute_plan(user_id)


# ----------------------------------------------------------------------------- subscriptions

def apply_subscription(sub: dict) -> int | None:
    """Store Razorpay's view of a subscription we created and update the owner's plan. Returns the user id."""
    with get_conn() as conn:
        row = conn.execute("SELECT user_id FROM billing_subscriptions WHERE id = ?", (sub["id"],)).fetchone()
        if not row:
            return None  # not ours (another app on the same Razorpay account)
        conn.execute(
            "UPDATE billing_subscriptions SET status = ?, current_start = ?, current_end = ?, short_url = COALESCE(?, short_url), "
            "updated_at = datetime('now') WHERE id = ?",
            (sub.get("status", "created"), sub.get("current_start"), sub.get("current_end"), sub.get("short_url"), sub["id"]))
    recompute_plan(int(row["user_id"]))
    return int(row["user_id"])


def record_payment(pay: dict, user_id: int, ref_id: str | None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO billing_payments (id, user_id, subscription_id, amount, currency, status, method, invoice_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status = excluded.status",
            (pay["id"], user_id, ref_id, int(pay.get("amount", 0)), pay.get("currency", plans.CURRENCY),
             pay.get("status", "captured"), pay.get("method"), pay.get("invoice_id"), int(pay.get("created_at") or _now())))


def _subscription_checkout(user_id: int, tier: str, period: str, amount: int, pass_end: int | None) -> dict:
    _, _, total_count, _ = PERIODS[period]
    with get_conn() as conn:
        pending = conn.execute("SELECT id FROM billing_subscriptions WHERE user_id = ? AND tier = ? AND period = ? AND status = 'created' "
                               "AND created_at >= datetime('now', '-1 day') ORDER BY created_at DESC LIMIT 1",
                               (user_id, tier, period)).fetchone()
    if pending and not pass_end:  # reuse the unpaid checkout from a closed popup instead of piling up subscriptions
        return {"mode": "subscription", "subscription_id": pending["id"]}
    body = {"plan_id": _plan_id(tier, period), "total_count": total_count, "quantity": 1, "customer_notify": 1,
            "notes": {"user_id": str(user_id), "tier": tier, "period": period}}
    if pass_end:  # already paid up with a pass: auto-renewal takes over the day it runs out
        body["start_at"] = pass_end
    sub = _api("POST", "/subscriptions", body)
    with get_conn() as conn:
        conn.execute("INSERT INTO billing_subscriptions (id, user_id, tier, period, amount, currency, status, short_url) "
                     "VALUES (?,?,?,?,?,?,?,?)",
                     (sub["id"], user_id, tier, period, amount, plans.CURRENCY, sub.get("status", "created"), sub.get("short_url")))
    return {"mode": "subscription", "subscription_id": sub["id"]}


# ----------------------------------------------------------------------------- prepaid passes

def _add_months(ts: int, months: int) -> int:
    return int((pd.Timestamp(ts, unit="s") + pd.DateOffset(months=months)).timestamp())


def _order_checkout(user_id: int, tier: str, period: str, amount: int) -> dict:
    order = _api("POST", "/orders", {"amount": amount, "currency": plans.CURRENCY, "receipt": f"lgr_{user_id}_{_now()}",
                                     "notes": {"user_id": str(user_id), "tier": tier, "period": period, "kind": "prepaid_pass"}})
    with get_conn() as conn:
        conn.execute("INSERT INTO billing_passes (id, user_id, tier, period, amount, currency) VALUES (?,?,?,?,?,?)",
                     (order["id"], user_id, tier, period, amount, plans.CURRENCY))
    return {"mode": "order", "order_id": order["id"]}


def _activate_pass(order_id: str, pay: dict) -> int | None:
    """Mark a pass paid exactly once (browser verify and webhook may race) and schedule its access window."""
    with get_conn() as conn:
        p = conn.execute("SELECT * FROM billing_passes WHERE id = ?", (order_id,)).fetchone()
        if not p:
            return None
        if pay.get("order_id") not in (None, order_id) or int(pay.get("amount", -1)) != p["amount"] \
                or pay.get("currency", plans.CURRENCY) != p["currency"]:
            raise BillingError("Payment doesn't match this order. If you were charged, contact support for a refund.", 400)
        claimed = conn.execute("UPDATE billing_passes SET status = 'paid', payment_id = ? WHERE id = ? AND status = 'created'",
                               (pay["id"], order_id)).rowcount
        if claimed:
            start = max(_now(), _active_pass_end(conn, p["user_id"]) or 0)  # renewing early extends, never overlaps
            conn.execute("UPDATE billing_passes SET starts_at = ?, ends_at = ? WHERE id = ?",
                         (start, _add_months(start, PERIODS[p["period"]][3]), order_id))
    record_payment({**pay, "status": "captured"}, p["user_id"], order_id)
    recompute_plan(p["user_id"])
    return p["user_id"]


def _captured_payment(payment_id: str, amount: int) -> dict:
    pay = _api("GET", f"/payments/{payment_id}")
    if pay.get("status") == "authorized":  # accounts without auto-capture: capture now so the money actually settles
        pay = _api("POST", f"/payments/{payment_id}/capture", {"amount": amount, "currency": plans.CURRENCY})
    if pay.get("status") != "captured":
        raise BillingError("The payment hasn't completed. If money left your account, it will be refunded automatically.", 400)
    return pay


# ----------------------------------------------------------------------------- checkout

def start_checkout(user_id: int, tier: str, period: str) -> dict:
    if tier not in plans.PLANS or not plans.PLANS[tier]["price_monthly"]:
        raise BillingError("That plan can't be purchased.")
    if plans.PLANS[tier].get("coming_soon"):
        raise BillingError(f"The {plans.PLANS[tier]['name']} plan is not available yet.", 409)
    if period not in PERIODS:
        raise BillingError("Choose monthly or annual billing.")
    with get_conn() as conn:
        user = conn.execute("SELECT name, email, email_verified_at, is_demo FROM users WHERE id = ?", (user_id,)).fetchone()
        subs = conn.execute("SELECT status, current_end FROM billing_subscriptions WHERE user_id = ? AND status != 'created'",
                            (user_id,)).fetchall()
        pass_end = _active_pass_end(conn, user_id)
    if user["is_demo"]:
        raise BillingError("Demo sandboxes can't subscribe. Create an account first.", 403)
    if not user["email_verified_at"]:
        raise BillingError("Verify your email address before upgrading. Check your inbox or resend the link from the banner.", 403)
    if any(_entitled(s["status"], s["current_end"]) for s in subs):
        raise BillingError("You already have an active subscription. Manage it on the Plans page.", 409)

    amount = plans.PLANS[tier][PERIODS[period][1]]
    if subscriptions_available():
        session = _subscription_checkout(user_id, tier, period, amount, pass_end)
    else:
        session = _order_checkout(user_id, tier, period, amount)
    label = {"monthly": "Monthly", "annual": "Yearly"}[period] if session["mode"] == "subscription" else \
        {"monthly": "1 month", "annual": "1 year"}[period]
    return {
        **session, "key_id": config.settings.razorpay_key_id, "amount": amount, "currency": plans.CURRENCY,
        "name": "Ledgerly", "description": f"{plans.PLANS[tier]['name']} · {label}",
        "prefill": {"name": user["name"], "email": user["email"]},
    }


def _hmac(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_checkout(user_id: int, payment_id: str, signature: str, subscription_id: str | None = None,
                    order_id: str | None = None) -> dict:
    """Called by the browser right after payment. Unlocks the plan as soon as the payment is proven."""
    secret = config.settings.razorpay_key_secret or ""
    if subscription_id:
        with get_conn() as conn:
            owned = conn.execute("SELECT 1 FROM billing_subscriptions WHERE id = ? AND user_id = ?", (subscription_id, user_id)).fetchone()
        if not owned:
            raise BillingError("Unknown subscription.", 404)
        if not hmac.compare_digest(_hmac(secret, f"{payment_id}|{subscription_id}".encode()), signature or ""):
            raise BillingError("Payment verification failed. If you were charged, it will be refunded or applied automatically.", 400)
        apply_subscription(fetch_subscription(subscription_id))  # Razorpay is the source of truth for status and period
        try:
            record_payment(_api("GET", f"/payments/{payment_id}"), user_id, subscription_id)
        except BillingError:
            pass  # the plan is already unlocked; the webhook will fill in the receipt
    elif order_id:
        with get_conn() as conn:
            p = conn.execute("SELECT amount FROM billing_passes WHERE id = ? AND user_id = ?", (order_id, user_id)).fetchone()
        if not p:
            raise BillingError("Unknown order.", 404)
        if not hmac.compare_digest(_hmac(secret, f"{order_id}|{payment_id}".encode()), signature or ""):
            raise BillingError("Payment verification failed. If you were charged, it will be refunded or applied automatically.", 400)
        _activate_pass(order_id, _captured_payment(payment_id, p["amount"]))
    else:
        raise BillingError("Missing subscription or order id.")
    return status(user_id)


def cancel(user_id: int) -> dict:
    """Cancel auto-renewal at the end of the paid period: access continues until then, no further charges."""
    with get_conn() as conn:
        rows = conn.execute("SELECT id, status FROM billing_subscriptions WHERE user_id = ? AND status != 'created'",
                            (user_id,)).fetchall()
    live = [r for r in rows if r["status"] in ACCESS_STATUSES]
    if not live:
        raise BillingError("You don't have an auto-renewing subscription. Prepaid passes simply end on their date.", 404)
    for r in live:
        sub = _api("POST", f"/subscriptions/{r['id']}/cancel", {"cancel_at_cycle_end": 1})
        with get_conn() as conn:
            conn.execute("UPDATE billing_subscriptions SET cancel_at_cycle_end = 1, updated_at = datetime('now') WHERE id = ?", (r["id"],))
        apply_subscription(sub)
    return status(user_id)


# ----------------------------------------------------------------------------- webhooks

SUBSCRIPTION_EVENTS = {"subscription.authenticated", "subscription.activated", "subscription.charged", "subscription.pending",
                       "subscription.halted", "subscription.cancelled", "subscription.completed", "subscription.paused",
                       "subscription.resumed", "subscription.updated"}


def verify_webhook_signature(body: bytes, signature: str | None) -> bool:
    secret = config.settings.razorpay_webhook_secret
    return bool(secret and signature) and hmac.compare_digest(_hmac(secret, body), signature)


def handle_webhook(body: bytes, signature: str | None, event_id: str | None) -> dict:
    if not config.settings.razorpay_webhook_secret:
        raise BillingError("Webhook secret not configured.", 503)
    if not verify_webhook_signature(body, signature):
        raise BillingError("Invalid signature.", 400)
    event = json.loads(body)
    name = event.get("event", "")
    event_id = event_id or hashlib.sha256(body).hexdigest()
    with get_conn() as conn:
        if conn.execute("INSERT OR IGNORE INTO billing_events (event_id, event) VALUES (?,?)", (event_id, name)).rowcount == 0:
            return {"ok": True, "duplicate": True}
    payload = event.get("payload", {})
    sub_entity = (payload.get("subscription") or {}).get("entity")
    pay_entity = (payload.get("payment") or {}).get("entity")
    order_entity = (payload.get("order") or {}).get("entity")
    user_id = None
    if name in SUBSCRIPTION_EVENTS and sub_entity:
        user_id = apply_subscription(fetch_subscription(sub_entity["id"]))
        if user_id and pay_entity:
            record_payment(pay_entity, user_id, sub_entity["id"])
    elif name in ("order.paid", "payment.captured") and pay_entity:
        order_id = (order_entity or {}).get("id") or pay_entity.get("order_id")
        if order_id and pay_entity.get("status") == "captured":
            user_id = _activate_pass(order_id, pay_entity)  # no-op if the browser already verified it
    elif name == "payment.failed" and pay_entity:
        ref = pay_entity.get("order_id") or pay_entity.get("subscription_id") or ""
        with get_conn() as conn:
            row = conn.execute("SELECT user_id FROM billing_passes WHERE id = ? UNION ALL "
                               "SELECT user_id FROM billing_subscriptions WHERE id = ?", (ref, ref)).fetchone()
        if row:
            user_id = int(row["user_id"])
            record_payment({**pay_entity, "status": "failed"}, user_id, ref)
    return {"ok": True, "event": name, "applied": user_id is not None}


# ----------------------------------------------------------------------------- reminders

def send_renewal_reminders() -> int:
    """Email everyone whose prepaid Pro ends within REMINDER_DAYS and who hasn't renewed or subscribed. Idempotent."""
    now = _now()
    horizon = now + REMINDER_DAYS * 86400
    with get_conn() as conn:
        due = conn.execute(
            """SELECT p.id, p.user_id, p.ends_at, u.name, u.email FROM billing_passes p JOIN users u ON u.id = p.user_id
               WHERE p.status = 'paid' AND p.reminder_sent_at IS NULL AND p.ends_at > ? AND p.ends_at <= ?
                 AND u.email IS NOT NULL AND u.is_demo = 0
                 AND NOT EXISTS (SELECT 1 FROM billing_passes q WHERE q.user_id = p.user_id AND q.status = 'paid' AND q.ends_at > p.ends_at)
                 AND NOT EXISTS (SELECT 1 FROM billing_subscriptions s WHERE s.user_id = p.user_id
                                 AND s.status IN ('authenticated', 'active', 'pending'))""",
            (now, horizon)).fetchall()
    for r in due:
        with get_conn() as conn:  # claim first so a crash mid-send can't produce duplicates on the next run
            if not conn.execute("UPDATE billing_passes SET reminder_sent_at = datetime('now') WHERE id = ? AND reminder_sent_at IS NULL",
                                (r["id"],)).rowcount:
                continue
        ends = datetime.fromtimestamp(r["ends_at"], tz=timezone.utc).strftime("%d %b %Y")
        mailer.send_renewal_reminder(r["email"], r["name"], ends, f"{config.settings.app_url}/plans")
    if due:
        log.info("Sent %d renewal reminder(s)", len(due))
    return len(due)


# ----------------------------------------------------------------------------- read model

def reconcile(user_id: int) -> None:
    """Re-fetch subscriptions whose period has ended (or that are mid-payment) in case a webhook was missed."""
    if not config.settings.billing_enabled:
        return
    now = _now()
    with get_conn() as conn:
        rows = conn.execute("SELECT id, status, current_end FROM billing_subscriptions WHERE user_id = ? AND status != 'created'",
                            (user_id,)).fetchall()
    for r in rows:
        stale = r["status"] not in TERMINAL and (r["current_end"] or 0) < now
        if stale or r["status"] in ("authenticated", "pending"):
            try:
                apply_subscription(fetch_subscription(r["id"]))
            except BillingError:
                pass


def status(user_id: int) -> dict:
    s = config.settings
    now = _now()
    plan = recompute_plan(user_id)
    with get_conn() as conn:
        sub = conn.execute("SELECT * FROM billing_subscriptions WHERE user_id = ? AND status != 'created' "
                           "ORDER BY created_at DESC, rowid DESC LIMIT 1", (user_id,)).fetchone()
        passes = conn.execute("SELECT * FROM billing_passes WHERE user_id = ? AND status = 'paid' ORDER BY ends_at", (user_id,)).fetchall()
        payments = conn.execute("SELECT * FROM billing_payments WHERE user_id = ? ORDER BY created_at DESC LIMIT 24", (user_id,)).fetchall()
    live_passes = [p for p in passes if p["ends_at"] > now]
    configured = s.billing_enabled
    return {
        "configured": configured, "webhooks_configured": bool(s.razorpay_webhook_secret),
        "test_mode": (s.razorpay_key_id or "").startswith("rzp_test_"), "plan": plan, "currency": plans.CURRENCY,
        "mode": ("subscription" if subscriptions_available() else "prepaid") if configured else None,
        "subscription": None if not sub else {
            "id": sub["id"], "tier": sub["tier"], "period": sub["period"], "amount": sub["amount"] / 100, "status": sub["status"],
            "current_period_end": sub["current_end"], "cancel_at_period_end": bool(sub["cancel_at_cycle_end"]),
            "has_access": _entitled(sub["status"], sub["current_end"]), "manage_url": sub["short_url"],
        },
        "pass": None if not live_passes else {
            "tier": live_passes[-1]["tier"], "starts_at": min(p["starts_at"] for p in live_passes),
            "ends_at": live_passes[-1]["ends_at"], "passes": len(live_passes),
            "days_left": max(0, (live_passes[-1]["ends_at"] - now) // 86400),
        },
        "payments": [{"id": p["id"], "amount": p["amount"] / 100, "currency": p["currency"], "status": p["status"],
                      "method": p["method"], "date": p["created_at"]} for p in payments],
    }
