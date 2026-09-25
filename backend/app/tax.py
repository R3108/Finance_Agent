"""Freelancer bookkeeping and tax estimates.

What this does: splits a ledger into business and personal, groups the business side into deduction
buckets, and turns that into an estimated tax bill, an advance-tax schedule and an export pack for
an accountant. All of it in integer minor units, like the rest of the app.

Three things this module is deliberately careful about:

*   **Rates are data, versioned by financial year.** `SLABS` is a table per (regime, year), not
    constants buried in a function. Tax rules change every budget; when a year isn't in the table
    the most recent one is used and every response carries `rates_stale: True` plus the year the
    figures actually came from, so a stale estimate announces itself instead of quietly lying.
*   **Nothing is auto-classified.** `suggest_tags` proposes, and its rows are written with
    `source='suggestion'`; only a person confirming them makes them count. Silently deciding that
    someone's dinner was a business expense is how you get a bad assessment.
*   **This is an estimate, not advice.** `DISCLAIMER` is returned with every estimate and shown in
    the UI. Surcharge, capital gains, house-property income, foreign income, 80-series deductions
    beyond the ones listed and TDS credits are not modelled.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Iterable

import pandas as pd

from . import analytics as an
from . import money as cur
from .db import get_conn

DISCLAIMER = ("An estimate from your own transactions, for planning only. It isn't tax advice and "
              "it doesn't replace a return prepared by a qualified professional.")

# ----------------------------------------------------------------------------- rate tables

@dataclass(frozen=True)
class Regime:
    key: str
    label: str
    country: str
    description: str


REGIMES: dict[str, Regime] = {
    "IN_new": Regime("IN_new", "India — new regime (default)", "IN",
                     "Lower slab rates, almost no deductions. The default for individuals."),
    "IN_old": Regime("IN_old", "India — old regime", "IN",
                     "Higher slab rates, but 80C/80D and similar deductions are allowed."),
    "IN_44ADA": Regime("IN_44ADA", "India — presumptive (44ADA)", "IN",
                       "For professionals under the turnover limit: half of gross receipts is "
                       "presumed to be profit and actual expenses are ignored."),
    "none": Regime("none", "No tax estimate", "*",
                   "Track business income and expenses only — no tax is calculated."),
}

#: (regime, financial year) -> slabs as (upper_bound_paise or None, rate). Bounds are inclusive
#: uppers; None means "and above". Sourced from the Finance Act for that year — REVIEW EVERY BUDGET.
SLABS: dict[tuple[str, str], list[tuple[int | None, float]]] = {
    ("IN_new", "2025-26"): [(400_000_00, 0.00), (800_000_00, 0.05), (1_200_000_00, 0.10),
                            (1_600_000_00, 0.15), (2_000_000_00, 0.20), (2_400_000_00, 0.25),
                            (None, 0.30)],
    ("IN_old", "2025-26"): [(250_000_00, 0.00), (500_000_00, 0.05), (1_000_000_00, 0.20),
                            (None, 0.30)],
}

#: Per (regime, year): health & education cess, the 87A rebate ceiling and the rebate cap.
PARAMS: dict[tuple[str, str], dict] = {
    ("IN_new", "2025-26"): {"cess": 0.04, "rebate_limit": 1_200_000_00, "rebate_cap": 60_000_00,
                            "standard_deduction": 75_000_00},
    ("IN_old", "2025-26"): {"cess": 0.04, "rebate_limit": 500_000_00, "rebate_cap": 12_500_00,
                            "standard_deduction": 50_000_00},
}

#: 44ADA: presumed profit share, and the gross-receipts ceiling for eligibility.
PRESUMPTIVE = {"share": 0.50, "receipt_limit": 7_500_000_00}

#: India's advance-tax instalments: (month, day, cumulative share of the year's liability due).
ADVANCE_TAX_SCHEDULE = [(6, 15, 0.15), (9, 15, 0.45), (12, 15, 0.75), (3, 15, 1.00)]

#: Below this, advance tax isn't required at all (section 208).
ADVANCE_TAX_THRESHOLD = 10_000_00


def financial_year(d: date, country: str = "IN") -> str:
    """India's FY runs April–March: 2026-09-20 falls in '2026-27'."""
    if country != "IN":
        return str(d.year)
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def fy_bounds(fy: str, country: str = "IN") -> tuple[date, date]:
    if country != "IN":
        year = int(fy)
        return date(year, 1, 1), date(year, 12, 31)
    start = int(fy.split("-")[0])
    return date(start, 4, 1), date(start + 1, 3, 31)


def _rate_year(regime: str, fy: str) -> tuple[str, bool]:
    """The year whose published rates we'll actually use, and whether it's older than asked for.

    Returning the fallback rather than raising keeps the product usable the day a new financial
    year starts — but the caller is told, so the UI can say the figures need updating.
    """
    if (regime, fy) in SLABS:
        return fy, False
    known = sorted(year for (r, year) in SLABS if r == regime)
    if not known:
        return fy, True
    return known[-1], known[-1] != fy


def slab_tax(taxable_paise: int, regime: str, fy: str) -> tuple[int, list[dict]]:
    """Tax before cess and rebate, plus the per-slab working so the UI can show it."""
    year, _ = _rate_year(regime, fy)
    slabs = SLABS.get((regime, year))
    if not slabs:
        return 0, []
    total, lower, bands = 0, 0, []
    for upper, rate in slabs:
        if taxable_paise <= lower:
            break
        top = taxable_paise if upper is None else min(taxable_paise, upper)
        amount = top - lower
        if amount > 0:
            band = int(round(amount * rate))
            total += band
            bands.append({"from": an.money(lower), "to": None if upper is None else an.money(upper),
                          "rate_pct": round(rate * 100, 2), "taxable": an.money(amount),
                          "tax": an.money(band)})
        lower = upper if upper is not None else taxable_paise
        if upper is None:
            break
    return total, bands


# ----------------------------------------------------------------------------- deduction buckets

@dataclass(frozen=True)
class Deduction:
    key: str
    label: str
    hint: str
    #: Default deductible share for a mixed-use bucket (phone bills are rarely 100% business).
    default_share: int = 100


DEDUCTIONS: dict[str, Deduction] = {d.key: d for d in [
    Deduction("software", "Software & subscriptions", "Tools you pay for to do the work."),
    Deduction("equipment", "Equipment & hardware",
              "Laptops, phones, desks. Large items may need to be depreciated rather than "
              "deducted in one year — check with your accountant."),
    Deduction("home_office", "Home office", "Rent, electricity and internet, apportioned to work use.", 30),
    Deduction("phone_internet", "Phone & internet", "The work share of your connections.", 50),
    Deduction("travel", "Travel & transport", "Journeys made for work, not commuting."),
    Deduction("professional", "Professional fees", "Accountants, lawyers, contractors you hire."),
    Deduction("marketing", "Marketing & website", "Ads, domains, hosting, design."),
    Deduction("education", "Training & research", "Courses and material that keep your skills current."),
    Deduction("meals", "Client meals & entertainment",
              "Often only partly allowed, and needs a record of who and why.", 50),
    Deduction("bank_fees", "Bank & payment fees", "Gateway charges, transfer fees on business accounts."),
    Deduction("other", "Other business expenses", "Anything else wholly for the business."),
]}

#: Spending category -> the bucket it usually belongs to when it IS business. Only ever a
#: suggestion: the same category is personal far more often than not for most people.
CATEGORY_HINTS: dict[str, str] = {
    "Subscriptions": "software",
    "Education": "education",
    "Travel": "travel",
    "Transport": "travel",
    "Utilities": "phone_internet",
    "Housing": "home_office",
    "Shopping": "equipment",
    "Fees": "bank_fees",
    "Dining": "meals",
}

#: Merchants that are business tools often enough to be worth proposing on sight.
MERCHANT_HINTS: dict[str, str] = {
    "Adobe Creative Cloud": "software", "ChatGPT Plus": "software", "Dropbox": "software",
    "Google Workspace": "software", "Google One": "software", "Online Learning": "education",
    "Electronics Store": "equipment", "IRCTC": "travel", "Airline": "travel", "Lodging": "travel",
}


class TaxError(ValueError):
    """Bad tax input, phrased for the person who typed it."""


# ----------------------------------------------------------------------------- profile & tags

def profile(user_id: int, as_of: date | None = None) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tax_profiles WHERE user_id = ?", (user_id,)).fetchone()
    today = as_of or date.today()
    if row is None:
        return {"regime": "IN_new", "financial_year": financial_year(today),
                "business_share_default": 100, "gst_registered": False}
    regime = row["regime"] if row["regime"] in REGIMES else "IN_new"
    return {"regime": regime,
            "financial_year": row["financial_year"] or financial_year(today, REGIMES[regime].country),
            "business_share_default": int(row["business_share_default"]),
            "gst_registered": bool(row["gst_registered"])}


def save_profile(user_id: int, regime: str | None = None, fy: str | None = None,
                 business_share_default: int | None = None, gst_registered: bool | None = None) -> dict:
    current = profile(user_id)
    if regime is not None and regime not in REGIMES:
        raise TaxError(f"Unknown regime. Choose one of: {', '.join(REGIMES)}")
    if business_share_default is not None and not 1 <= business_share_default <= 100:
        raise TaxError("The default business share must be between 1% and 100%.")
    merged = {
        "regime": regime or current["regime"],
        "financial_year": fy or current["financial_year"],
        "business_share_default": current["business_share_default"] if business_share_default is None
        else business_share_default,
        "gst_registered": current["gst_registered"] if gst_registered is None else gst_registered,
    }
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO tax_profiles (user_id, regime, financial_year, business_share_default, gst_registered)
               VALUES (?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET regime = excluded.regime,
                   financial_year = excluded.financial_year,
                   business_share_default = excluded.business_share_default,
                   gst_registered = excluded.gst_registered,
                   updated_at = datetime('now')""",
            (user_id, merged["regime"], merged["financial_year"],
             merged["business_share_default"], int(merged["gst_registered"])))
    return merged


def tag(user_id: int, transaction_id: int, kind: str, deduction: str | None = None,
        share_pct: int = 100, client: str | None = None, note: str | None = None,
        source: str = "user") -> None:
    if kind not in ("business", "personal"):
        raise TaxError("A transaction is either 'business' or 'personal'.")
    if deduction is not None and deduction not in DEDUCTIONS:
        raise TaxError(f"Unknown deduction bucket. Choose one of: {', '.join(DEDUCTIONS)}")
    if not 1 <= share_pct <= 100:
        raise TaxError("The deductible share must be between 1% and 100%.")
    with get_conn() as conn:
        owns = conn.execute("SELECT 1 FROM transactions WHERE id = ? AND user_id = ?",
                            (transaction_id, user_id)).fetchone()
        if not owns:
            raise TaxError("That transaction isn't yours.")
        conn.execute(
            """INSERT INTO tax_tags (user_id, transaction_id, kind, deduction, share_pct, client, note, source)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id, transaction_id) DO UPDATE SET kind = excluded.kind,
                   deduction = excluded.deduction, share_pct = excluded.share_pct,
                   client = excluded.client, note = excluded.note, source = excluded.source,
                   updated_at = datetime('now')""",
            (user_id, transaction_id, kind, deduction, share_pct, client, note, source))


def untag(user_id: int, transaction_id: int) -> int:
    with get_conn() as conn:
        return conn.execute("DELETE FROM tax_tags WHERE user_id = ? AND transaction_id = ?",
                            (user_id, transaction_id)).rowcount


def tags_frame(user_id: int) -> pd.DataFrame:
    from .db import query_df
    df = query_df("SELECT * FROM tax_tags WHERE user_id = ?", (user_id,))
    if df.empty:
        return pd.DataFrame(columns=["transaction_id", "kind", "deduction", "share_pct", "client", "source"])
    return df


def tagged(tx: pd.DataFrame, tags: pd.DataFrame) -> pd.DataFrame:
    """The transaction frame with its business classification joined on."""
    if tx.empty:
        return tx.assign(kind=None, deduction=None, share_pct=100, client=None, tag_source=None)
    out = tx.merge(tags.rename(columns={"id": "tag_id", "source": "tag_source"}),
                   left_on="id", right_on="transaction_id", how="left")
    out["share_pct"] = out["share_pct"].fillna(100).astype(int)
    return out


def suggest_tags(tx: pd.DataFrame, tags: pd.DataFrame, limit: int = 40) -> list[dict]:
    """Transactions that look like business expenses, for a person to confirm or reject.

    Ranked by amount, because that's where a wrong answer costs the most. Nothing here is applied;
    confirming a row is what writes a tag.
    """
    done = set(tags["transaction_id"]) if not tags.empty else set()
    sp = an.spending_frame(tx)
    out = []
    for r in sp.itertuples():
        if r.id in done:
            continue
        bucket = MERCHANT_HINTS.get(r.merchant) or CATEGORY_HINTS.get(r.category)
        if not bucket:
            continue
        d = DEDUCTIONS[bucket]
        out.append({
            "transaction_id": int(r.id), "date": r.date.date().isoformat(), "merchant": r.merchant,
            "category": r.category, "amount": an.money(r.spend_cents),
            "amount_text": an.fmt_cents(r.spend_cents),
            "deduction": bucket, "deduction_label": d.label, "suggested_share": d.default_share,
            "why": (f"{r.merchant} is usually a business tool." if r.merchant in MERCHANT_HINTS
                    else f"{r.category} spending is often partly a business cost."),
        })
    out.sort(key=lambda s: -s["amount"])
    return out[:limit]


# ----------------------------------------------------------------------------- the numbers

def _window(tx: pd.DataFrame, fy: str, country: str) -> pd.DataFrame:
    start, end = fy_bounds(fy, country)
    return tx[(tx["date"].dt.date >= start) & (tx["date"].dt.date <= end)]


def business_summary(tx: pd.DataFrame, tags: pd.DataFrame, fy: str, country: str = "IN") -> dict:
    """Gross receipts, deductible expenses by bucket, and net profit — all from confirmed tags."""
    joined = tagged(_window(tx, fy, country), tags)
    business = joined[joined["kind"] == "business"]

    income_rows = business[business["amount_cents"] > 0]
    gross_receipts = int(income_rows["amount_cents"].sum())

    expense_rows = business[business["amount_cents"] < 0].copy()
    # a mixed-use charge only counts for its business share
    expense_rows["deductible_cents"] = (
        -expense_rows["amount_cents"] * expense_rows["share_pct"] / 100).round().astype("int64")
    total_expenses = int(expense_rows["deductible_cents"].sum())

    buckets = []
    if not expense_rows.empty:
        grouped = expense_rows.groupby(expense_rows["deduction"].fillna("other"))
        for key, rows in grouped:
            amount = int(rows["deductible_cents"].sum())
            spec = DEDUCTIONS.get(str(key), DEDUCTIONS["other"])
            buckets.append({"key": str(key), "label": spec.label, "amount": an.money(amount),
                            "amount_text": an.fmt_cents(amount), "transactions": int(len(rows)),
                            "share_pct": an.pct(amount, total_expenses)})
        buckets.sort(key=lambda b: -b["amount"])

    clients = []
    if not income_rows.empty:
        by_client = income_rows.groupby(income_rows["client"].fillna("Unattributed"))["amount_cents"].agg(["sum", "count"])
        clients = [{"client": str(name), "amount": an.money(int(row["sum"])),
                    "amount_text": an.fmt_cents(int(row["sum"])), "invoices": int(row["count"]),
                    "share_pct": an.pct(int(row["sum"]), gross_receipts)}
                   for name, row in by_client.sort_values("sum", ascending=False).iterrows()]

    return {
        "financial_year": fy,
        "gross_receipts": an.money(gross_receipts),
        "gross_receipts_cents": gross_receipts,
        "total_expenses": an.money(total_expenses),
        "total_expenses_cents": total_expenses,
        "net_profit": an.money(gross_receipts - total_expenses),
        "net_profit_cents": gross_receipts - total_expenses,
        "margin_pct": an.pct(gross_receipts - total_expenses, gross_receipts),
        "buckets": buckets,
        "clients": clients,
        "tagged_transactions": int(len(business)),
        "untagged_spend": an.money(int(an.spending_frame(
            joined[joined["kind"].isna()])["spend_cents"].sum() or 0)),
    }


def estimate(summary: dict, regime: str, fy: str, paid_cents: int = 0) -> dict:
    """Turn a business summary into an estimated tax bill for the chosen regime."""
    if regime == "none":
        return {"regime": regime, "applicable": False, "disclaimer": DISCLAIMER,
                "reason": "No tax estimate is configured — this view tracks income and expenses only."}

    gross = summary["gross_receipts_cents"]
    if regime == "IN_44ADA":
        eligible = gross <= PRESUMPTIVE["receipt_limit"]
        taxable_base = int(round(gross * PRESUMPTIVE["share"]))
        rate_regime = "IN_new"
    else:
        eligible = True
        taxable_base = max(0, summary["net_profit_cents"])
        rate_regime = regime

    year, stale = _rate_year(rate_regime, fy)
    params = PARAMS.get((rate_regime, year), {})
    taxable = max(0, taxable_base)

    base_tax, bands = slab_tax(taxable, rate_regime, fy)
    rebate = 0
    if params.get("rebate_limit") and taxable <= params["rebate_limit"]:
        rebate = min(base_tax, params.get("rebate_cap", base_tax))
    after_rebate = max(0, base_tax - rebate)
    cess = int(round(after_rebate * params.get("cess", 0)))
    total = after_rebate + cess
    outstanding = max(0, total - paid_cents)

    return {
        "regime": regime,
        "regime_label": REGIMES[regime].label,
        "applicable": True,
        "eligible": eligible,
        "eligibility_note": (None if eligible else
                             f"Gross receipts of {an.fmt_cents(gross)} are above the "
                             f"{an.fmt_cents(PRESUMPTIVE['receipt_limit'])} limit for presumptive taxation."),
        "financial_year": fy,
        "rates_from": year,
        # the UI shows a warning on this: an estimate on last year's rates is still worth having,
        # but the person must know which year's rules produced it
        "rates_stale": stale,
        "taxable_income": an.money(taxable),
        "taxable_income_text": an.fmt_cents(taxable),
        "presumptive_share_pct": round(PRESUMPTIVE["share"] * 100) if regime == "IN_44ADA" else None,
        "bands": bands,
        "tax_before_rebate": an.money(base_tax),
        "rebate": an.money(rebate),
        "cess": an.money(cess),
        "total_tax": an.money(total),
        "total_tax_cents": total,
        "total_tax_text": an.fmt_cents(total),
        "already_paid": an.money(paid_cents),
        "outstanding": an.money(outstanding),
        "outstanding_text": an.fmt_cents(outstanding),
        "effective_rate_pct": an.pct(total, taxable),
        "disclaimer": DISCLAIMER,
    }


def compare_regimes(summary: dict, fy: str, paid_cents: int = 0) -> list[dict]:
    """The same year costed under every regime, cheapest first — the question freelancers actually ask."""
    out = []
    for key in ("IN_new", "IN_old", "IN_44ADA"):
        e = estimate(summary, key, fy, paid_cents)
        if not e.get("applicable") or not e.get("eligible", True):
            continue
        out.append({"regime": key, "label": REGIMES[key].label, "total_tax": e["total_tax"],
                    "total_tax_text": e["total_tax_text"], "taxable_income": e["taxable_income"],
                    "rates_stale": e["rates_stale"]})
    out.sort(key=lambda r: r["total_tax"])
    if out:
        out[0]["best"] = True
    return out


def advance_tax_schedule(total_tax_cents: int, fy: str, as_of: date, paid_cents: int = 0) -> dict:
    """India's four instalments, with what's due now and what's already covered."""
    if total_tax_cents < ADVANCE_TAX_THRESHOLD:
        return {"required": False,
                "reason": f"Advance tax isn't required below {an.fmt_cents(ADVANCE_TAX_THRESHOLD)} of liability.",
                "instalments": []}
    fy_start = int(fy.split("-")[0])
    instalments, due_by_now = [], 0
    for month, day, share in ADVANCE_TAX_SCHEDULE:
        year = fy_start if month >= 4 else fy_start + 1
        when = date(year, month, day)
        cumulative = int(round(total_tax_cents * share))
        previous = instalments[-1]["cumulative_cents"] if instalments else 0
        overdue = when < as_of
        if overdue:
            due_by_now = cumulative
        instalments.append({
            "due_date": when.isoformat(), "share_pct": round(share * 100),
            "cumulative_cents": cumulative, "cumulative": an.money(cumulative),
            "instalment": an.money(cumulative - previous),
            "instalment_text": an.fmt_cents(cumulative - previous),
            "status": "paid" if paid_cents >= cumulative else ("overdue" if overdue else "upcoming"),
        })
    shortfall = max(0, due_by_now - paid_cents)
    return {
        "required": True,
        "instalments": instalments,
        "paid": an.money(paid_cents),
        "due_so_far": an.money(due_by_now),
        "shortfall": an.money(shortfall),
        "shortfall_text": an.fmt_cents(shortfall),
        "note": ("Interest under sections 234B and 234C can apply to instalments paid late or short. "
                 "This schedule doesn't calculate that interest."),
    }


def payments(user_id: int, fy: str, country: str = "IN") -> list[dict]:
    start, end = fy_bounds(fy, country)
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, paid_on, amount_cents, kind, note FROM tax_payments
               WHERE user_id = ? AND paid_on BETWEEN ? AND ? ORDER BY paid_on DESC""",
            (user_id, start.isoformat(), end.isoformat())).fetchall()
    return [{**dict(r), "amount": an.money(r["amount_cents"]),
             "amount_text": an.fmt_cents(r["amount_cents"])} for r in rows]


def add_payment(user_id: int, paid_on: date, amount_cents: int, kind: str, note: str | None) -> int:
    if amount_cents <= 0:
        raise TaxError("A payment has to be more than zero.")
    if kind not in ("advance", "self_assessment", "tds"):
        raise TaxError("Payment kind must be advance, self_assessment or tds.")
    with get_conn() as conn:
        return conn.execute(
            "INSERT INTO tax_payments (user_id, paid_on, amount_cents, kind, note) VALUES (?,?,?,?,?)",
            (user_id, paid_on.isoformat(), amount_cents, kind, note)).lastrowid


def delete_payment(user_id: int, payment_id: int) -> int:
    with get_conn() as conn:
        return conn.execute("DELETE FROM tax_payments WHERE id = ? AND user_id = ?",
                            (payment_id, user_id)).rowcount


def overview(user_id: int, tx: pd.DataFrame, as_of: date, fy: str | None = None) -> dict:
    """Everything the freelancer view needs, in one call."""
    prof = profile(user_id, as_of)
    country = REGIMES[prof["regime"]].country
    year = fy or prof["financial_year"]
    tags = tags_frame(user_id)
    summary = business_summary(tx, tags, year, country)
    paid = payments(user_id, year, country)
    paid_cents = sum(p["amount_cents"] for p in paid)

    est = estimate(summary, prof["regime"], year, paid_cents)
    schedule = (advance_tax_schedule(est.get("total_tax_cents", 0), year, as_of, paid_cents)
                if country == "IN" and est.get("applicable") else {"required": False, "instalments": []})

    return {
        "profile": prof,
        "currency": cur.current_code(),
        "financial_year": year,
        "available_years": _available_years(tx, country),
        "summary": summary,
        "estimate": est,
        "comparison": compare_regimes(summary, year, paid_cents) if country == "IN" else [],
        "advance_tax": schedule,
        "payments": paid,
        "suggestions": suggest_tags(_window(tx, year, country), tags),
        "regimes": [{"key": r.key, "label": r.label, "description": r.description}
                    for r in REGIMES.values()],
        "deductions": [{"key": d.key, "label": d.label, "hint": d.hint, "default_share": d.default_share}
                       for d in DEDUCTIONS.values()],
        "disclaimer": DISCLAIMER,
    }


def _available_years(tx: pd.DataFrame, country: str) -> list[str]:
    if tx.empty:
        return [financial_year(date.today(), country)]
    years = {financial_year(d.date(), country) for d in tx["date"]}
    return sorted(years, reverse=True)


# ----------------------------------------------------------------------------- accountant export

EXPORT_COLUMNS = ["date", "merchant", "description", "account", "category", "kind", "deduction",
                  "deduction_label", "share_pct", "client", "amount", "deductible_amount", "note",
                  "receipt"]


def export_rows(user_id: int, tx: pd.DataFrame, fy: str, country: str = "IN") -> Iterable[list]:
    """One row per business transaction, with the deductible amount already worked out.

    Yields lists rather than building a file, so the API can stream a year of rows without holding
    them all in memory.
    """
    tags = tags_frame(user_id)
    joined = tagged(_window(tx, fy, country), tags)
    business = joined[joined["kind"] == "business"].sort_values("date")
    with get_conn() as conn:
        receipts = {r["transaction_id"]: r["filename"] for r in conn.execute(
            "SELECT transaction_id, filename FROM receipts WHERE user_id = ? AND transaction_id IS NOT NULL",
            (user_id,))}

    yield EXPORT_COLUMNS
    for r in business.itertuples():
        deductible = (abs(r.amount_cents) * r.share_pct / 100) if r.amount_cents < 0 else r.amount_cents
        bucket = r.deduction if isinstance(r.deduction, str) else ""
        yield [
            r.date.date().isoformat(), r.merchant, r.description, r.account or "", r.category,
            r.kind, bucket, DEDUCTIONS[bucket].label if bucket in DEDUCTIONS else "",
            r.share_pct, r.client if isinstance(r.client, str) else "",
            f"{an.money(r.amount_cents):.2f}", f"{an.money(int(round(deductible))):.2f}",
            r.note if isinstance(r.note, str) else "", receipts.get(int(r.id), ""),
        ]


def export_manifest(user_id: int, tx: pd.DataFrame, fy: str, country: str = "IN") -> dict:
    """A short header for the export, so the accountant knows what they're looking at."""
    tags = tags_frame(user_id)
    summary = business_summary(tx, tags, fy, country)
    prof = profile(user_id)
    return {"financial_year": fy, "regime": REGIMES[prof["regime"]].label,
            "gross_receipts": summary["gross_receipts"], "total_expenses": summary["total_expenses"],
            "net_profit": summary["net_profit"], "currency": cur.current_code(),
            "generated_on": date.today().isoformat(), "disclaimer": DISCLAIMER}


def rules_from_tags(user_id: int) -> dict[str, dict]:
    """Merchants the user has already classified, so the same merchant can be tagged consistently.

    Only merchants they've been consistent about are returned: one Uber ride tagged business doesn't
    mean every Uber ride is.
    """
    from .db import query_df
    df = query_df(
        """SELECT t.merchant, g.kind, g.deduction, g.share_pct, COUNT(*) AS n
           FROM tax_tags g JOIN transactions t ON t.id = g.transaction_id
           WHERE g.user_id = ? AND g.source = 'user'
           GROUP BY t.merchant, g.kind, g.deduction, g.share_pct""",
        (user_id,))
    if df.empty:
        return {}
    out: dict[str, dict] = {}
    for merchant, rows in df.groupby("merchant"):
        if len(rows) > 1:      # classified inconsistently: no safe default to offer
            continue
        row = rows.iloc[0]
        if int(row["n"]) < 2:  # a single example isn't a pattern
            continue
        out[str(merchant)] = {"kind": row["kind"],
                              "deduction": row["deduction"] if isinstance(row["deduction"], str) else None,
                              "share_pct": int(row["share_pct"]), "seen": int(row["n"])}
    return out


def apply_rules(user_id: int, tx: pd.DataFrame) -> int:
    """Tag untagged transactions from merchants the user has consistently classified. Returns the count."""
    rules = rules_from_tags(user_id)
    if not rules:
        return 0
    tags = tags_frame(user_id)
    done = set(tags["transaction_id"]) if not tags.empty else set()
    applied = 0
    for r in tx.itertuples():
        if r.id in done or r.merchant not in rules:
            continue
        rule = rules[r.merchant]
        tag(user_id, int(r.id), rule["kind"], rule["deduction"], rule["share_pct"], source="rule")
        applied += 1
    return applied


def stats(user_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS tagged,
                      SUM(kind = 'business') AS business,
                      SUM(source = 'user') AS by_hand
               FROM tax_tags WHERE user_id = ?""", (user_id,)).fetchone()
    return {"tagged": int(row["tagged"] or 0), "business": int(row["business"] or 0),
            "by_hand": int(row["by_hand"] or 0)}


def as_json(obj) -> str:
    return json.dumps(obj, default=str)
