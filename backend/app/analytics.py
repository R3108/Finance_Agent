"""Deterministic financial analytics (pandas).

Every number the product shows — in the dashboard or in an agent answer — comes
from a function in this module. All sums are done on integer cents and only
converted to dollars at the output boundary via `money()`.
"""
from __future__ import annotations

import calendar
import math
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction

import numpy as np
import pandas as pd

from . import money as cur
from .categorizer import BILL_CATEGORIES, ESSENTIAL_CATEGORIES

# ----------------------------------------------------------------------------- helpers

def money(cents) -> float:
    return round(int(cents) / 100, 2)


def fmt_money(dollars: float) -> str:
    """Display text in the current user's currency (see `money.set_currency`)."""
    return cur.format_money(dollars)


def fmt_cents(cents) -> str:
    """Display text straight from integer minor units — the common case in narrative copy."""
    return cur.format_minor(int(cents))


def fmt_whole(dollars: float) -> str:
    """Display text with no decimal places, for round figures like a rounding step."""
    return cur.format_money(dollars, decimals=0)


def pct(part, whole, places: int = 1) -> float | None:
    return round(float(part) / float(whole) * 100, places) if whole else None


def resolve_as_of(df: pd.DataFrame, as_of: date | None = None) -> date:
    """Analysis date: explicit, else the latest transaction date (capped at today)."""
    if as_of:
        return as_of
    if df.empty:
        return date.today()
    return min(df["date"].max().date(), date.today())


def spending_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Rows that count toward spending: not transfers, not income. Refunds (positive) reduce spend."""
    out = df[~df["is_transfer"] & (df["category"] != "Income")].copy()
    out["spend_cents"] = -out["amount_cents"]
    return out


def income_frame(df: pd.DataFrame) -> pd.DataFrame:
    return df[~df["is_transfer"] & (df["category"] == "Income")]


def month_str(d: date) -> str:
    return d.strftime("%Y-%m")


def shift_month(month: str, delta: int) -> str:
    y, m = map(int, month.split("-"))
    idx = y * 12 + (m - 1) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def complete_months(as_of: date, n: int) -> list[str]:
    """The n full calendar months before as_of's month, oldest first."""
    cur = month_str(as_of)
    return [shift_month(cur, -i) for i in range(n, 0, -1)]


def _ceil_to(cents: float, step_major: int) -> int:
    """Round minor units up to a tidy multiple of `step_major` major units (see `money.rounding_step`)."""
    step = step_major * 100
    return int(math.ceil(cents / step) * step) if cents > 0 else 0


# ----------------------------------------------------------------------------- accounts

def account_balances(df: pd.DataFrame, accounts: pd.DataFrame) -> dict:
    sums = df.groupby("account_id")["amount_cents"].sum() if not df.empty else pd.Series(dtype="int64")
    items, total, liquid = [], 0, 0
    for _, a in accounts.iterrows():
        bal = int(a["opening_balance_cents"]) + int(sums.get(a["id"], 0))
        total += bal
        if a["type"] in ("checking", "savings"):
            liquid += bal
        items.append({"id": int(a["id"]), "name": a["name"], "type": a["type"],
                      "institution": a["institution"], "balance": money(bal)})
    return {"accounts": items, "net_cash": money(total), "liquid_cash": money(liquid)}


# ----------------------------------------------------------------------------- spending

def monthly_summary(df: pd.DataFrame, months: int = 12, as_of: date | None = None) -> list[dict]:
    as_of = resolve_as_of(df, as_of)
    window = complete_months(as_of, months - 1) + [month_str(as_of)]
    spend = spending_frame(df).groupby("month")["spend_cents"].sum()
    inc = income_frame(df).groupby("month")["amount_cents"].sum()
    rows = []
    for m in window:
        s, i = int(spend.get(m, 0)), int(inc.get(m, 0))
        rows.append({
            "month": m, "income": money(i), "spending": money(s), "net": money(i - s),
            "savings_rate_pct": pct(i - s, i), "partial": m == month_str(as_of),
        })
    return rows


def category_breakdown(df: pd.DataFrame, month: str | None = None, as_of: date | None = None) -> dict:
    as_of = resolve_as_of(df, as_of)
    month = month or month_str(as_of)
    sp = spending_frame(df)
    cur = sp[sp["month"] == month].groupby("category")["spend_cents"].agg(["sum", "count"])
    prev = sp[sp["month"] == shift_month(month, -1)].groupby("category")["spend_cents"].sum()
    trailing_months = [shift_month(month, -i) for i in range(1, 7)]
    trailing = (sp[sp["month"].isin(trailing_months)].groupby(["category", "month"])["spend_cents"].sum()
                .unstack(fill_value=0).reindex(columns=trailing_months, fill_value=0))
    total = int(cur["sum"].sum()) if not cur.empty else 0
    partial = month == month_str(as_of)
    # compare a partial month against the same fraction of a typical month
    y, m = map(int, month.split("-"))
    progress = Fraction(as_of.day, calendar.monthrange(y, m)[1]) if partial else Fraction(1)
    items = []
    for cat, row in cur.sort_values("sum", ascending=False).iterrows():
        avg6 = int(round(trailing.loc[cat].mean())) if cat in trailing.index else 0
        # fixed bills land in one lump (rent on the 1st), so don't prorate them
        expected = avg6 if cat in FIXED_CATEGORIES else int(round(avg6 * progress))
        items.append({
            "category": cat, "amount": money(row["sum"]), "transactions": int(row["count"]),
            "share_pct": pct(row["sum"], total), "previous_month": money(prev.get(cat, 0)),
            "six_month_avg": money(avg6), "expected_to_date": money(expected),
            "vs_six_month_avg_pct": pct(row["sum"] - expected, expected),
        })
    return {"month": month, "total": money(total), "partial": partial, "categories": items}


def top_merchants(df: pd.DataFrame, start: str | None = None, end: str | None = None, limit: int = 10) -> list[dict]:
    sp = spending_frame(df)
    if start:
        sp = sp[sp["date"] >= pd.Timestamp(start)]
    if end:
        sp = sp[sp["date"] <= pd.Timestamp(end)]
    g = sp.groupby(["merchant", "category"])["spend_cents"].agg(["sum", "count"]).sort_values("sum", ascending=False)
    return [{"merchant": m, "category": c, "total": money(r["sum"]), "transactions": int(r["count"]),
             "average": money(round(r["sum"] / r["count"]))} for (m, c), r in g.head(limit).iterrows()]


def weekday_pattern(df: pd.DataFrame, months: int = 6, as_of: date | None = None) -> list[dict]:
    as_of = resolve_as_of(df, as_of)
    sp = spending_frame(df)
    sp = sp[(sp["date"] > pd.Timestamp(as_of) - pd.DateOffset(months=months))
            & ~sp["category"].isin(BILL_CATEGORIES | {"Subscriptions"})]
    by = sp.groupby(sp["date"].dt.dayofweek)["spend_cents"].sum()
    weeks = max(1, months * 52 // 12)
    return [{"weekday": calendar.day_name[d], "avg_spend": money(round(by.get(d, 0) / weeks))} for d in range(7)]


def search_transactions(df: pd.DataFrame, query: str | None = None, category: str | None = None,
                        start: str | None = None, end: str | None = None,
                        min_amount: float | None = None, max_amount: float | None = None,
                        limit: int = 50, offset: int = 0) -> dict:
    out = df
    if query:
        q = query.lower()
        out = out[out["merchant"].str.lower().str.contains(q, regex=False)
                  | out["description"].str.lower().str.contains(q, regex=False)]
    if category:
        out = out[out["category"].str.lower() == category.lower()]
    if start:
        out = out[out["date"] >= pd.Timestamp(start)]
    if end:
        out = out[out["date"] <= pd.Timestamp(end)]
    if min_amount is not None:
        out = out[out["amount_cents"].abs() >= int(round(min_amount * 100))]
    if max_amount is not None:
        out = out[out["amount_cents"].abs() <= int(round(max_amount * 100))]
    out = out.sort_values(["date", "id"], ascending=False)
    spend_total = int(-out.loc[~out["is_transfer"] & (out["category"] != "Income"), "amount_cents"].sum())
    page = out.iloc[offset: offset + limit]
    return {
        "total_count": int(len(out)),
        "net_spend_of_matches": money(spend_total),
        "transactions": [
            {"id": int(r.id), "date": r.date.strftime("%Y-%m-%d"), "merchant": r.merchant,
             "description": r.description, "account": r.account, "amount": money(r.amount_cents),
             "category": r.category, "category_source": r.category_source, "is_transfer": bool(r.is_transfer)}
            for r in page.itertuples()
        ],
    }


# ----------------------------------------------------------------------------- recurring / subscriptions

CADENCES = {  # name: (min_days, max_days, nominal_days, monthly factor)
    "weekly": (6, 8, 7, Fraction(52, 12)),
    "biweekly": (13, 16, 14, Fraction(26, 12)),
    "four_weekly": (27, 29, 28, Fraction(13, 12)),
    "monthly": (26, 35, 30, Fraction(1)),
    "quarterly": (85, 97, 91, Fraction(1, 3)),
    "semiannual": (175, 190, 182, Fraction(1, 6)),
    "annual": (355, 375, 365, Fraction(1, 12)),
}
NOT_SUBSCRIPTION_CATEGORIES = {"Groceries", "Dining", "Travel", "Fees", "Gifts & Donations", "Uncategorized"}

#: Services that substitute for each other, and how many a user must stack before it's worth flagging.
#: Membership is by normalised merchant name, so regional services belong in the same group as their
#: global equivalents — someone paying for Netflix, Hotstar and SonyLIV is stacking video the same way.
SERVICE_GROUPS = {
    "Video streaming": ({"Netflix", "Hulu", "Disney+", "Max", "Peacock", "Paramount+",
                         "Disney+ Hotstar", "SonyLIV", "ZEE5", "JioCinema"}, 3),
    "Music streaming": ({"Spotify", "Apple Music", "YouTube Music", "Tidal", "Music Streaming"}, 2),
    "Cloud storage": ({"Apple iCloud", "Dropbox", "Google One", "OneDrive"}, 2),
}


def _classify_cadence(median_days: float, calendar_monthly: bool) -> str | None:
    if calendar_monthly and 26 <= median_days <= 35:
        return "monthly"
    for name, (lo, hi, _, _) in CADENCES.items():
        if lo <= median_days <= hi:
            return name
    return None


def _recurring_kind(direction: str, category: str, cadence: str) -> str:
    if direction == "income":
        return "income"
    if category in BILL_CATEGORIES:
        return "bill"
    # e.g. a dentist every 6 months is recurring, but not something you "subscribe" to
    if cadence in ("quarterly", "semiannual") and category != "Subscriptions":
        return "recurring"
    return "subscription"


@dataclass
class Recurring:
    key: str
    merchant: str
    account_id: int | None
    account: str | None
    category: str
    kind: str
    cadence: str
    occurrences: int
    first_date: date
    last_date: date
    next_expected: date
    last_cents: int
    previous_cents: int
    avg_cents: int
    monthly_cents: int
    annual_cents: int
    active: bool
    price_changed_on: date | None = None

    def to_dict(self, status: str | None = None) -> dict:
        return {
            "key": self.key, "merchant": self.merchant, "account": self.account, "category": self.category,
            "kind": self.kind, "cadence": self.cadence, "occurrences": self.occurrences,
            "first_charge": self.first_date.isoformat(), "last_charge": self.last_date.isoformat(),
            "next_expected": self.next_expected.isoformat() if self.active else None,
            "last_amount": money(self.last_cents), "previous_price": money(self.previous_cents),
            "price_changed_on": self.price_changed_on.isoformat() if self.price_changed_on else None,
            "average_amount": money(self.avg_cents), "monthly_cost": money(self.monthly_cents),
            "annual_cost": money(self.annual_cents), "active": self.active, "status": status or "keep",
        }


def detect_recurring(df: pd.DataFrame, as_of: date | None = None, direction: str = "expense") -> list[Recurring]:
    """Find series of charges from one merchant on one account at a regular cadence with stable amounts."""
    as_of = resolve_as_of(df, as_of)
    if direction == "expense":
        base = df[(df["amount_cents"] < 0) & ~df["is_transfer"] & (df["category"] != "Income")]
    else:
        base = df[(df["amount_cents"] > 0) & (df["category"] == "Income")]
    results: list[Recurring] = []
    for (merchant, account_id), g in base.groupby(["merchant", "account_id"], dropna=False):
        if len(g) < 2:
            continue
        g = g.sort_values("date")
        amounts = g["amount_cents"].abs().astype("int64").to_numpy()
        dates = g["date"]
        intervals = dates.diff().dt.days.dropna().to_numpy()
        median_iv = float(np.median(intervals))
        dom = dates.dt.day.to_numpy()
        calendar_monthly = bool(np.std(dom) <= 1.5)
        cadence = _classify_cadence(median_iv, calendar_monthly)
        if cadence is None:
            continue
        lo, hi, nominal, factor = CADENCES[cadence]
        if len(g) < (2 if cadence in ("annual", "semiannual") else 3):
            continue
        tol = max(3.0, 0.15 * nominal)
        if np.mean(np.abs(intervals - median_iv) <= tol) < 0.75:
            continue
        # amounts must be stable, allowing step changes (price increases)
        steps = np.abs(np.diff(amounts)) / np.maximum(amounts[:-1], 1)
        category = g["category"].iloc[-1]
        stable = np.mean(steps <= 0.02) >= 0.7
        variable_bill = category in BILL_CATEGORIES and np.std(amounts) / np.mean(amounts) <= 0.35
        if not (stable or variable_bill):
            continue
        if direction == "expense" and category in NOT_SUBSCRIPTION_CATEGORIES:
            continue

        last_date = dates.iloc[-1].date()
        if cadence == "monthly" and calendar_monthly:
            next_expected = (pd.Timestamp(last_date) + pd.DateOffset(months=1)).date()
        elif cadence == "annual":
            next_expected = (pd.Timestamp(last_date) + pd.DateOffset(years=1)).date()
        else:
            next_expected = last_date + timedelta(days=int(round(median_iv)))
        grace = max(5, int(0.25 * nominal))
        active = as_of <= next_expected + timedelta(days=grace)
        last_cents = int(amounts[-1]) if not variable_bill or stable else int(round(np.median(amounts[-6:])))
        monthly = int(round(last_cents * factor))
        # most recent price change: walk back to the last charge with a different amount
        prev_cents, change_date = int(amounts[-1]), None
        if stable:
            for i in range(len(amounts) - 2, -1, -1):
                if abs(int(amounts[i]) - int(amounts[-1])) > max(1, 0.02 * amounts[-1]):
                    prev_cents, change_date = int(amounts[i]), dates.iloc[i + 1].date()
                    break
        results.append(Recurring(
            key=f"{merchant}|{'' if pd.isna(account_id) else int(account_id)}",
            merchant=merchant,
            account_id=None if pd.isna(account_id) else int(account_id),
            account=g["account"].iloc[-1] if "account" in g else None,
            category=category,
            kind=_recurring_kind(direction, category, cadence),
            cadence=cadence, occurrences=len(g), first_date=dates.iloc[0].date(), last_date=last_date,
            next_expected=next_expected, last_cents=last_cents, previous_cents=prev_cents,
            price_changed_on=change_date,
            avg_cents=int(round(amounts.mean())), monthly_cents=monthly, annual_cents=monthly * 12,
            active=active,
        ))
    results.sort(key=lambda r: (-r.active, -r.monthly_cents))
    return results


def subscriptions_summary(df: pd.DataFrame, statuses: dict[str, str] | None = None,
                          as_of: date | None = None, include_inactive: bool = True) -> dict:
    statuses = statuses or {}
    rec = detect_recurring(df, as_of)
    items = [r.to_dict(statuses.get(r.key)) for r in rec if include_inactive or r.active]
    active = [r for r in rec if r.active]
    subs = [r for r in active if r.kind == "subscription"]
    bills = [r for r in active if r.kind == "bill"]
    return {
        "items": items,
        "active_subscriptions": len(subs),
        "subscriptions_monthly_total": money(sum(r.monthly_cents for r in subs)),
        "subscriptions_annual_total": money(sum(r.annual_cents for r in subs)),
        "bills_monthly_total": money(sum(r.monthly_cents for r in bills)),
    }


def unusual_subscriptions(df: pd.DataFrame, as_of: date | None = None) -> list[dict]:
    """Flag subscriptions needing attention: price hikes, duplicates, overlaps, new, costly, renewals, stopped."""
    as_of = resolve_as_of(df, as_of)
    rec = [r for r in detect_recurring(df, as_of) if r.kind == "subscription"]
    active = [r for r in rec if r.active]
    flags: list[dict] = []

    def flag(r: Recurring | None, type_: str, severity: str, title: str, detail: str, savings_cents: int = 0,
             merchants=None, event_date: date | None = None):
        # `event_date` is the date the flagged thing happened (price changed, first charged, renews).
        # Alerts key off it so the same event is reported once, while a genuinely new one — next
        # year's price rise — is a different event and is reported again.
        flags.append({"type": type_, "severity": severity, "title": title, "detail": detail,
                      "merchant": r.merchant if r else None, "key": r.key if r else None,
                      "merchants": merchants or ([r.merchant] if r else []),
                      "event_date": event_date.isoformat() if event_date else None,
                      "potential_monthly_savings": money(savings_cents),
                      "potential_annual_savings": money(savings_cents * 12)})

    for r in active:
        if r.price_changed_on and (as_of - r.price_changed_on).days <= 180 and r.last_cents > r.previous_cents:
            inc = r.last_cents - r.previous_cents
            inc_pct = pct(inc, r.previous_cents)
            flag(r, "price_increase", "high" if inc_pct >= 25 else "medium",
                 f"{r.merchant} raised its price by {inc_pct}%",
                 f"Charge went from {fmt_cents(r.previous_cents)} to {fmt_cents(r.last_cents)} "
                 f"on {r.price_changed_on.isoformat()} "
                 f"(+{fmt_cents(int(round(inc * CADENCES[r.cadence][3])))}/month).",
                 int(round(inc * CADENCES[r.cadence][3])), event_date=r.price_changed_on)
        if (as_of - r.first_date).days <= 90:
            flag(r, "new", "low", f"New subscription: {r.merchant}",
                 f"First charged on {r.first_date.isoformat()}; costs {fmt_cents(r.monthly_cents)}/month. "
                 "Confirm you meant to sign up (free trials often convert silently).", event_date=r.first_date)
        if r.monthly_cents >= cur.scaled_minor(50):          # a costly subscription
            flag(r, "high_cost", "medium", f"{r.merchant} is a high-cost subscription",
                 f"{fmt_cents(r.monthly_cents)}/month ({fmt_cents(r.annual_cents)}/year). "
                 "Check for a cheaper tier or annual billing discount.", event_date=r.first_date)
        if r.cadence in ("annual", "semiannual") and 0 <= (r.next_expected - as_of).days <= 30:
            flag(r, "upcoming_renewal", "medium", f"{r.merchant} renews soon",
                 f"Expected renewal on {r.next_expected.isoformat()} for {fmt_cents(r.last_cents)}. "
                 "Cancel before that date if you no longer use it.", event_date=r.next_expected)

    by_merchant: dict[str, list[Recurring]] = {}
    for r in active:
        by_merchant.setdefault(r.merchant, []).append(r)
    for merchant, series in by_merchant.items():
        if len(series) > 1:
            dup_cost = sum(s.monthly_cents for s in series) - max(s.monthly_cents for s in series)
            accounts = ", ".join(sorted({s.account or "unknown" for s in series}))
            flag(series[-1], "duplicate", "high", f"{merchant} is billed on {len(series)} accounts",
                 f"Charges appear on: {accounts}. You are likely paying twice.", dup_cost,
                 event_date=series[-1].last_date)

    for group, (members, threshold) in SERVICE_GROUPS.items():
        in_group = [r for r in active if r.merchant in members]
        names = sorted({r.merchant for r in in_group})
        if len(names) >= threshold:
            cheapest = min(in_group, key=lambda r: r.monthly_cents)
            total = sum(r.monthly_cents for r in in_group)
            flag(None, "overlap", "medium", f"{len(names)} overlapping {group.lower()} services",
                 f"{', '.join(names)} cost {fmt_cents(total)}/month combined. "
                 "Rotating services month-to-month instead of stacking them saves money.",
                 total - cheapest.monthly_cents if group == "Video streaming" else cheapest.monthly_cents,
                 merchants=names)

    for r in rec:
        if not r.active and (as_of - r.last_date).days <= 180:
            flag(r, "stopped", "info", f"{r.merchant} appears cancelled",
                 f"No charge since {r.last_date.isoformat()} (expected around {r.next_expected.isoformat()}).",
                 event_date=r.last_date)

    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    flags.sort(key=lambda f: (order[f["severity"]], -f["potential_monthly_savings"]))
    return flags


# ----------------------------------------------------------------------------- anomalies

def detect_anomalies(df: pd.DataFrame, as_of: date | None = None, lookback_days: int = 180) -> list[dict]:
    as_of = resolve_as_of(df, as_of)
    exp = df[(df["amount_cents"] < 0) & ~df["is_transfer"]].copy()
    exp["abs_cents"] = -exp["amount_cents"]
    cutoff = pd.Timestamp(as_of - timedelta(days=lookback_days))
    found: dict[int, dict] = {}
    recurring_merchants = {r.merchant for r in detect_recurring(df, as_of)}

    def add(row, reason: str, severity: str):
        entry = found.setdefault(int(row.id), {
            "id": int(row.id), "date": row.date.strftime("%Y-%m-%d"), "merchant": row.merchant,
            "category": row.category, "amount": money(row.amount_cents), "reasons": [], "severity": "low"})
        entry["reasons"].append(reason)
        if ["low", "medium", "high"].index(severity) > ["low", "medium", "high"].index(entry["severity"]):
            entry["severity"] = severity

    # 1) robust z-score (median / MAD) within each category's full history
    for cat, g in exp.groupby("category"):
        if len(g) < 8 or cat in BILL_CATEGORIES:
            continue
        med = g["abs_cents"].median()
        mad = (g["abs_cents"] - med).abs().median() or 1
        recent = g[(g["date"] >= cutoff) & ~g["merchant"].isin(recurring_merchants)]
        for row in recent.itertuples():
            z = 0.6745 * (row.abs_cents - med) / mad
            if z > 3.5 and row.abs_cents >= cur.scaled_minor(75):   # ignore small outliers
                multiple = round(row.abs_cents / med, 1)
                add(row, f"{multiple}x your typical {cat} transaction ({fmt_cents(med)})",
                    "high" if multiple >= 5 else "medium")

    recent = exp[exp["date"] >= cutoff]
    # 2) duplicate charges: same merchant & amount within 1 day (excluding tiny amounts)
    rs = recent.sort_values("date")
    for (_, _), g in rs.groupby(["merchant", "abs_cents"]):
        if len(g) < 2 or g["abs_cents"].iloc[0] < 1000:
            continue
        gaps = g["date"].diff().dt.days
        for row, gap in zip(g.itertuples(), gaps):
            if pd.notna(gap) and gap <= 1:
                add(row, "Possible duplicate charge (same merchant and amount within 1 day)", "high")

    # 3) unrecognised merchants and 4) first-time merchants with a large charge
    first_seen = exp.groupby("merchant")["date"].min()
    for row in recent.itertuples():
        if row.category == "Uncategorized" and row.abs_cents >= cur.scaled_minor(20):
            add(row, "Unrecognised merchant — verify this charge", "medium")
        if (row.abs_cents >= cur.scaled_minor(250) and first_seen[row.merchant] == row.date
                and row.category not in ("Travel",)):
            add(row, "First purchase ever at this merchant", "low")
        if row.category == "Fees":
            add(row, "Bank fee — often avoidable or refundable on request", "low")

    return sorted(found.values(), key=lambda a: (a["date"]), reverse=True)


# ----------------------------------------------------------------------------- budgets

SINKING_FUND_CATEGORIES = {"Travel", "Gifts & Donations", "Education"}
FIXED_CATEGORIES = BILL_CATEGORIES | {"Subscriptions"}


def suggest_budgets(df: pd.DataFrame, current: dict[str, int] | None = None, as_of: date | None = None,
                    lookback_months: int = 6) -> dict:
    as_of = resolve_as_of(df, as_of)
    current = current or {}
    months = complete_months(as_of, lookback_months)
    sp = spending_frame(df)
    pivot = (sp[sp["month"].isin(months)].groupby(["category", "month"])["spend_cents"].sum()
             .unstack(fill_value=0).reindex(columns=months, fill_value=0))
    year_months = complete_months(as_of, 12)
    yearly = sp[sp["month"].isin(year_months)].groupby("category")["spend_cents"].sum()
    inc = income_frame(df)
    avg_income = int(round(inc[inc["month"].isin(months)]["amount_cents"].sum() / len(months)))

    items = []
    for cat, series in pivot.iterrows():
        if cat in ("Transfers", "Income", "Uncategorized"):
            continue
        vals = series.to_numpy(dtype=float)
        avg, med, p75 = vals.mean(), float(np.median(vals)), float(np.percentile(vals, 75))
        coarse, fine = cur.rounding_step(10), cur.rounding_step(5)
        if cat in SINKING_FUND_CATEGORIES:
            suggested = _ceil_to(yearly.get(cat, 0) / 12, coarse)
            method = "sinking fund"
            why = f"Irregular spending: 1/12 of the last 12 months' total ({fmt_cents(yearly.get(cat, 0))}), set aside monthly."
        elif cat in FIXED_CATEGORIES:
            suggested = _ceil_to(vals[-3:].max(), coarse)
            method = "fixed"
            why = f"Fixed obligation: highest of the last 3 months, rounded up to {fmt_whole(coarse)}."
        elif cat in ESSENTIAL_CATEGORIES:
            suggested = _ceil_to(p75, coarse)
            method = "essential"
            why = (f"Essential variable cost: 75th percentile of the last {lookback_months} months, "
                   f"rounded up to {fmt_whole(coarse)}.")
        else:
            suggested = _ceil_to(med * 0.9, fine)
            method = "discretionary (-10%)"
            why = f"Discretionary: your median month ({fmt_cents(med)}) trimmed 10%, rounded up to {fmt_whole(fine)}."
        if suggested <= 0:
            continue
        items.append({
            "category": cat, "avg_monthly": money(round(avg)), "median_monthly": money(round(med)),
            "p75_monthly": money(round(p75)), "suggested": money(suggested), "method": method, "rationale": why,
            "current_budget": money(current[cat]) if cat in current else None,
            "change_vs_avg": money(suggested - round(avg)),
        })
    items.sort(key=lambda x: -x["suggested"])
    total_suggested = sum(int(round(i["suggested"] * 100)) for i in items)
    needs = sum(int(round(i["suggested"] * 100)) for i in items if i["category"] in ESSENTIAL_CATEGORIES)
    wants = total_suggested - needs
    return {
        "based_on_months": months,
        "avg_monthly_income": money(avg_income),
        "suggestions": items,
        "total_suggested": money(total_suggested),
        "projected_monthly_savings": money(avg_income - total_suggested),
        "rule_50_30_20": {
            "needs": money(needs), "needs_pct": pct(needs, avg_income),
            "wants": money(wants), "wants_pct": pct(wants, avg_income),
            "savings": money(avg_income - total_suggested), "savings_pct": pct(avg_income - total_suggested, avg_income),
            "target": {"needs_pct": 50, "wants_pct": 30, "savings_pct": 20},
        },
    }


def budget_status(df: pd.DataFrame, budgets: dict[str, int], month: str | None = None,
                  as_of: date | None = None) -> dict:
    as_of = resolve_as_of(df, as_of)
    month = month or month_str(as_of)
    y, m = map(int, month.split("-"))
    days_in_month = calendar.monthrange(y, m)[1]
    is_current = month == month_str(as_of)
    elapsed = as_of.day if is_current else days_in_month
    sp = spending_frame(df)
    spent = sp[sp["month"] == month].groupby("category")["spend_cents"].sum()
    items = []
    for cat, limit in sorted(budgets.items()):
        s = int(spent.get(cat, 0))
        projected = int(round(s * days_in_month / elapsed)) if is_current and cat not in FIXED_CATEGORIES else s
        remaining = limit - s
        days_left = days_in_month - elapsed
        status = "over" if s > limit else ("at_risk" if projected > limit else "on_track")
        items.append({
            "category": cat, "limit": money(limit), "spent": money(s), "remaining": money(remaining),
            "used_pct": pct(s, limit), "projected_month_end": money(projected), "status": status,
            "daily_allowance": money(remaining // days_left) if is_current and days_left > 0 and remaining > 0 else 0.0,
        })
    return {"month": month, "days_elapsed": elapsed, "days_in_month": days_in_month, "items": items,
            "total_limit": money(sum(budgets.values())), "total_spent": money(sum(int(spent.get(c, 0)) for c in budgets))}


# ----------------------------------------------------------------------------- forecast, safe-to-spend, health

def _occurrences(r: Recurring, start: date, end: date) -> list[date]:
    out, d = [], r.next_expected
    nominal = CADENCES[r.cadence][2]
    while d <= end:
        if d > start:
            out.append(d)
        if r.cadence == "monthly":
            d = (pd.Timestamp(d) + pd.DateOffset(months=1)).date()
        elif r.cadence == "annual":
            d = (pd.Timestamp(d) + pd.DateOffset(years=1)).date()
        else:
            d = d + timedelta(days=nominal)
    return out


def _variable_daily_cents(df: pd.DataFrame, as_of: date, recurring_keys: set[str], days: int = 90) -> int:
    sp = spending_frame(df)
    sp = sp[(sp["date"] > pd.Timestamp(as_of - timedelta(days=days))) & (sp["date"] <= pd.Timestamp(as_of))]
    keys = sp["merchant"] + "|" + sp["account_id"].fillna(-1).astype("int64").astype(str).replace("-1", "")
    return int(round(sp.loc[~keys.isin(recurring_keys), "spend_cents"].sum() / days))


def cashflow_forecast(df: pd.DataFrame, accounts: pd.DataFrame, days: int = 60, as_of: date | None = None) -> dict:
    """Project cash across all accounts: scheduled income + recurring charges + average variable spend."""
    as_of = resolve_as_of(df, as_of)
    end = as_of + timedelta(days=days)
    expenses = [r for r in detect_recurring(df, as_of) if r.active]
    incomes = [r for r in detect_recurring(df, as_of, "income") if r.active]
    daily_var = _variable_daily_cents(df, as_of, {r.key for r in expenses})
    events: dict[date, list[tuple[str, int]]] = {}
    for r in expenses:
        for d in _occurrences(r, as_of, end):
            events.setdefault(d, []).append((r.merchant, -r.last_cents))
    for r in incomes:
        for d in _occurrences(r, as_of, end):
            events.setdefault(d, []).append((r.merchant, r.last_cents))
    bal = int(round(account_balances(df, accounts)["net_cash"] * 100))
    start_bal, lowest = bal, (bal, as_of)
    series = []
    for i in range(1, days + 1):
        d = as_of + timedelta(days=i)
        day_events = events.get(d, [])
        bal += sum(a for _, a in day_events) - daily_var
        if bal < lowest[0]:
            lowest = (bal, d)
        series.append({"date": d.isoformat(), "balance": money(bal),
                       "events": [{"name": n, "amount": money(a)} for n, a in day_events]})
    scheduled_in = sum(a for evs in events.values() for _, a in evs if a > 0)
    scheduled_out = -sum(a for evs in events.values() for _, a in evs if a < 0)
    return {
        "as_of": as_of.isoformat(), "days": days, "starting_balance": money(start_bal),
        "ending_balance": money(bal), "lowest_balance": money(lowest[0]), "lowest_balance_date": lowest[1].isoformat(),
        "scheduled_income": money(scheduled_in), "scheduled_recurring_charges": money(scheduled_out),
        "estimated_variable_spending": money(daily_var * days), "avg_daily_variable_spend": money(daily_var),
        "series": series,
    }


def safe_to_spend(df: pd.DataFrame, savings_target_pct: float = 20.0, as_of: date | None = None) -> dict:
    """Discretionary money left this month after bills still due and your savings target."""
    as_of = resolve_as_of(df, as_of)
    month = month_str(as_of)
    y, m = as_of.year, as_of.month
    month_end = date(y, m, calendar.monthrange(y, m)[1])
    inc_so_far = int(income_frame(df).query("month == @month")["amount_cents"].sum())
    spent_so_far = int(spending_frame(df).query("month == @month")["spend_cents"].sum())
    expected_income = sum(r.last_cents * len(_occurrences(r, as_of, month_end))
                          for r in detect_recurring(df, as_of, "income") if r.active)
    due = [(r.merchant, r.last_cents, d) for r in detect_recurring(df, as_of) if r.active
           for d in _occurrences(r, as_of, month_end)]
    bills_due = sum(c for _, c, _ in due)
    total_income = inc_so_far + expected_income
    savings_target = int(round(total_income * savings_target_pct / 100))
    available = total_income - spent_so_far - bills_due - savings_target
    days_left = (month_end - as_of).days + 1
    return {
        "month": month, "income_received": money(inc_so_far), "income_expected": money(expected_income),
        "spent_so_far": money(spent_so_far), "recurring_still_due": money(bills_due),
        "upcoming_charges": [{"merchant": n, "amount": money(c), "date": d.isoformat()} for n, c, d in sorted(due, key=lambda x: x[2])],
        "savings_target_pct": savings_target_pct, "savings_target": money(savings_target),
        "safe_to_spend": money(available), "days_left": days_left,
        "per_day": money(available // days_left) if available > 0 else 0.0,
    }


def _scale(value: float, zero_at: float, full_at: float) -> float:
    if full_at > zero_at:
        return float(np.clip((value - zero_at) / (full_at - zero_at), 0, 1) * 100)
    return float(np.clip((zero_at - value) / (zero_at - full_at), 0, 1) * 100)


def health_score(df: pd.DataFrame, accounts: pd.DataFrame, budgets: dict[str, int], as_of: date | None = None) -> dict:
    as_of = resolve_as_of(df, as_of)
    months6 = complete_months(as_of, 6)
    months3 = months6[-3:]
    sp = spending_frame(df)
    inc = income_frame(df)
    spend_m = sp.groupby("month")["spend_cents"].sum().reindex(months6, fill_value=0)
    inc_m = inc.groupby("month")["amount_cents"].sum().reindex(months6, fill_value=0)
    inc3, spend3 = int(inc_m[months3].sum()), int(spend_m[months3].sum())
    savings_rate = (inc3 - spend3) / inc3 * 100 if inc3 else 0.0
    avg_spend = spend_m.mean() or 1
    liquid = account_balances(df, accounts)["liquid_cash"] * 100
    emergency_months = liquid / avg_spend
    subs = subscriptions_summary(df, as_of=as_of)
    sub_load = subs["subscriptions_monthly_total"] * 100 / (inc_m.mean() or 1) * 100
    volatility = float(spend_m.std(ddof=0) / avg_spend)
    if budgets:
        last = budget_status(df, budgets, months6[-1], as_of)
        adherence = sum(i["status"] != "over" for i in last["items"]) / len(last["items"]) * 100
    else:
        adherence = 50.0

    components = [
        ("Savings rate", 30, _scale(savings_rate, 0, 20), f"{savings_rate:.1f}% of income saved over the last 3 months (target 20%+)."),
        ("Emergency fund", 25, _scale(emergency_months, 0, 6), f"{emergency_months:.1f} months of expenses in checking + savings (target 6)."),
        ("Budget adherence", 15, adherence, f"{adherence:.0f}% of budgeted categories stayed within limit last month." if budgets else "No budgets set yet."),
        ("Subscription load", 15, _scale(sub_load, 15, 5), f"Subscriptions take {sub_load:.1f}% of income (healthy: under 5%)."),
        ("Spending stability", 15, _scale(volatility, 0.5, 0.1), f"Monthly spending varies by {volatility * 100:.0f}% (lower is steadier)."),
    ]
    score = round(sum(w * s for _, w, s, _ in components) / 100)
    grade = "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D" if score >= 40 else "E"
    return {
        "score": score, "grade": grade,
        "components": [{"name": n, "weight": w, "score": round(s), "detail": d} for n, w, s, d in components],
        "metrics": {"savings_rate_pct": round(savings_rate, 1), "emergency_fund_months": round(float(emergency_months), 1),
                    "subscription_load_pct": round(float(sub_load), 1), "spending_volatility_pct": round(volatility * 100, 1)},
    }


# ----------------------------------------------------------------------------- goals & what-if

def avg_monthly_surplus(df: pd.DataFrame, as_of: date | None = None, months: int = 6) -> int:
    rows = monthly_summary(df, months + 1, as_of)[:-1]
    return int(round(sum(r["net"] * 100 for r in rows) / len(rows))) if rows else 0


def goals_progress(df: pd.DataFrame, goals: pd.DataFrame, as_of: date | None = None, extra_monthly_cents: int = 0) -> list[dict]:
    as_of = resolve_as_of(df, as_of)
    surplus = avg_monthly_surplus(df, as_of) + extra_monthly_cents
    out = []
    for g in goals.itertuples():
        remaining = max(0, int(g.target_cents) - int(g.saved_cents))
        target_date = date.fromisoformat(g.target_date)
        months_left = max(1, (target_date.year - as_of.year) * 12 + target_date.month - as_of.month)
        required = math.ceil(remaining / months_left)
        months_at_surplus = math.ceil(remaining / surplus) if surplus > 0 else None
        out.append({
            "id": int(g.id), "name": g.name, "target": money(g.target_cents), "saved": money(g.saved_cents),
            "remaining": money(remaining), "progress_pct": pct(g.saved_cents, g.target_cents),
            "target_date": g.target_date, "months_left": months_left, "required_monthly": money(required),
            "available_monthly_surplus": money(surplus), "on_track": surplus >= required,
            "months_to_goal_at_full_surplus": months_at_surplus,
        })
    return out


def simulate_savings(df: pd.DataFrame, goals: pd.DataFrame, cancel: list[str] | None = None,
                     category_cuts: dict[str, float] | None = None, as_of: date | None = None) -> dict:
    """What-if: cancel subscriptions (by merchant name or key) and/or cut category spending by a percentage."""
    as_of = resolve_as_of(df, as_of)
    cancel = [c.lower() for c in (cancel or [])]
    rec = [r for r in detect_recurring(df, as_of) if r.active]
    cancelled = [r for r in rec if r.key.lower() in cancel or r.merchant.lower() in cancel]
    sub_savings = sum(r.monthly_cents for r in cancelled)
    months = complete_months(as_of, 6)
    sp = spending_frame(df)
    cat_avg = sp[sp["month"].isin(months)].groupby("category")["spend_cents"].sum() / len(months)
    cuts = []
    for cat, p in (category_cuts or {}).items():
        match = next((c for c in cat_avg.index if c.lower() == cat.lower()), None)
        if match is None:
            continue
        cut = int(round(cat_avg[match] * float(p) / 100))
        cuts.append({"category": match, "cut_pct": float(p), "avg_monthly": money(round(cat_avg[match])),
                     "monthly_savings": money(cut)})
    total = sub_savings + sum(int(round(c["monthly_savings"] * 100)) for c in cuts)
    base_goals = goals_progress(df, goals, as_of)
    new_goals = goals_progress(df, goals, as_of, total)
    return {
        "cancelled_subscriptions": [{"merchant": r.merchant, "key": r.key, "monthly_cost": money(r.monthly_cents)} for r in cancelled],
        "category_cuts": cuts,
        "monthly_savings": money(total), "annual_savings": money(total * 12),
        "five_year_savings_at_4pct": money(round(_fv_monthly(total, 0.04, 60))),
        "monthly_surplus_before": money(avg_monthly_surplus(df, as_of)),
        "monthly_surplus_after": money(avg_monthly_surplus(df, as_of) + total),
        "goal_impact": [{"goal": b["name"], "months_before": b["months_to_goal_at_full_surplus"],
                         "months_after": a["months_to_goal_at_full_surplus"]} for b, a in zip(base_goals, new_goals)],
    }


def _fv_monthly(payment_cents: int, annual_rate: float, n_months: int) -> float:
    r = annual_rate / 12
    return payment_cents * (((1 + r) ** n_months - 1) / r) if r else payment_cents * n_months


# ----------------------------------------------------------------------------- insights feed

def insights(df: pd.DataFrame, accounts: pd.DataFrame, budgets: dict[str, int], as_of: date | None = None) -> list[dict]:
    """Deterministic, ranked list of things worth the user's attention right now."""
    as_of = resolve_as_of(df, as_of)
    out: list[dict] = []
    for f in unusual_subscriptions(df, as_of):
        if f["severity"] in ("high", "medium"):
            out.append({"type": "subscription", "severity": f["severity"], "title": f["title"],
                        "detail": f["detail"], "impact_monthly": f["potential_monthly_savings"]})
    for a in detect_anomalies(df, as_of, lookback_days=45):
        if a["severity"] in ("high", "medium"):
            out.append({"type": "anomaly", "severity": a["severity"],
                        "title": f"Unusual charge: {a['merchant']} {fmt_money(abs(a['amount']))} on {a['date']}",
                        "detail": "; ".join(a["reasons"]), "impact_monthly": 0.0})
    if budgets:
        for b in budget_status(df, budgets, as_of=as_of)["items"]:
            if b["status"] != "on_track":
                out.append({"type": "budget", "severity": "high" if b["status"] == "over" else "medium",
                            "title": f"{b['category']} budget {'exceeded' if b['status'] == 'over' else 'at risk'}",
                            "detail": f"Spent {fmt_money(b['spent'])} of {fmt_money(b['limit'])}; "
                                      f"on pace for {fmt_money(b['projected_month_end'])} this month.",
                            "impact_monthly": round(max(0.0, b["projected_month_end"] - b["limit"]), 2)})
    # category trend: last 3 full months vs the 3 before
    months = complete_months(as_of, 6)
    sp = spending_frame(df)
    piv = sp[sp["month"].isin(months)].groupby(["category", "month"])["spend_cents"].sum().unstack(fill_value=0).reindex(columns=months, fill_value=0)
    for cat, row in piv.iterrows():
        before, after = row[months[:3]].sum() / 3, row[months[3:]].sum() / 3
        if before >= cur.scaled_minor(50) and after > before * 1.2 and cat not in FIXED_CATEGORIES:
            out.append({"type": "trend", "severity": "medium", "title": f"{cat} spending is trending up",
                        "detail": f"Averaged {fmt_cents(round(after))}/month over the last 3 months vs "
                                  f"{fmt_cents(round(before))} before (+{pct(after - before, before)}%).",
                        "impact_monthly": money(round(after - before))})
    sts = safe_to_spend(df, as_of=as_of)
    if sts["safe_to_spend"] >= 0:
        out.append({"type": "safe_to_spend", "severity": "info",
                    "title": f"Safe to spend this month: {fmt_money(sts['safe_to_spend'])}",
                    "detail": f"After {fmt_money(sts['recurring_still_due'])} of recurring charges still due and a {sts['savings_target_pct']:.0f}% savings target "
                              f"— about {fmt_money(sts['per_day'])}/day for {sts['days_left']} days.",
                    "impact_monthly": 0.0})
    else:
        out.append({"type": "safe_to_spend", "severity": "medium",
                    "title": f"{fmt_money(-sts['safe_to_spend'])} short of your {sts['savings_target_pct']:.0f}% savings target this month",
                    "detail": f"Spending so far plus {fmt_money(sts['recurring_still_due'])} of recurring charges still due leaves less than the "
                              f"{fmt_money(sts['savings_target'])} target. Pausing discretionary spending for the remaining {sts['days_left']} days helps close the gap.",
                    "impact_monthly": 0.0})
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    out.sort(key=lambda i: (order[i["severity"]], -i["impact_monthly"]))
    return out
