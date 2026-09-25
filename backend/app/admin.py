"""Operator metrics: the numbers you need to decide whether this is working as a business.

Every figure is computed from the app's own tables, in integer minor units, with the same rule the
rest of the codebase follows: no estimate is presented as a measurement. Where a number is a
projection (MRR from current subscriptions) it is named as one.

Access is by an explicit allow-list in `ADMIN_EMAILS`. With that unset there are no admins and every
route here returns 403 — the safe default for a setting someone will forget to configure.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from . import plans
from .config import live_env, settings
from .db import get_conn, query_df

#: Revenue is charged in INR regardless of a user's display currency, so these are rupee figures.
CURRENCY = plans.CURRENCY


def admin_emails() -> set[str]:
    raw = live_env("ADMIN_EMAILS", settings.env_file) or ""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_admin(email: str | None) -> bool:
    return bool(email) and email.lower() in admin_emails()


def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _money(paise: int | float | None) -> float:
    return round(int(paise or 0) / 100, 2)


# ----------------------------------------------------------------------------- revenue

#: Months per billing period, for normalising an annual plan into a monthly run rate.
PERIOD_MONTHS = {"monthly": 1, "annual": 12}


def revenue() -> dict:
    """Recurring revenue, plus what was actually collected. MRR is a projection; collections are facts."""
    now = _now()
    with get_conn() as conn:
        subs = conn.execute(
            """SELECT tier, period, amount, status, current_end FROM billing_subscriptions
               WHERE status IN ('active', 'authenticated', 'pending')""").fetchall()
        passes = conn.execute(
            "SELECT tier, period, amount FROM billing_passes WHERE status = 'paid' AND amount > 0 AND ends_at > ?",
            (now,)).fetchall()
        collected = conn.execute(
            """SELECT
                 SUM(CASE WHEN created_at >= ? THEN amount ELSE 0 END) AS last_30,
                 SUM(CASE WHEN created_at >= ? THEN amount ELSE 0 END) AS last_365,
                 SUM(amount) AS lifetime, COUNT(*) AS payments
               FROM billing_payments WHERE status = 'captured'""",
            (now - 30 * 86400, now - 365 * 86400)).fetchone()

    # a subscription contributes its amount divided over the months it covers
    mrr = sum(int(s["amount"]) / PERIOD_MONTHS.get(s["period"], 1) for s in subs
              if s["status"] == "active" or (s["current_end"] or 0) > now)
    # prepaid passes aren't recurring, but they are revenue that is currently being consumed
    prepaid_mrr = sum(int(p["amount"]) / PERIOD_MONTHS.get(p["period"], 1) for p in passes)

    return {
        "currency": CURRENCY,
        "mrr_subscriptions": _money(mrr),
        "mrr_prepaid_equivalent": _money(prepaid_mrr),
        "mrr_total": _money(mrr + prepaid_mrr),
        "arr_projected": _money((mrr + prepaid_mrr) * 12),
        "collected_30d": _money(collected["last_30"]),
        "collected_365d": _money(collected["last_365"]),
        "collected_lifetime": _money(collected["lifetime"]),
        "payments": int(collected["payments"] or 0),
        "active_subscriptions": len(subs),
        "active_prepaid_passes": len(passes),
    }


def plan_mix() -> list[dict]:
    """Who is on what — and how many of the paid seats are free time rather than purchases."""
    now = _now()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT plan, COUNT(*) AS n FROM users WHERE is_demo = 0 GROUP BY plan").fetchall()
        granted = conn.execute(
            """SELECT COUNT(DISTINCT user_id) AS n FROM billing_passes
               WHERE status = 'paid' AND amount = 0 AND starts_at <= ? AND ends_at > ?""",
            (now, now)).fetchone()["n"]
    total = sum(int(r["n"]) for r in rows) or 1
    mix = [{"plan": r["plan"], "users": int(r["n"]), "share_pct": round(int(r["n"]) / total * 100, 1)}
           for r in rows]
    mix.sort(key=lambda m: -plans.RANK.get(m["plan"], 0))
    return [*mix, {"plan": "(on free time)", "users": int(granted or 0),
                   "share_pct": round(int(granted or 0) / total * 100, 1)}]


# ----------------------------------------------------------------------------- growth & activation

#: The steps a new account goes through. Each is measured from data the app already stores rather
#: than from a tracking call, so the funnel is right even for accounts created before analytics.
ACTIVATION_STEPS = [
    ("signed_up", "SELECT COUNT(*) FROM users WHERE is_demo = 0 AND created_at >= ?"),
    ("verified_email", "SELECT COUNT(*) FROM users WHERE is_demo = 0 AND created_at >= ? AND email_verified_at IS NOT NULL"),
    ("has_transactions", """SELECT COUNT(DISTINCT u.id) FROM users u JOIN transactions t ON t.user_id = u.id
                            WHERE u.is_demo = 0 AND u.created_at >= ?"""),
    ("asked_the_assistant", """SELECT COUNT(DISTINCT u.id) FROM users u JOIN chat_messages c ON c.user_id = u.id
                               WHERE u.is_demo = 0 AND u.created_at >= ? AND c.role = 'user'"""),
    ("set_a_budget", """SELECT COUNT(DISTINCT u.id) FROM users u JOIN budgets b ON b.user_id = u.id
                        WHERE u.is_demo = 0 AND u.created_at >= ?"""),
    ("paid", """SELECT COUNT(DISTINCT u.id) FROM users u JOIN billing_payments p ON p.user_id = u.id
                WHERE u.is_demo = 0 AND u.created_at >= ? AND p.status = 'captured'"""),
]


def activation(days: int = 90) -> dict:
    """The signup funnel for accounts created in the window."""
    since = (date.today() - timedelta(days=days)).isoformat()
    steps = []
    with get_conn() as conn:
        for name, sql in ACTIVATION_STEPS:
            steps.append({"step": name, "users": int(conn.execute(sql, (since,)).fetchone()[0])})
    first = steps[0]["users"] or 1
    for s in steps:
        s["share_pct"] = round(s["users"] / first * 100, 1)
    return {"window_days": days, "steps": steps}


def signups(weeks: int = 12) -> list[dict]:
    df = query_df(
        """SELECT date(created_at) AS day, COUNT(*) AS n FROM users
           WHERE is_demo = 0 AND created_at >= date('now', ?) GROUP BY day ORDER BY day""",
        (f"-{weeks * 7} days",))
    if df.empty:
        return []
    df["week"] = df["day"].str.slice(0, 10)
    return [{"day": r.day, "signups": int(r.n)} for r in df.itertuples()]


def retention(cohorts: int = 6) -> list[dict]:
    """Monthly signup cohorts and how many of each were still active in later months.

    "Active" means they have a chat message, a transaction import or a session in that month —
    the cheapest proxy the schema supports without a dedicated activity table.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """WITH cohort AS (
                 SELECT id, substr(created_at, 1, 7) AS joined FROM users
                 WHERE is_demo = 0 AND created_at >= date('now', ?)
               ),
               activity AS (
                 SELECT user_id, substr(created_at, 1, 7) AS month FROM chat_messages
                 UNION SELECT user_id, substr(created_at, 1, 7) FROM transactions
                 UNION SELECT user_id, substr(created_at, 1, 7) FROM sessions
               )
               SELECT c.joined, a.month, COUNT(DISTINCT c.id) AS active
               FROM cohort c LEFT JOIN activity a ON a.user_id = c.id AND a.month >= c.joined
               GROUP BY c.joined, a.month ORDER BY c.joined, a.month""",
            (f"-{cohorts * 31} days",)).fetchall()
        sizes = {r["joined"]: int(r["n"]) for r in conn.execute(
            """SELECT substr(created_at, 1, 7) AS joined, COUNT(*) AS n FROM users
               WHERE is_demo = 0 AND created_at >= date('now', ?) GROUP BY joined""",
            (f"-{cohorts * 31} days",))}

    by_cohort: dict[str, dict] = {}
    for r in rows:
        if not r["month"]:
            continue
        cohort = by_cohort.setdefault(r["joined"], {"cohort": r["joined"], "size": sizes.get(r["joined"], 0),
                                                    "months": []})
        offset = _month_gap(r["joined"], r["month"])
        if offset < 0:
            continue
        cohort["months"].append({"offset": offset, "active": int(r["active"]),
                                 "retained_pct": round(int(r["active"]) / max(1, cohort["size"]) * 100, 1)})
    return sorted(by_cohort.values(), key=lambda c: c["cohort"], reverse=True)


def _month_gap(start: str, end: str) -> int:
    (sy, sm), (ey, em) = (int(x) for x in start.split("-")), (int(x) for x in end.split("-"))
    return (ey - sy) * 12 + (em - sm)


def churn(days: int = 30) -> dict:
    """Subscriptions that ended or were told to end, against those that were active."""
    since = (date.today() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            """SELECT
                 SUM(status IN ('cancelled', 'expired', 'completed')) AS ended,
                 SUM(cancel_at_cycle_end = 1 AND status = 'active') AS leaving,
                 SUM(status = 'halted') AS halted,
                 SUM(status = 'active') AS active
               FROM billing_subscriptions WHERE updated_at >= ?""", (since,)).fetchone()
    ended, active = int(row["ended"] or 0), int(row["active"] or 0)
    return {"window_days": days, "ended": ended, "leaving_at_period_end": int(row["leaving"] or 0),
            "halted_payment_failure": int(row["halted"] or 0), "active": active,
            "churn_pct": round(ended / (ended + active) * 100, 1) if (ended + active) else None}


def growth_programs() -> dict:
    """How much free time is being handed out, and what it's bringing back."""
    with get_conn() as conn:
        trials = conn.execute("SELECT COUNT(*) FROM users WHERE trial_started_at IS NOT NULL").fetchone()[0]
        converted = conn.execute(
            """SELECT COUNT(DISTINCT u.id) FROM users u JOIN billing_payments p ON p.user_id = u.id
               WHERE u.trial_started_at IS NOT NULL AND p.status = 'captured'""").fetchone()[0]
        referrals = conn.execute(
            "SELECT COUNT(*) AS n, SUM(rewarded_at IS NOT NULL) AS done FROM referrals").fetchone()
        coupons = conn.execute(
            "SELECT COUNT(*) AS codes, COALESCE(SUM(redeemed), 0) AS used FROM coupons").fetchone()
        free_days = conn.execute(
            "SELECT COALESCE(SUM((ends_at - starts_at) / 86400), 0) FROM billing_passes WHERE amount = 0").fetchone()[0]
    return {
        "trials_started": int(trials),
        "trials_converted": int(converted),
        "trial_conversion_pct": round(int(converted) / int(trials) * 100, 1) if trials else None,
        "referrals_attributed": int(referrals["n"] or 0),
        "referrals_rewarded": int(referrals["done"] or 0),
        "coupon_codes": int(coupons["codes"] or 0),
        "coupon_redemptions": int(coupons["used"] or 0),
        "free_days_granted": int(free_days or 0),
    }


def engagement(days: int = 30) -> dict:
    since = (date.today() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            """SELECT
                 (SELECT COUNT(DISTINCT user_id) FROM chat_messages WHERE created_at >= ? AND role = 'user') AS asked,
                 (SELECT COUNT(*) FROM chat_messages WHERE created_at >= ? AND role = 'user') AS questions,
                 (SELECT COUNT(*) FROM notifications WHERE created_at >= ?) AS alerts,
                 (SELECT COUNT(*) FROM notifications WHERE created_at >= ? AND read_at IS NOT NULL) AS alerts_read,
                 (SELECT COUNT(*) FROM households) AS households,
                 (SELECT COUNT(*) FROM receipts) AS receipts,
                 (SELECT COUNT(DISTINCT user_id) FROM tax_tags) AS freelancers""",
            (since, since, since, since)).fetchone()
    alerts, read = int(row["alerts"] or 0), int(row["alerts_read"] or 0)
    return {"window_days": days, "users_who_asked": int(row["asked"] or 0),
            "questions": int(row["questions"] or 0), "alerts_created": alerts,
            "alerts_read_pct": round(read / alerts * 100, 1) if alerts else None,
            "households": int(row["households"] or 0), "receipts": int(row["receipts"] or 0),
            "users_using_tax_tools": int(row["freelancers"] or 0)}


def top_events(days: int = 30, limit: int = 15) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT name, COUNT(*) AS n, COUNT(DISTINCT user_id) AS users FROM analytics_events
               WHERE created_at >= date('now', ?) GROUP BY name ORDER BY n DESC LIMIT ?""",
            (f"-{days} days", limit)).fetchall()
    return [{"name": r["name"], "count": int(r["n"]), "users": int(r["users"])} for r in rows]


def dashboard(days: int = 30) -> dict:
    with get_conn() as conn:
        totals = conn.execute(
            """SELECT COUNT(*) AS users,
                      SUM(is_demo = 1) AS demos,
                      SUM(is_demo = 0 AND created_at >= date('now', ?)) AS new_users
               FROM users""", (f"-{days} days",)).fetchone()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window_days": days,
        "users": {"total": int(totals["users"] or 0), "demo_sandboxes": int(totals["demos"] or 0),
                  "new_in_window": int(totals["new_users"] or 0)},
        "revenue": revenue(),
        "plan_mix": plan_mix(),
        "activation": activation(days if days >= 30 else 90),
        "churn": churn(days),
        "growth": growth_programs(),
        "engagement": engagement(days),
        "retention": retention(),
        "signups": signups(),
        "events": top_events(days),
    }
