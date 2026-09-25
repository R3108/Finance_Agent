"""“Can I afford it?” — a purchase checked against the user's real cash flow, before they buy.

Most money apps tell you what already happened. This answers the question people actually ask at
the checkout: *if I buy this now, what breaks?* It replays the 90-day cash-flow forecast with the
purchase added (once, or every month for a new subscription or EMI) and reports, deterministically:

* the lowest balance before and after, and the day it happens;
* this month's safe-to-spend before and after;
* how many months later each savings goal lands;
* how much of the emergency cushion it uses;
* a verdict — comfortable / tight / not now — with the reasons, and for a one-off purchase that
  doesn't fit today, the **earliest date it would** (usually just after the next paycheck).

Everything is integer minor units and reuses `analytics`; nothing here is estimated by a model.
The agent exposes it as the `can_i_afford` tool, so "can I afford a ₹60,000 phone?" gets the same
answer in chat as on the page.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd

from . import analytics as an

HORIZON_DAYS = 90
#: The balance the verdict tries to keep: this many days of the user's own day-to-day spending.
CUSHION_DAYS = 30


class AffordError(Exception):
    pass


def _charge_dates(when: date, recurring: bool, end: date) -> list[date]:
    if not recurring:
        return [when]
    out, n = [], 0
    while True:
        d = (pd.Timestamp(when) + pd.DateOffset(months=n)).date()
        if d > end:
            return out
        out.append(d)
        n += 1


def check(tx: pd.DataFrame, accounts: pd.DataFrame, goals: pd.DataFrame, amount_cents: int,
          when: date | None = None, recurring: bool = False, as_of: date | None = None,
          label: str | None = None) -> dict:
    as_of = an.resolve_as_of(tx, as_of)
    if amount_cents <= 0:
        raise AffordError("Enter an amount greater than zero.")
    when = max(when or as_of, as_of)
    end = as_of + timedelta(days=HORIZON_DAYS)
    if when > end:
        raise AffordError(f"Pick a date within the next {HORIZON_DAYS} days — the forecast doesn't reach further.")

    fc = an.cashflow_forecast(tx, accounts, HORIZON_DAYS, as_of)
    start = int(round(fc["starting_balance"] * 100))
    days = [as_of] + [date.fromisoformat(s["date"]) for s in fc["series"]]
    base = [start] + [int(round(s["balance"] * 100)) for s in fc["series"]]
    charges = _charge_dates(when, recurring, end)

    def with_purchase(on: list[date]) -> list[int]:
        return [b - amount_cents * sum(1 for c in on if c <= d) for d, b in zip(days, base)]

    after = with_purchase(charges)
    low_before = min(range(len(base)), key=lambda i: base[i])
    low_after = min(range(len(after)), key=lambda i: after[i])
    cushion = fc["avg_daily_variable_spend"] * 100 * CUSHION_DAYS
    cushion = int(round(cushion))

    # this month's safe-to-spend, if the (first) charge lands this month
    sts = an.safe_to_spend(tx, as_of=as_of)
    sts_before = int(round(sts["safe_to_spend"] * 100))
    this_month = sum(1 for c in charges if an.month_str(c) == an.month_str(as_of))
    sts_after = sts_before - amount_cents * this_month

    # goals: a one-off delays them by the amount; a recurring charge shrinks the monthly surplus
    surplus = an.avg_monthly_surplus(tx, as_of)
    monthly_cost = amount_cents if recurring else 0
    goal_rows = []
    before_goals = an.goals_progress(tx, goals, as_of)
    after_goals = an.goals_progress(tx, goals, as_of, -monthly_cost) if recurring else before_goals
    for b, a in zip(before_goals, after_goals):
        remaining = int(round(b["remaining"] * 100))
        if remaining <= 0:
            continue
        m_before = b["months_to_goal_at_full_surplus"]
        if recurring:
            m_after, on_track_after = a["months_to_goal_at_full_surplus"], a["on_track"]
        else:
            m_after = math.ceil((remaining + amount_cents) / surplus) if surplus > 0 else None
            required = math.ceil((remaining + amount_cents) / max(1, b["months_left"]))
            on_track_after = surplus >= required
        goal_rows.append({"goal": b["name"], "months_before": m_before, "months_after": m_after,
                          "delay_months": (m_after - m_before) if m_before is not None and m_after is not None else None,
                          "on_track_before": b["on_track"], "on_track_after": on_track_after})

    liquid = int(round(an.account_balances(tx, accounts)["liquid_cash"] * 100))
    avg_spend_rows = an.monthly_summary(tx, 4, as_of)[:-1]
    avg_monthly_spend = int(round(sum(r["spending"] * 100 for r in avg_spend_rows) / len(avg_spend_rows))) if avg_spend_rows else 0
    ef_before = liquid / avg_monthly_spend if avg_monthly_spend else None
    ef_after = (liquid - (0 if recurring else amount_cents)) / avg_monthly_spend if avg_monthly_spend else None

    # ---- verdict
    reasons: list[str] = []
    what = label or "this"
    if after[low_after] < 0:
        verdict = "not_now"
        reasons.append(f"Your cash would go negative: about {an.fmt_cents(after[low_after])} on "
                       f"{days[low_after].isoformat()}.")
    else:
        verdict = "comfortable"
        if after[low_after] < cushion:
            verdict = "tight"
            reasons.append(f"Your lowest balance would drop to {an.fmt_cents(after[low_after])} on "
                           f"{days[low_after].isoformat()}, under a month of everyday spending "
                           f"({an.fmt_cents(cushion)}).")
        if this_month and sts_after < 0:
            verdict = "tight"
            reasons.append(f"It would take this month's safe-to-spend from {an.fmt_cents(sts_before)} to "
                           f"{an.fmt_cents(sts_after)}, eating into your savings target." if sts_before >= 0 else
                           f"You're already {an.fmt_cents(-sts_before)} past this month's safe-to-spend, so it would "
                           f"come out of your savings target.")
        flipped = [g["goal"] for g in goal_rows if g["on_track_before"] and not g["on_track_after"]]
        if flipped:
            verdict = "tight"
            reasons.append(f"It would put {', '.join(flipped)} behind schedule.")
    if recurring and amount_cents >= surplus:
        # a monthly cost the surplus can't carry fails eventually, even if the next 90 days look fine
        verdict = "not_now" if amount_cents > surplus else ("tight" if verdict == "comfortable" else verdict)
        reasons.append(f"At {an.fmt_cents(amount_cents)} a month it needs more than your usual monthly surplus "
                       f"({an.fmt_cents(surplus)})." if amount_cents > surplus else
                       f"At {an.fmt_cents(amount_cents)} a month it uses all of your usual monthly surplus.")
    if verdict == "comfortable":
        reasons.append(f"Your balance stays above {an.fmt_cents(after[low_after])} for the next {HORIZON_DAYS} days "
                       f"with {what} included.")

    # ---- the earliest date a one-off purchase would fit comfortably
    wait_until = None
    if verdict != "comfortable" and not recurring:
        floor = max(cushion, 0)
        for i, d in enumerate(days):
            if d < when:
                continue
            trial = with_purchase([d])
            if min(trial) >= floor and (an.month_str(d) != an.month_str(as_of) or sts_before - amount_cents >= 0):
                wait_until = d.isoformat()
                break

    return {
        "as_of": as_of.isoformat(), "label": label, "amount": an.money(amount_cents), "date": when.isoformat(),
        "recurring": recurring, "annual_cost": an.money(amount_cents * 12) if recurring else None,
        "verdict": verdict, "reasons": reasons, "wait_until": wait_until,
        "lowest_balance_before": an.money(base[low_before]), "lowest_balance_before_date": days[low_before].isoformat(),
        "lowest_balance_after": an.money(after[low_after]), "lowest_balance_after_date": days[low_after].isoformat(),
        "cushion": an.money(cushion), "cushion_days": CUSHION_DAYS,
        "safe_to_spend_before": an.money(sts_before), "safe_to_spend_after": an.money(sts_after),
        "monthly_surplus_before": an.money(surplus), "monthly_surplus_after": an.money(surplus - monthly_cost),
        "emergency_fund_months_before": round(ef_before, 1) if ef_before is not None else None,
        "emergency_fund_months_after": round(ef_after, 1) if ef_after is not None else None,
        "goals": goal_rows,
        "series": [{"date": d.isoformat(), "before": an.money(b), "after": an.money(a)}
                   for d, b, a in zip(days, base, after)],
    }
