"""Deterministic planning features: debt payoff, net worth, bill calendar, savings challenges, year in review.

Same contract as `analytics.py`: integer cents in, integer cents through every step, dollars only at
the output boundary via `money()`. The agent and the UI read every figure from here.
"""
from __future__ import annotations

import calendar
import json
from datetime import date, timedelta

import pandas as pd

from . import analytics as an
from .analytics import money, pct
from .categorizer import CATEGORIES, ESSENTIAL_CATEGORIES

# Spending you can skip for a day without consequences — used by no-spend days and challenges.
DISCRETIONARY_CATEGORIES = set(CATEGORIES) - ESSENTIAL_CATEGORIES - an.FIXED_CATEGORIES - {"Income", "Transfers", "Fees"}


def _add_months(d: date, n: int) -> date:
    return (pd.Timestamp(d) + pd.DateOffset(months=n)).date()


def _month_bounds(month: str) -> tuple[date, date]:
    y, m = map(int, month.split("-"))
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def _discretionary_daily(df: pd.DataFrame) -> pd.Series:
    """Discretionary spend per calendar day (cents), only days with spending."""
    sp = an.spending_frame(df)
    sp = sp[sp["category"].isin(DISCRETIONARY_CATEGORIES) & (sp["spend_cents"] > 0)]
    return sp.groupby(sp["date"].dt.date)["spend_cents"].sum()


def _no_spend_days(daily: pd.Series, start: date, end: date) -> tuple[int, int]:
    """(days without discretionary spend, longest consecutive run) in [start, end]."""
    if end < start:
        return 0, 0
    spend_days = set(daily.index)
    count = run = best = 0
    d = start
    while d <= end:
        if d in spend_days:
            run = 0
        else:
            count += 1
            run += 1
            best = max(best, run)
        d += timedelta(days=1)
    return count, best


# ----------------------------------------------------------------------------- debt payoff

STRATEGIES = {
    "avalanche": "Highest interest rate first — the least total interest.",
    "snowball": "Smallest balance first — the quickest early wins.",
    "minimum": "Minimum payments only — the baseline to beat.",
}
MAX_MONTHS = 600


def _monthly_interest(balance_cents: int, apr_bps: int) -> int:
    """One month of interest on a balance, rounded half-up to the cent (APR / 12, in basis points)."""
    return (balance_cents * apr_bps + 60_000) // 120_000


def _simulate_payoff(debts: list[dict], extra_cents: int, strategy: str) -> dict:
    bal = {d["id"]: d["balance_cents"] for d in debts}
    if strategy == "avalanche":
        order = sorted(debts, key=lambda d: (-d["apr_bps"], d["balance_cents"]))
    else:
        order = sorted(debts, key=lambda d: (d["balance_cents"], -d["apr_bps"]))
    # Freed-up minimums roll into the next target, so the monthly budget stays constant.
    budget = sum(d["min_payment_cents"] for d in debts) + (extra_cents if strategy != "minimum" else 0)
    paid_off: dict[int, int] = {}
    interest_total = paid_total = month = 0
    timeline = [sum(bal.values())]
    while any(b > 0 for b in bal.values()) and month < MAX_MONTHS:
        month += 1
        for d in debts:
            if bal[d["id"]] > 0:
                i = _monthly_interest(bal[d["id"]], d["apr_bps"])
                bal[d["id"]] += i
                interest_total += i
        remaining = budget
        for d in debts:  # minimums first on every debt
            pay = min(d["min_payment_cents"], bal[d["id"]], remaining)
            bal[d["id"]] -= pay
            remaining -= pay
            paid_total += pay
        if strategy != "minimum":
            for d in order:  # then everything left goes to the priority target
                if remaining <= 0:
                    break
                pay = min(bal[d["id"]], remaining)
                bal[d["id"]] -= pay
                remaining -= pay
                paid_total += pay
        for d in debts:
            if bal[d["id"]] == 0 and d["id"] not in paid_off:
                paid_off[d["id"]] = month
        timeline.append(sum(bal.values()))
    return {"months": month, "feasible": len(paid_off) == len(debts), "paid_off": paid_off,
            "interest_cents": interest_total, "paid_cents": paid_total, "timeline": timeline}


def debt_summary(debts: pd.DataFrame) -> dict:
    items = [{"id": int(d.id), "name": d.name, "kind": d.kind, "balance": money(d.balance_cents),
              "apr_pct": round(int(d.apr_bps) / 100, 2), "min_payment": money(d.min_payment_cents),
              "monthly_interest_now": money(_monthly_interest(int(d.balance_cents), int(d.apr_bps)))}
             for d in debts.itertuples()]
    total = int(debts["balance_cents"].sum()) if not debts.empty else 0
    weighted_bps = int(round((debts["balance_cents"] * debts["apr_bps"]).sum() / total)) if total else 0
    return {
        "debts": items, "total_debt": money(total), "weighted_apr_pct": round(weighted_bps / 100, 2),
        "total_min_payment": money(int(debts["min_payment_cents"].sum()) if not debts.empty else 0),
        "monthly_interest_now": money(sum(_monthly_interest(int(d.balance_cents), int(d.apr_bps)) for d in debts.itertuples())),
    }


def debt_payoff_plan(debts: pd.DataFrame, extra_monthly_cents: int = 0, as_of: date | None = None,
                     df: pd.DataFrame | None = None) -> dict:
    """Compare avalanche, snowball and minimum-only payoff month by month (exact cents)."""
    as_of = as_of or date.today()
    rows = [{"id": int(d.id), "name": d.name, "balance_cents": int(d.balance_cents), "apr_bps": int(d.apr_bps),
             "min_payment_cents": int(d.min_payment_cents)} for d in debts.itertuples() if int(d.balance_cents) > 0]
    summary = debt_summary(debts)
    surplus = an.avg_monthly_surplus(df, as_of) if df is not None else None
    if not rows:
        return {**summary, "extra_monthly": money(extra_monthly_cents), "strategies": {}, "chart": [],
                "recommended": None, "avg_monthly_surplus": money(surplus) if surplus is not None else None}
    names = {r["id"]: r["name"] for r in rows}
    sims = {s: _simulate_payoff(rows, max(0, extra_monthly_cents), s) for s in STRATEGIES}
    base = sims["minimum"]
    strategies = {}
    for s, r in sims.items():
        order = sorted(r["paid_off"].items(), key=lambda kv: kv[1])
        strategies[s] = {
            "description": STRATEGIES[s], "feasible": r["feasible"], "months": r["months"] if r["feasible"] else None,
            "debt_free_date": _add_months(as_of, r["months"]).strftime("%Y-%m") if r["feasible"] else None,
            "total_interest": money(r["interest_cents"]), "total_paid": money(r["paid_cents"]),
            "interest_saved_vs_minimum": money(base["interest_cents"] - r["interest_cents"]) if base["feasible"] else None,
            "months_saved_vs_minimum": base["months"] - r["months"] if base["feasible"] and r["feasible"] else None,
            "first_win_month": order[0][1] if order else None,
            "payoff_order": [{"name": names[i], "month": m, "date": _add_months(as_of, m).strftime("%Y-%m")} for i, m in order],
        }
    horizon = min(max(len(r["timeline"]) for r in sims.values()), 361)
    chart = [{"month": _add_months(as_of, i).strftime("%Y-%m"),
              **{s: money(r["timeline"][i]) if i < len(r["timeline"]) else 0.0 for s, r in sims.items()}}
             for i in range(horizon)]
    feasible = [s for s in ("avalanche", "snowball") if sims[s]["feasible"]]
    recommended = min(feasible, key=lambda s: (sims[s]["interest_cents"], sims[s]["months"])) if feasible else None
    return {**summary, "extra_monthly": money(extra_monthly_cents), "strategies": strategies, "chart": chart,
            "recommended": recommended, "avg_monthly_surplus": money(surplus) if surplus is not None else None,
            "avalanche_vs_snowball_interest": money(sims["snowball"]["interest_cents"] - sims["avalanche"]["interest_cents"])}


# ----------------------------------------------------------------------------- net worth

def net_worth(df: pd.DataFrame, accounts: pd.DataFrame, assets: pd.DataFrame, debts: pd.DataFrame,
              as_of: date | None = None, months: int = 12) -> dict:
    as_of = an.resolve_as_of(df, as_of)
    balances = an.account_balances(df, accounts)
    acct_assets = sum(int(round(a["balance"] * 100)) for a in balances["accounts"] if a["balance"] >= 0)
    acct_liabilities = -sum(int(round(a["balance"] * 100)) for a in balances["accounts"] if a["balance"] < 0)
    manual_assets = int(assets["value_cents"].sum()) if not assets.empty else 0
    debt_total = int(debts["balance_cents"].sum()) if not debts.empty else 0
    total_assets = acct_assets + manual_assets
    total_liabilities = acct_liabilities + debt_total

    by_kind: dict[str, int] = {"cash": acct_assets}
    for a in assets.itertuples():
        by_kind[a.kind] = by_kind.get(a.kind, 0) + int(a.value_cents)

    # month-end cash across all accounts, rebuilt from opening balances + transactions
    opening = int(accounts["opening_balance_cents"].sum()) if not accounts.empty else 0
    window = an.complete_months(as_of, months - 1) + [an.month_str(as_of)]
    daily = df.groupby(df["date"].dt.date)["amount_cents"].sum().sort_index().cumsum() if not df.empty else pd.Series(dtype="int64")
    history = []
    for m in window:
        end = min(_month_bounds(m)[1], as_of)
        upto = daily[daily.index <= end]
        cash = opening + (int(upto.iloc[-1]) if len(upto) else 0)
        history.append({"month": m, "net_cash": money(cash), "partial": m == an.month_str(as_of)})
    change = int(round((history[-1]["net_cash"] - history[0]["net_cash"]) * 100)) if history else 0
    return {
        "as_of": as_of.isoformat(),
        "net_worth": money(total_assets - total_liabilities),
        "total_assets": money(total_assets), "total_liabilities": money(total_liabilities),
        "cash": money(acct_assets), "manual_assets": money(manual_assets),
        "card_balances": money(acct_liabilities), "debts": money(debt_total),
        "debt_to_asset_pct": pct(total_liabilities, total_assets),
        "asset_mix": [{"kind": k, "value": money(v), "share_pct": pct(v, total_assets)} for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1]) if v],
        "assets": [{"id": int(a.id), "name": a.name, "kind": a.kind, "value": money(a.value_cents), "updated_at": a.updated_at}
                   for a in assets.itertuples()],
        "cash_history": history,
        "cash_change_period": money(change),
    }


# ----------------------------------------------------------------------------- bill calendar

def bill_calendar(df: pd.DataFrame, month: str | None = None, as_of: date | None = None) -> dict:
    """Every recurring charge and paycheck in a month: actual ones up to as_of, projected ones after."""
    as_of = an.resolve_as_of(df, as_of)
    month = month or an.month_str(as_of)
    first, last = _month_bounds(month)
    expenses = an.detect_recurring(df, as_of)
    incomes = an.detect_recurring(df, as_of, "income")
    kind_of = {r.key: r.kind for r in expenses + incomes}
    events: dict[date, list[dict]] = {}

    def add(d: date, name: str, cents: int, kind: str, status: str):
        events.setdefault(d, []).append({"name": name, "amount": money(cents), "kind": kind, "status": status})

    # actual charges already on the statement
    if first <= as_of:
        keys = df["merchant"] + "|" + df["account_id"].fillna(-1).astype("int64").astype(str).replace("-1", "")
        mask = keys.isin(kind_of.keys()) & (df["date"] >= pd.Timestamp(first)) & (df["date"] <= pd.Timestamp(min(last, as_of)))
        for row, key in zip(df[mask].itertuples(), keys[mask]):
            add(row.date.date(), row.merchant, int(row.amount_cents), kind_of[key], "posted")
    # projected charges still to come
    if last > as_of:
        for r in [x for x in expenses + incomes if x.active]:
            sign = 1 if r.kind == "income" else -1
            for d in an._occurrences(r, as_of, last):
                if d >= first:
                    add(d, r.merchant, sign * r.last_cents, r.kind, "upcoming")

    days = [{"date": d.isoformat(), "events": sorted(evs, key=lambda e: e["amount"])} for d, evs in sorted(events.items())]
    flat = [e for evs in events.values() for e in evs]
    out_c = -sum(int(round(e["amount"] * 100)) for e in flat if e["amount"] < 0)
    in_c = sum(int(round(e["amount"] * 100)) for e in flat if e["amount"] > 0)
    due_c = -sum(int(round(e["amount"] * 100)) for e in flat if e["amount"] < 0 and e["status"] == "upcoming")
    busiest = max(events.items(), key=lambda kv: -sum(e["amount"] for e in kv[1] if e["amount"] < 0), default=None)
    return {
        "month": month, "as_of": as_of.isoformat(), "first_weekday": first.weekday(), "days_in_month": last.day,
        "days": days, "recurring_out": money(out_c), "recurring_in": money(in_c), "still_due": money(due_c),
        "net_recurring": money(in_c - out_c), "event_count": len(flat),
        "heaviest_day": busiest[0].isoformat() if busiest else None,
        "earliest_month": df["date"].min().strftime("%Y-%m") if not df.empty else month,
        "latest_month": an.shift_month(an.month_str(as_of), 3),
    }


# ----------------------------------------------------------------------------- savings challenges

CHALLENGE_TYPES = {"no_spend", "category_cap", "merchant_break"}


def _baseline_daily(sp: pd.DataFrame, before: date, days: int = 90) -> float:
    window = sp[(sp["date"] < pd.Timestamp(before)) & (sp["date"] >= pd.Timestamp(before - timedelta(days=days)))]
    return float(window["spend_cents"].sum()) / days


def evaluate_challenge(ch: dict, df: pd.DataFrame, as_of: date) -> dict:
    """Score one challenge against real transactions. Savings are estimates vs your prior 90-day pace."""
    params = json.loads(ch["params_json"]) if isinstance(ch.get("params_json"), str) else ch.get("params", {})
    start, end = date.fromisoformat(ch["start_date"]), date.fromisoformat(ch["end_date"])
    total = (end - start).days + 1
    upto = min(as_of, end)
    elapsed = max(0, (upto - start).days + 1)
    left = total - elapsed
    finished = as_of >= end
    sp = an.spending_frame(df)
    in_window = sp[(sp["date"] >= pd.Timestamp(start)) & (sp["date"] <= pd.Timestamp(upto))]
    status, progress, detail, savings = "active", 0.0, "", 0

    if ch["type"] == "no_spend":
        target = int(params.get("target_days", 10))
        achieved, streak = _no_spend_days(_discretionary_daily(df), start, upto)
        disc = sp[sp["category"].isin(DISCRETIONARY_CATEGORIES)]
        savings = int(round(_baseline_daily(disc, start) * achieved))
        status = "won" if achieved >= target else "lost" if achieved + left < target else "active"
        progress = min(100.0, achieved / target * 100)
        detail = f"{achieved} of {target} no-spend days so far (best streak {streak}); {left} days left."
    elif ch["type"] == "category_cap":
        cat, cap = params["category"], int(params["cap_cents"])
        base_sp = sp[sp["category"] == cat]
        spent = int(in_window.loc[in_window["category"] == cat, "spend_cents"].sum())
        savings = max(0, int(round(_baseline_daily(base_sp, start) * elapsed)) - spent)
        status = "lost" if spent > cap else "won" if finished else "active"
        progress = min(100.0, spent / cap * 100) if cap else 100.0
        detail = f"{an.fmt_money(money(spent))} of {an.fmt_money(money(cap))} {cat} cap used; {an.fmt_money(money(max(0, cap - spent)))} left for {left} days."
    elif ch["type"] == "merchant_break":
        merchant = params["merchant"]
        base_sp = sp[sp["merchant"].str.lower() == merchant.lower()]
        hits = in_window[in_window["merchant"].str.lower() == merchant.lower()]
        spent = int(hits["spend_cents"].sum())
        savings = max(0, int(round(_baseline_daily(base_sp, start) * elapsed)) - spent)
        if len(hits):
            status = "lost"
            detail = f"Broken on {hits['date'].min().date().isoformat()} ({len(hits)} charge{'s' if len(hits) > 1 else ''} at {merchant})."
        else:
            status = "won" if finished else "active"
            detail = f"{elapsed} day{'s' if elapsed != 1 else ''} without {merchant}; {left} to go."
        progress = elapsed / total * 100
    return {
        "id": int(ch["id"]) if ch.get("id") is not None else None, "type": ch["type"], "title": ch["title"], "params": params,
        "start_date": start.isoformat(), "end_date": end.isoformat(), "days_total": total, "days_elapsed": elapsed,
        "days_left": left, "status": status, "progress_pct": round(progress, 1), "detail": detail,
        "estimated_savings": money(savings),
    }


def challenges_overview(challenges: pd.DataFrame, df: pd.DataFrame, as_of: date | None = None) -> dict:
    as_of = an.resolve_as_of(df, as_of)
    items = [evaluate_challenge(dict(r._asdict()), df, as_of) for r in challenges.itertuples(index=False)]
    won = [i for i in items if i["status"] == "won"]
    active = [i for i in items if i["status"] == "active"]
    saved = sum(int(round(i["estimated_savings"] * 100)) for i in items if i["status"] in ("won", "active"))
    # a "streak" of consecutive wins, newest first, for the gamified header
    streak = 0
    for i in sorted([i for i in items if i["status"] != "active"], key=lambda i: i["end_date"], reverse=True):
        if i["status"] != "won":
            break
        streak += 1
    return {
        "as_of": as_of.isoformat(), "items": items, "active": len(active), "won": len(won),
        "lost": sum(i["status"] == "lost" for i in items), "win_streak": streak,
        "estimated_savings_total": money(saved),
        "suggestions": suggest_challenges(df, as_of, items),
    }


def suggest_challenges(df: pd.DataFrame, as_of: date | None = None, existing: list[dict] | None = None) -> list[dict]:
    """Challenges tailored to the user's own habits, each with a projected saving."""
    as_of = an.resolve_as_of(df, as_of)
    taken = {(e["type"], json.dumps(e["params"], sort_keys=True)) for e in (existing or []) if e["status"] == "active"}
    taken_types = {t for t, _ in taken}
    sp = an.spending_frame(df)
    out: list[dict] = []

    # 1) cap the biggest discretionary category at 80% of a typical month
    months = an.complete_months(as_of, 6)
    piv = (sp[sp["month"].isin(months) & sp["category"].isin(DISCRETIONARY_CATEGORIES - {"Travel", "Uncategorized"})]
           .groupby(["category", "month"])["spend_cents"].sum().unstack(fill_value=0).reindex(columns=months, fill_value=0))
    if not piv.empty:
        med = piv.median(axis=1).sort_values(ascending=False)
        cat, typical = med.index[0], int(round(med.iloc[0]))
        cap = an._ceil_to(typical * 0.8, 5)
        if cap > 0 and "category_cap" not in taken_types:
            out.append({"type": "category_cap", "title": f"{cat} under {an.fmt_money(money(cap))} for 30 days",
                        "params": {"category": cat, "cap_cents": cap}, "duration_days": 30,
                        "why": f"{cat} is your biggest discretionary category (typical month {an.fmt_money(money(typical))}).",
                        "projected_savings": money(typical - cap)})

    # 2) take a break from the small habit you repeat most
    recent = sp[(sp["date"] > pd.Timestamp(as_of - timedelta(days=90))) & sp["category"].isin(DISCRETIONARY_CATEGORIES)]
    g = recent.groupby("merchant")["spend_cents"].agg(["sum", "count"])
    habits = g[(g["count"] >= 8) & (g["sum"] / g["count"] <= 2000)].sort_values("sum", ascending=False)
    if not habits.empty and "merchant_break" not in taken_types:
        merchant, row = habits.index[0], habits.iloc[0]
        projected = int(round(row["sum"] / 90 * 14))
        out.append({"type": "merchant_break", "title": f"14 days without {merchant}",
                    "params": {"merchant": merchant}, "duration_days": 14,
                    "why": f"{int(row['count'])} visits in the last 90 days, {an.fmt_money(money(int(row['sum'])))} in total.",
                    "projected_savings": money(projected)})

    # 3) more no-spend days than you currently manage
    daily = _discretionary_daily(df)
    current, _ = _no_spend_days(daily, as_of - timedelta(days=29), as_of)
    target = min(30, current + 4)
    disc = sp[sp["category"].isin(DISCRETIONARY_CATEGORIES)]
    per_day = _baseline_daily(disc, as_of + timedelta(days=1))
    if "no_spend" not in taken_types:
        out.append({"type": "no_spend", "title": f"{target} no-spend days this month",
                    "params": {"target_days": target}, "duration_days": 30,
                    "why": f"You had {current} days with no discretionary spending in the last 30 days.",
                    "projected_savings": money(int(round(per_day * (target - current))))})
    return out


# ----------------------------------------------------------------------------- year in review ("Money Wrapped")

COFFEE_PATTERN = r"coffee|starbucks|dunkin|blue bottle|peet"
DELIVERY_MERCHANTS = {"DoorDash", "Uber Eats", "Grubhub", "Instacart", "Postmates"}
PERSONAS = {
    "Dining": ("The Foodie", "Good meals are your love language — and your biggest lever."),
    "Shopping": ("The Collector", "You like nice things. A 48-hour wait rule could be your superpower."),
    "Travel": ("The Explorer", "You spend on experiences over things."),
    "Entertainment": ("The Fun-Seeker", "Movies, games and shows: you invest in a good time."),
    "Personal Care": ("The Self-Care Pro", "You put yourself first — sustainably, we hope."),
    "Education": ("The Lifelong Learner", "Your money goes into your head."),
    "Gifts & Donations": ("The Giver", "A big share of your spending goes to other people."),
}


def year_in_review(df: pd.DataFrame, as_of: date | None = None, year: int | None = None) -> dict:
    as_of = an.resolve_as_of(df, as_of)
    if year:
        months = [f"{year}-{m:02d}" for m in range(1, 13) if f"{year}-{m:02d}" <= an.month_str(as_of)]
        label = str(year)
    else:
        months = an.complete_months(as_of, 12)
        label = "Last 12 months"
    if not months:
        raise ValueError("No data for that period")
    start, end = _month_bounds(months[0])[0], min(_month_bounds(months[-1])[1], as_of)
    sp = an.spending_frame(df)
    sp = sp[(sp["date"] >= pd.Timestamp(start)) & (sp["date"] <= pd.Timestamp(end))]
    inc = an.income_frame(df)
    inc = inc[(inc["date"] >= pd.Timestamp(start)) & (inc["date"] <= pd.Timestamp(end))]
    spent, earned = int(sp["spend_cents"].sum()), int(inc["amount_cents"].sum())
    variable = sp[~sp["category"].isin(an.FIXED_CATEGORIES) & (sp["spend_cents"] > 0)]

    by_cat = sp.groupby("category")["spend_cents"].sum().sort_values(ascending=False)
    disc_cat = by_cat[by_cat.index.isin(DISCRETIONARY_CATEGORIES - {"Uncategorized"})]
    by_merchant = variable.groupby("merchant")["spend_cents"].agg(["sum", "count"])
    top_m = by_merchant.sort_values("sum", ascending=False).head(1)
    visited = by_merchant.sort_values(["count", "sum"], ascending=False).head(1)
    biggest = variable.sort_values("spend_cents", ascending=False).head(1)
    monthly = sp.groupby("month")["spend_cents"].sum().reindex(months, fill_value=0)
    full = monthly[[m for m in months if m != an.month_str(as_of)]]
    coffee = variable[variable["merchant"].str.contains(COFFEE_PATTERN, case=False, regex=True)]
    delivery = variable[variable["merchant"].isin(DELIVERY_MERCHANTS)]
    subs = sp[sp["category"] == "Subscriptions"]
    no_spend, streak = _no_spend_days(_discretionary_daily(df), start, end)
    weekday = variable.groupby(variable["date"].dt.dayofweek)["spend_cents"].sum()

    persona, tagline = PERSONAS.get(disc_cat.index[0], ("The Balanced Spender", "No single habit dominates your spending.")) \
        if len(disc_cat) else ("The Minimalist", "Almost nothing discretionary — impressive.")
    savings_rate = pct(earned - spent, earned)
    if savings_rate is not None and savings_rate >= 20:
        tagline += f" And you saved {savings_rate}% of your income."

    # previous equivalent period for a year-over-year comparison
    prev_start, prev_end = _add_months(start, -len(months)), start - timedelta(days=1)
    prev_all = an.spending_frame(df)
    prev = int(prev_all[(prev_all["date"] >= pd.Timestamp(prev_start)) & (prev_all["date"] <= pd.Timestamp(prev_end))]["spend_cents"].sum())
    has_prev = not df.empty and df["date"].min().date() <= prev_start

    result = {
        "label": label, "start": start.isoformat(), "end": end.isoformat(), "months": len(months),
        "income": money(earned), "spending": money(spent), "saved": money(earned - spent), "savings_rate_pct": savings_rate,
        "spending_change_pct": pct(spent - prev, prev) if has_prev else None,
        "persona": persona, "persona_tagline": tagline,
        "top_category": {"name": by_cat.index[0], "amount": money(by_cat.iloc[0]), "share_pct": pct(by_cat.iloc[0], spent)} if len(by_cat) else None,
        "top_discretionary_category": {"name": disc_cat.index[0], "amount": money(disc_cat.iloc[0])} if len(disc_cat) else None,
        "top_merchant": {"name": top_m.index[0], "amount": money(top_m["sum"].iloc[0]), "visits": int(top_m["count"].iloc[0])} if len(top_m) else None,
        "most_visited": {"name": visited.index[0], "visits": int(visited["count"].iloc[0]), "amount": money(visited["sum"].iloc[0])} if len(visited) else None,
        "biggest_purchase": {"merchant": biggest["merchant"].iloc[0], "amount": money(biggest["spend_cents"].iloc[0]),
                             "date": biggest["date"].iloc[0].strftime("%Y-%m-%d")} if len(biggest) else None,
        "priciest_month": {"month": full.idxmax(), "amount": money(full.max())} if len(full) else None,
        "leanest_month": {"month": full.idxmin(), "amount": money(full.min())} if len(full) else None,
        "coffee": {"visits": len(coffee), "amount": money(coffee["spend_cents"].sum())},
        "delivery": {"orders": len(delivery), "amount": money(delivery["spend_cents"].sum())},
        "subscriptions_total": money(subs["spend_cents"].sum()),
        "no_spend_days": no_spend, "longest_no_spend_streak": streak, "days": (end - start).days + 1,
        "busiest_weekday": calendar.day_name[int(weekday.idxmax())] if len(weekday) else None,
        "unique_merchants": int(variable["merchant"].nunique()),
        "monthly": [{"month": m, "spending": money(v)} for m, v in monthly.items()],
    }
    result["share_text"] = _share_text(result)
    return result


def _share_text(r: dict) -> str:
    lines = [f"My Ledgerly Money Wrapped ({r['label']})", f"Persona: {r['persona']}"]
    if r["savings_rate_pct"] is not None:
        lines.append(f"Savings rate: {r['savings_rate_pct']}%")
    if r["top_discretionary_category"]:
        lines.append(f"Top indulgence: {r['top_discretionary_category']['name']}")
    if r["most_visited"]:
        lines.append(f"Most visited: {r['most_visited']['name']} ({r['most_visited']['visits']} visits)")
    lines.append(f"No-spend days: {r['no_spend_days']} (longest streak {r['longest_no_spend_streak']})")
    return "\n".join(lines)
