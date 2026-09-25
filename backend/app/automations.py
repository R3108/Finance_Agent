"""Proactive watchers: the agent that works while nobody is looking.

Everything else in the product answers a question the user came to ask. These rules run on a
schedule, notice the same things the dashboard would have told them, and push the ones that matter.

Design notes:

*   **Findings are deterministic.** Every watcher is a thin reader over `analytics` / `planning`,
    so an alert can never state a number those modules didn't produce. The LLM is used only to
    write the digest's opening line, and even that goes through the agent's grounding guard.
*   **Dedupe, not cooldown.** Each finding carries a `dedupe_key` naming the *event* it describes,
    and `notifications` has a UNIQUE constraint on it. That makes the sweep idempotent: running it
    every hour, or twice at once, still produces one notification per real event. Keys embed the
    period they concern (`category_over:Dining:2026-09`), which gives each rule its natural cadence
    without a separate cooldown column to get wrong.
*   **Failures are per-user.** One user's bad data must not stop the sweep for everyone, so
    `run_due` records the error on that user's row and moves on.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from . import analytics as an
from . import mailer
from . import money as cur
from . import planning as pl
from . import plans
from .config import settings
from .db import get_conn
from .services import UserData

log = logging.getLogger("ledgerly.automations")

#: How long after a sweep before a user is eligible again. The loop ticks far more often than this;
#: this is what actually paces the watchers.
SWEEP_INTERVAL = timedelta(hours=6)


@dataclass
class Finding:
    dedupe_key: str
    severity: str          # high | medium | low | info
    title: str
    body: str
    action_url: str | None = None


@dataclass(frozen=True)
class RuleSpec:
    key: str
    label: str
    description: str
    evaluate: Callable[[UserData, dict], list[Finding]]
    #: Parameter name -> (kind, label, default). `kind` is 'money', 'days', 'category' or 'percent';
    #: the UI renders an input from it and `validate_params` coerces and bounds-checks it.
    params: dict[str, tuple[str, str, float | str]] = field(default_factory=dict)
    default_severity: str = "medium"


def _now() -> datetime:
    """UTC, without a tzinfo — timestamps are stored as naive UTC strings and compared as such."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _month(as_of: date) -> str:
    return as_of.strftime("%Y-%m")


# ----------------------------------------------------------------------------- watchers

def _category_over(u: UserData, p: dict) -> list[Finding]:
    category, limit = p["category"], int(round(float(p["limit"]) * 100))
    month = _month(u.as_of)
    sp = an.spending_frame(u.tx)
    spent = int(sp[(sp["month"] == month) & (sp["category"] == category)]["spend_cents"].sum())
    if spent < limit:
        return []
    return [Finding(
        f"category_over:{category}:{month}", "medium",
        f"{category} passed {an.fmt_cents(limit)} this month",
        f"You've spent {an.fmt_cents(spent)} on {category} in {month}, "
        f"{an.fmt_cents(spent - limit)} over the limit you set.",
        "/transactions",
    )]


def _budget_risk(u: UserData, p: dict) -> list[Finding]:
    if not u.budgets:
        return []
    status = an.budget_status(u.tx, u.budgets, as_of=u.as_of)
    out = []
    for b in status["items"]:
        if b["status"] == "on_track":
            continue
        over = b["status"] == "over"
        out.append(Finding(
            f"budget_{b['status']}:{b['category']}:{status['month']}",
            "high" if over else "medium",
            f"{b['category']} budget {'exceeded' if over else 'on pace to be exceeded'}",
            f"{an.fmt_money(b['spent'])} of {an.fmt_money(b['limit'])} used with "
            f"{status['days_in_month'] - status['days_elapsed']} days left; "
            f"on pace for {an.fmt_money(b['projected_month_end'])}.",
            "/budgets",
        ))
    return out


def _large_transaction(u: UserData, p: dict) -> list[Finding]:
    threshold = int(round(float(p["amount"]) * 100))
    window = u.as_of - timedelta(days=int(p.get("days", 7)))
    sp = u.tx[(~u.tx["is_transfer"]) & (u.tx["amount_cents"] <= -threshold)
              & (u.tx["date"].dt.date > window)]
    return [Finding(
        f"large_txn:{int(r.id)}", "medium",
        f"Large charge: {r.merchant} {an.fmt_cents(-r.amount_cents)}",
        f"A {an.fmt_cents(-r.amount_cents)} {r.category} charge posted on "
        f"{r.date.date().isoformat()}, above your {an.fmt_cents(threshold)} alert level.",
        "/transactions",
    ) for r in sp.itertuples()]


def _subscription_flags(kinds: set[str], severity: str, url: str = "/subscriptions"):
    """Watchers that surface a subset of `unusual_subscriptions`, which already does the detection."""
    def evaluate(u: UserData, p: dict) -> list[Finding]:
        out = []
        for f in an.unusual_subscriptions(u.tx, u.as_of):
            if f["type"] not in kinds:
                continue
            # the flag's own identity (merchant + the date the event happened) is the dedupe key,
            # so next year's price rise is a new alert but today's is never repeated
            stamp = f.get("event_date") or _month(u.as_of)
            out.append(Finding(f"{f['type']}:{f.get('merchant') or f['title']}:{stamp}",
                               f.get("severity", severity), f["title"], f["detail"], url))
        return out
    return evaluate


def _low_balance(u: UserData, p: dict) -> list[Finding]:
    floor = int(round(float(p["amount"]) * 100))
    days = int(p.get("days", 30))
    f = an.cashflow_forecast(u.tx, u.accounts, days, u.as_of)
    low_cents = int(round(f["lowest_balance"] * 100))
    if low_cents >= floor:
        return []
    return [Finding(
        f"low_balance:{f['lowest_balance_date']}", "high",
        f"Cash dips to {an.fmt_cents(low_cents)} on {f['lowest_balance_date']}",
        f"Over the next {days} days your projected balance falls below your "
        f"{an.fmt_cents(floor)} floor. Scheduled income {an.fmt_money(f['scheduled_income'])}, "
        f"recurring charges {an.fmt_money(f['scheduled_recurring_charges'])}.",
        "/",
    )]


def _unusual_charge(u: UserData, p: dict) -> list[Finding]:
    out = []
    for a in an.detect_anomalies(u.tx, u.as_of, lookback_days=int(p.get("days", 14))):
        if a["severity"] != "high":
            continue
        out.append(Finding(
            f"anomaly:{a['id']}", "high",
            f"Unusual: {a['merchant']} {an.fmt_money(abs(a['amount']))}",
            f"{'; '.join(a['reasons'])} (posted {a['date']}).",
            "/insights",
        ))
    return out


def _bill_due(u: UserData, p: dict) -> list[Finding]:
    within = int(p.get("days", 3))
    cal = pl.bill_calendar(u.tx, None, u.as_of)
    out = []
    for day in cal["days"]:
        due = date.fromisoformat(day["date"])
        if not 0 <= (due - u.as_of).days <= within:
            continue
        for e in day["events"]:
            if e["status"] != "upcoming" or e["amount"] >= 0:
                continue
            out.append(Finding(
                f"bill_due:{e['name']}:{day['date']}", "low",
                f"{e['name']} is due {day['date']}",
                f"{an.fmt_money(abs(e['amount']))} expected on {day['date']}.",
                "/calendar",
            ))
    return out


def _safe_to_spend_low(u: UserData, p: dict) -> list[Finding]:
    floor = int(round(float(p["amount"]) * 100))
    s = an.safe_to_spend(u.tx, as_of=u.as_of)
    left = int(round(s["safe_to_spend"] * 100))
    if left >= floor:
        return []
    return [Finding(
        f"safe_low:{s['month']}", "medium",
        f"Only {an.fmt_cents(left)} safe to spend for the rest of {s['month']}",
        f"After {an.fmt_money(s['recurring_still_due'])} of recurring charges still due and your "
        f"{s['savings_target_pct']:.0f}% savings target, that's about "
        f"{an.fmt_money(s['per_day'])}/day for {s['days_left']} days.",
        "/",
    )]


RULE_TYPES: dict[str, RuleSpec] = {
    r.key: r for r in [
        RuleSpec("category_over", "Category spending cap",
                 "Alert when a category passes an amount in a calendar month.",
                 _category_over, {"category": ("category", "Category", "Dining"),
                                  "limit": ("money", "Monthly limit", 10000)}),
        RuleSpec("budget_risk", "Budget at risk",
                 "Alert when a budget is exceeded, or is on pace to be before month end.",
                 _budget_risk),
        RuleSpec("large_transaction", "Large charge",
                 "Alert on any single charge above an amount.",
                 _large_transaction, {"amount": ("money", "Charge above", 20000),
                                      "days": ("days", "Look back (days)", 7)}),
        RuleSpec("new_subscription", "New subscription",
                 "Alert when a recurring charge appears that wasn't there before — including silent "
                 "trial conversions.",
                 _subscription_flags({"new"}, "medium")),
        RuleSpec("price_increase", "Subscription price rise",
                 "Alert when a subscription starts charging more than it used to.",
                 _subscription_flags({"price_increase"}, "high")),
        RuleSpec("duplicate_billing", "Duplicate billing",
                 "Alert when the same service is billed on more than one account.",
                 _subscription_flags({"duplicate"}, "high")),
        RuleSpec("upcoming_renewal", "Annual renewal due",
                 "Alert before an annual or half-yearly subscription renews, while there's still "
                 "time to cancel.",
                 _subscription_flags({"upcoming_renewal"}, "medium")),
        RuleSpec("low_balance", "Cash running low",
                 "Alert when the cash-flow forecast dips below an amount.",
                 _low_balance, {"amount": ("money", "Balance floor", 50000),
                                "days": ("days", "Forecast window (days)", 30)}),
        RuleSpec("unusual_charge", "Unusual charge",
                 "Alert on outlier amounts, duplicate charges and unrecognised merchants.",
                 _unusual_charge, {"days": ("days", "Look back (days)", 14)}),
        RuleSpec("bill_due", "Bill due soon",
                 "A reminder a few days before each recurring bill lands.",
                 _bill_due, {"days": ("days", "Days ahead", 3)}),
        RuleSpec("safe_to_spend_low", "Safe-to-spend low",
                 "Alert when what's left for the month drops below an amount.",
                 _safe_to_spend_low, {"amount": ("money", "Alert below", 10000)}),
    ]
}

#: Created for every new account so the watchers do something useful before anyone configures them.
#: Ordered most valuable first, because a free plan gets only as many as its limit allows — the rest
#: are part of what Pro unlocks. Thresholds that depend on the user's own numbers are set by
#: `default_rules` below.
STARTER_RULES = ("budget_risk", "price_increase", "duplicate_billing", "new_subscription",
                 "upcoming_renewal", "unusual_charge")


class RuleError(ValueError):
    """A rule's parameters are missing or out of range."""


def validate_params(kind: str, raw: dict | None) -> dict:
    """Coerce and bounds-check a rule's parameters, filling in defaults for anything omitted."""
    spec = RULE_TYPES.get(kind)
    if spec is None:
        raise RuleError(f"Unknown alert type '{kind}'. Choose one of: {', '.join(RULE_TYPES)}")
    raw = raw or {}
    out: dict = {}
    for name, (ptype, label, default) in spec.params.items():
        value = raw.get(name, default)
        if ptype == "category":
            from .categorizer import CATEGORIES
            if value not in CATEGORIES:
                raise RuleError(f"{label}: '{value}' is not a category")
            out[name] = value
        elif ptype == "days":
            try:
                out[name] = max(1, min(int(value), 180))
            except (TypeError, ValueError):
                raise RuleError(f"{label} must be a whole number of days") from None
        else:  # money / percent
            try:
                amount = float(value)
            except (TypeError, ValueError):
                raise RuleError(f"{label} must be a number") from None
            if amount <= 0:
                raise RuleError(f"{label} must be greater than zero")
            out[name] = round(amount, 2)
    return out


def default_rules(u: UserData) -> list[tuple[str, dict]]:
    """The starter rule set, with thresholds scaled to this user's own spending."""
    rules: list[tuple[str, dict]] = [(k, validate_params(k, None)) for k in STARTER_RULES]
    sp = an.spending_frame(u.tx)
    if not sp.empty:
        # "large" means large *for this user*: the 99th percentile of what they actually spend,
        # rounded to a tidy figure, so the alert is rare rather than constant
        p99 = float(sp["spend_cents"].quantile(0.99))
        step = cur.rounding_step(50) * 100
        threshold = max(step, int(round(p99 / step)) * step)
        rules.append(("large_transaction", validate_params("large_transaction",
                                                           {"amount": threshold / 100, "days": 7})))
    return rules


def ensure_default_rules(user_id: int) -> int:
    """Install the starter rules for a user who has none. Returns how many were created.

    Capped at the plan's rule limit, so a free account is never seeded past a quota it would then
    be blocked from adding to — the watchers it doesn't get are part of what upgrading buys.
    """
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM alert_rules WHERE user_id = ?", (user_id,)).fetchone():
            return 0
    u = UserData(user_id)
    rules = default_rules(u)
    limit = plans.limit_for(u.plan, "alert_rules")
    if limit is not None:
        rules = rules[:limit]
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO alert_rules (user_id, kind, params_json, channels) VALUES (?,?,?, 'inapp')",
            [(user_id, kind, json.dumps(params)) for kind, params in rules],
        )
    return len(rules)


# ----------------------------------------------------------------------------- the sweep

def _deliverable(u: UserData, channels: str) -> set[str]:
    """Channels this user's plan actually allows. Email delivery is a paid feature."""
    wanted = {c.strip() for c in channels.split(",") if c.strip()}
    if not plans.allows(u.plan, "alert_email"):
        wanted.discard("email")
    return wanted


def run_user(user_id: int, force: bool = False, now: datetime | None = None) -> dict:
    """Evaluate every active rule for one user and store the findings it hasn't reported yet."""
    now = now or _now()
    u = UserData(user_id)
    cur.set_currency(u.currency)   # alert copy is written in the user's own currency
    with get_conn() as conn:
        state = conn.execute("SELECT * FROM automation_state WHERE user_id = ?", (user_id,)).fetchone()
        rows = conn.execute("SELECT * FROM alert_rules WHERE user_id = ? AND active = 1 ORDER BY id",
                            (user_id,)).fetchall()
    if not force and state and state["last_run_at"]:
        if datetime.fromisoformat(state["last_run_at"]) > now - SWEEP_INTERVAL:
            return {"skipped": "not due", "created": 0}

    created: list[dict] = []
    for row in rows:
        spec = RULE_TYPES.get(row["kind"])
        if spec is None:
            continue
        try:
            findings = spec.evaluate(u, json.loads(row["params_json"] or "{}"))
        except Exception:
            # a single broken rule must not silence the others
            log.exception("Rule %s (#%s) failed for user %s", row["kind"], row["id"], user_id)
            continue
        channels = _deliverable(u, row["channels"])
        for f in findings:
            with get_conn() as conn:
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO notifications
                       (user_id, rule_id, kind, severity, title, body, action_url, dedupe_key)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (user_id, row["id"], row["kind"], f.severity, f.title, f.body,
                     f.action_url, f.dedupe_key))
                if not cursor.rowcount:          # already reported: OR IGNORE hit the UNIQUE key
                    continue
                notification_id = cursor.lastrowid
            created.append({"id": notification_id, "severity": f.severity, "title": f.title,
                            "body": f.body, "email": "email" in channels})

    _mark_run(user_id, now)
    emailed = _email_alerts(u, [c for c in created if c["email"] and c["severity"] == "high"])
    return {"created": len(created), "emailed": emailed, "items": created}


def _mark_run(user_id: int, now: datetime, error: str | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO automation_state (user_id, last_run_at, last_error) VALUES (?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET last_run_at = excluded.last_run_at,
                                                  last_error = excluded.last_error""",
            (user_id, now.isoformat(timespec="seconds"), error))


def _email_alerts(u: UserData, items: list[dict]) -> int:
    """Email only high-severity alerts as they happen; everything else waits for the digest."""
    if not items:
        return 0
    with get_conn() as conn:
        row = conn.execute("SELECT name, email, email_verified_at FROM users WHERE id = ?",
                           (u.user_id,)).fetchone()
    if not row or not row["email"] or not row["email_verified_at"]:
        return 0
    mailer.send_alerts(row["email"], row["name"], items, f"{settings.app_url}/insights")
    with get_conn() as conn:
        conn.executemany("UPDATE notifications SET emailed_at = datetime('now') WHERE id = ?",
                         [(i["id"],) for i in items])
    return len(items)


def active_user_ids(now: datetime) -> list[int]:
    """Users worth sweeping: real accounts with data, not swept within `SWEEP_INTERVAL`.

    Demo sandboxes are skipped — they're throwaway, and their synthetic data would generate a burst
    of alerts nobody asked for.
    """
    cutoff = (now - SWEEP_INTERVAL).isoformat(timespec="seconds")
    with get_conn() as conn:
        return [r[0] for r in conn.execute(
            """SELECT u.id FROM users u
               LEFT JOIN automation_state s ON s.user_id = u.id
               WHERE u.is_demo = 0
                 AND EXISTS (SELECT 1 FROM alert_rules r WHERE r.user_id = u.id AND r.active = 1)
                 AND (s.last_run_at IS NULL OR s.last_run_at < ?)""", (cutoff,))]


def run_due(now: datetime | None = None) -> dict:
    """One sweep across every user who is due. Safe to call from any process, as often as you like."""
    now = now or _now()
    swept = created = failed = 0
    for user_id in active_user_ids(now):
        try:
            result = run_user(user_id, force=True, now=now)
            created += result.get("created", 0)
            swept += 1
        except Exception as exc:
            failed += 1
            log.exception("Automation sweep failed for user %s", user_id)
            _mark_run(user_id, now, f"{type(exc).__name__}: {exc}")
    if swept or failed:
        log.info("Automation sweep: %s users, %s alerts, %s failures", swept, created, failed)
    return {"users": swept, "created": created, "failed": failed}


# ----------------------------------------------------------------------------- notification inbox

def list_notifications(user_id: int, limit: int = 50, unread_only: bool = False) -> dict:
    where = "WHERE user_id = ?" + (" AND read_at IS NULL" if unread_only else "")
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT id, kind, severity, title, body, action_url, read_at, created_at
                FROM notifications {where} ORDER BY id DESC LIMIT ?""",
            (user_id, min(limit, 200))).fetchall()
        unread = conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read_at IS NULL",
            (user_id,)).fetchone()[0]
    return {"unread": unread,
            "items": [{**dict(r), "read": r["read_at"] is not None} for r in rows]}


def build_digest(u: UserData, period: str = "weekly") -> dict:
    """The recurring money summary, assembled entirely from deterministic analytics.

    Nothing here is written by a model. `narrate` below can add one opening sentence, but it is
    checked against these same figures before it is used.
    """
    cur.set_currency(u.currency)
    days = 7 if period == "weekly" else 30
    since = u.as_of - timedelta(days=days)
    sp = an.spending_frame(u.tx)
    window = sp[sp["date"].dt.date > since]
    spent = int(window["spend_cents"].sum())
    prior = sp[(sp["date"].dt.date > since - timedelta(days=days)) & (sp["date"].dt.date <= since)]
    prior_spent = int(prior["spend_cents"].sum())

    by_category = (window.groupby("category")["spend_cents"].sum().sort_values(ascending=False))
    health = an.health_score(u.tx, u.accounts, u.budgets, u.as_of)
    sts = an.safe_to_spend(u.tx, as_of=u.as_of)
    with get_conn() as conn:
        alerts = conn.execute(
            """SELECT severity, title, body FROM notifications
               WHERE user_id = ? AND created_at > datetime('now', ?) ORDER BY id DESC LIMIT 6""",
            (u.user_id, f"-{days} days")).fetchall()

    return {
        "period": period,
        "days": days,
        "as_of": u.as_of.isoformat(),
        "currency": u.currency,
        "spent": an.money(spent),
        "prior_spent": an.money(prior_spent),
        "change_pct": an.pct(spent - prior_spent, prior_spent) if prior_spent else None,
        "top_categories": [{"category": c, "amount": an.money(int(v)), "amount_text": an.fmt_cents(int(v))}
                           for c, v in by_category.head(4).items()],
        "health_score": health["score"],
        "health_grade": health["grade"],
        "safe_to_spend": sts["safe_to_spend"],
        "per_day": sts["per_day"],
        "days_left": sts["days_left"],
        "alerts": [dict(a) for a in alerts],
        # pre-formatted so the email templates never have to know about currency at all
        "text": {"spent": an.fmt_cents(spent), "prior_spent": an.fmt_cents(prior_spent),
                 "safe_to_spend": an.fmt_money(sts["safe_to_spend"]), "per_day": an.fmt_money(sts["per_day"])},
    }


def narrate(u: UserData, digest: dict) -> str | None:
    """One human opening line for the digest, written by the model and then verified.

    The model sees only the finished digest figures, so it has nothing to do but phrase them. The
    agent's own grounding guard then checks every number it wrote against those figures; if any
    figure isn't one of them, the sentence is dropped rather than shown. A digest with a plain
    deterministic opening is strictly better than one with an invented number in it.
    """
    if not settings.llm_enabled:
        return None
    try:
        from langchain_openai import ChatOpenAI

        from .agent import check_grounding, source_numbers

        facts = json.dumps({k: v for k, v in digest.items() if k != "alerts"}, default=str)
        prompt = (
            "Write ONE warm, plain sentence (max 25 words) opening a personal-finance digest email. "
            "Use at most one number, copied exactly from the data. No greeting, no sign-off, no emoji.\n"
            f"Data: {facts}"
        )
        llm = ChatOpenAI(model=settings.openai_model, temperature=0.4, api_key=settings.openai_api_key)
        line = str(llm.invoke(prompt).content).strip().strip('"')
        if check_grounding(line, source_numbers([facts])):
            log.info("Digest narration dropped for user %s: ungrounded figure", u.user_id)
            return None
        return line
    except Exception as exc:
        log.warning("Digest narration failed: %s", exc)
        return None


def digest_due(state, now: datetime) -> bool:
    period = (state["digest_period"] if state else "weekly") or "weekly"
    if period == "off":
        return False
    last = state["last_digest_at"] if state else None
    if not last:
        return True
    return datetime.fromisoformat(last) < now - timedelta(days=7 if period == "weekly" else 30)


def send_digest(user_id: int, now: datetime | None = None, force: bool = False) -> bool:
    """Email one user their digest if it's due. Returns whether it was sent."""
    now = now or _now()
    u = UserData(user_id)
    if not plans.allows(u.plan, "digest"):
        return False
    with get_conn() as conn:
        state = conn.execute("SELECT * FROM automation_state WHERE user_id = ?", (user_id,)).fetchone()
        row = conn.execute("SELECT name, email, email_verified_at FROM users WHERE id = ?",
                           (user_id,)).fetchone()
    if not row or not row["email"] or not row["email_verified_at"]:
        return False
    if not force and not digest_due(state, now):
        return False
    digest = build_digest(u, (state["digest_period"] if state else "weekly") or "weekly")
    mailer.send_digest(row["email"], row["name"], digest, narrate(u, digest), f"{settings.app_url}/")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO automation_state (user_id, last_digest_at) VALUES (?,?)
               ON CONFLICT(user_id) DO UPDATE SET last_digest_at = excluded.last_digest_at""",
            (user_id, now.isoformat(timespec="seconds")))
    return True


def send_due_digests(now: datetime | None = None) -> int:
    now = now or _now()
    sent = 0
    with get_conn() as conn:
        ids = [r[0] for r in conn.execute(
            """SELECT u.id FROM users u LEFT JOIN automation_state s ON s.user_id = u.id
               WHERE u.is_demo = 0 AND u.email IS NOT NULL AND u.email_verified_at IS NOT NULL
                 AND COALESCE(s.digest_period, 'weekly') != 'off'""")]
    for user_id in ids:
        try:
            sent += bool(send_digest(user_id, now))
        except Exception:
            log.exception("Digest failed for user %s", user_id)
    return sent


def set_digest_period(user_id: int, period: str) -> None:
    if period not in ("off", "weekly", "monthly"):
        raise RuleError("Digest period must be off, weekly or monthly")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO automation_state (user_id, digest_period) VALUES (?,?)
               ON CONFLICT(user_id) DO UPDATE SET digest_period = excluded.digest_period""",
            (user_id, period))


def mark_read(user_id: int, notification_id: int | None = None) -> int:
    """Mark one notification read, or all of them when `notification_id` is None."""
    with get_conn() as conn:
        if notification_id is None:
            cursor = conn.execute(
                "UPDATE notifications SET read_at = datetime('now') WHERE user_id = ? AND read_at IS NULL",
                (user_id,))
        else:
            cursor = conn.execute(
                "UPDATE notifications SET read_at = datetime('now') WHERE user_id = ? AND id = ?",
                (user_id, notification_id))
        return cursor.rowcount
