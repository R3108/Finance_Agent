"""Subscription tiers and feature entitlements.

The single place that decides what each plan can do. API routes call `require(feature)` and the agent's
tools call `allows(plan, feature)`, so moving a feature between tiers is a one-line change here.
Paid plans are bought through Razorpay (`billing.py`), which writes `users.plan`. Prices are integer paise.
"""
from __future__ import annotations

from datetime import date

from .db import get_conn

# Entitlement is a single linear rank: a plan includes everything the plans below it include.
# That keeps `allows()` to one comparison, but it can't express orthogonal tiers — a "Business"
# plan with the freelancer tools but not the household would need FEATURES to map to explicit
# feature sets per plan instead. Worth doing if that tier is ever sold; not before.
RANK = {"free": 0, "pro": 1, "family": 2}

CURRENCY = "INR"

PLANS = {
    "free": {
        "name": "Free", "price_monthly": 0, "price_annual": 0,
        "tagline": "See where your money goes.",
        # 5 is deliberately the size of the core starter set: a free account gets a genuinely useful
        # set of watchers, and Pro is what adds custom rules, email delivery and the digest.
        "limits": {"ai_questions_per_month": 15, "alert_rules": 5},
    },
    "pro": {
        "name": "Pro", "price_monthly": 29900, "price_annual": 269900,   # ₹299/month or ₹2,699/year
        "tagline": "Plan, pay down debt and save automatically.",
        "limits": {"ai_questions_per_month": None, "alert_rules": None},
    },
    "family": {
        "name": "Family", "price_monthly": 49900, "price_annual": 449900,
        "tagline": "Pro for the whole household, on one bill.",
        "limits": {"ai_questions_per_month": None, "alert_rules": None},
    },
}

# feature key: (label, minimum plan)
FEATURES = {
    "dashboard": ("Dashboard, insights and health score", "free"),
    "subscriptions": ("Subscription detection and unusual-charge alerts", "free"),
    "budgets": ("Smart budgets", "free"),
    "net_worth": ("Net worth tracking", "free"),
    "bill_calendar": ("Bill calendar", "free"),
    "csv_import": ("Bank CSV import and export", "free"),
    "sms_capture": ("Add transactions by pasting bank SMS alerts", "free"),
    "afford": ("“Can I afford it?” purchase check", "free"),
    "splits": ("Split bills with friends and settle up over UPI", "free"),
    "two_factor": ("Two-factor sign-in and device management", "free"),
    "ai_assistant": ("AI assistant with verified numbers", "free"),
    "unlimited_ai": ("Unlimited AI questions", "pro"),
    "ai_categorization": ("AI categorisation of unknown merchants", "pro"),
    "automations": ("Proactive alerts that watch your money", "free"),
    "unlimited_alerts": ("Unlimited alert rules", "pro"),
    "alert_email": ("Alerts delivered by email", "pro"),
    "digest": ("Weekly AI money digest", "pro"),
    "sms_autocapture": ("Automatic tracking: your phone forwards every bank SMS as it arrives", "pro"),
    "freelancer": ("Business expenses, tax estimate and accountant export", "pro"),
    "receipts": ("Receipt storage with automatic matching", "pro"),
    "what_if": ("Goals what-if simulator", "pro"),
    "debt_planner": ("Debt payoff planner (avalanche vs snowball)", "pro"),
    "challenges": ("Savings challenges", "pro"),
    "wrapped": ("Money Wrapped year in review", "pro"),
    "household": ("Shared household ledger for up to 5 people", "family"),
}


class PlanRequired(Exception):
    def __init__(self, feature: str, plan: str):
        self.feature, self.plan = feature, plan
        label, needed = FEATURES[feature]
        super().__init__(f"{label} is part of Ledgerly {PLANS[needed]['name']}. Upgrade on the Plans page to unlock it.")


def allows(plan: str, feature: str) -> bool:
    return RANK.get(plan, 0) >= RANK[FEATURES[feature][1]]


def ai_questions_used(user_id: int, today: date | None = None) -> int:
    month = (today or date.today()).strftime("%Y-%m")
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE user_id = ? AND role = 'user' AND substr(created_at, 1, 7) = ?",
            (user_id, month)).fetchone()[0]


def check_ai_quota(user_id: int, plan: str) -> None:
    limit = PLANS.get(plan, PLANS["free"])["limits"]["ai_questions_per_month"]
    if limit is not None and ai_questions_used(user_id) >= limit:
        raise PlanRequired("unlimited_ai", plan)


def limit_for(plan: str, name: str):
    return PLANS.get(plan, PLANS["free"])["limits"].get(name)


def check_alert_rule_quota(user_id: int, plan: str) -> None:
    """Free accounts keep a handful of watchers; unlimited alerts are what Pro is for."""
    limit = limit_for(plan, "alert_rules")
    if limit is None:
        return
    with get_conn() as conn:
        used = conn.execute("SELECT COUNT(*) FROM alert_rules WHERE user_id = ?", (user_id,)).fetchone()[0]
    if used >= limit:
        raise PlanRequired("unlimited_alerts", plan)


def public_catalog() -> dict:
    """Prices and features for the signed-out landing page — no usage, nothing about any account."""
    return {"currency": CURRENCY, "plans": _plan_rows()}


def _plan_rows() -> list[dict]:
    return [{"id": pid, **p,
             # rupees at the output boundary; paise everywhere else
             "price_monthly": p["price_monthly"] / 100, "price_annual": p["price_annual"] / 100,
             "annual_saving_pct": round((1 - p["price_annual"] / (p["price_monthly"] * 12)) * 100) if p["price_monthly"] else 0,
             "features": [{"key": k, "label": lbl, "included": RANK[pid] >= RANK[need], "tier": need}
                          for k, (lbl, need) in FEATURES.items()]}
            for pid, p in PLANS.items()]


def catalog(user_id: int, plan: str) -> dict:
    return {
        "current": plan, "currency": CURRENCY,
        "usage": {"ai_questions_this_month": ai_questions_used(user_id),
                  "ai_questions_limit": limit_for(plan, "ai_questions_per_month"),
                  "alert_rules_limit": limit_for(plan, "alert_rules")},
        "plans": _plan_rows(),
    }
