import pandas as pd
import pytest

from app import analytics as an
from app.calc import evaluate
from app.categorizer import categorize
from app.ingest import parse_csv, to_cents

from .conftest import AS_OF


def test_categorizer_rules_and_user_override():
    assert categorize("NETFLIX.COM 866-579-7172 CA") == ("Netflix", "Subscriptions", "rule")
    assert categorize("UBER EATS PENDING")[1] == "Dining"
    assert categorize("UBER *TRIP HELP.UBER.COM")[1] == "Transport"
    assert categorize("PARENT TEACHER ASSOC")[1] == "Uncategorized"  # 'RENT' must not match inside words
    rules = [{"pattern": "netflix", "category": "Entertainment", "priority": 100}]
    assert categorize("NETFLIX.COM", rules)[1:] == ("Entertainment", "user")


def test_money_is_exact():
    assert to_cents("$1,234.565") == 123457
    assert to_cents("0.1") + to_cents("0.2") == 30
    assert evaluate("0.1 + 0.2") == "0.3"
    assert evaluate("pct_change(15.49, 17.99)") == "16.1394"
    assert evaluate("round(652.92 * 12, 2)") == "7835.04"
    with pytest.raises(ValueError):
        evaluate("__import__('os')")
    with pytest.raises(ValueError):
        evaluate("1/0")


def test_monthly_summary_matches_raw_sums(user):
    rows = an.monthly_summary(user.tx, 6, AS_OF)
    assert [r["month"] for r in rows][-1] == "2026-09" and rows[-1]["partial"]
    tx = user.tx
    aug = tx[(tx["month"] == "2026-08") & ~tx["is_transfer"]]
    expected_spend = -aug.loc[aug["category"] != "Income", "amount_cents"].sum()
    got = next(r for r in rows if r["month"] == "2026-08")
    assert got["spending"] == round(expected_spend / 100, 2)
    assert got["net"] == round(got["income"] - got["spending"], 2)


def test_subscription_detection(user):
    subs = {s["merchant"]: s for s in an.subscriptions_summary(user.tx, as_of=AS_OF)["items"]}
    assert subs["Netflix"]["last_amount"] == 17.99 and subs["Netflix"]["cadence"] == "monthly"
    assert subs["Amazon Prime"]["cadence"] == "annual"
    assert subs["New York Times"]["cadence"] == "four_weekly"
    assert subs["Headspace"]["active"] is False
    assert subs["Rent"]["kind"] == "bill"
    # variable spending must not be detected as a subscription
    assert not {"Whole Foods", "Starbucks", "Amazon", "Uber"} & subs.keys()


def test_unusual_subscriptions(user):
    flags = an.unusual_subscriptions(user.tx, AS_OF)
    kinds = {(f["type"], f["merchant"]) for f in flags}
    assert ("price_increase", "Netflix") in kinds
    assert ("price_increase", "New York Times") in kinds
    assert ("duplicate", "Spotify") in kinds
    assert ("new", "ChatGPT Plus") in kinds
    assert ("upcoming_renewal", "Amazon Prime") in kinds
    assert ("stopped", "Headspace") in kinds
    assert any(f["type"] == "overlap" and "Netflix" in f["merchants"] for f in flags)


def test_anomalies(user):
    found = {(a["merchant"], a["amount"]) for a in an.detect_anomalies(user.tx, AS_OF)}
    assert ("Best Buy", -1299.99) in found
    assert ("Whole Foods", -142.37) in found
    assert ("Zxq Digital Svcs", -89.0) in found


def test_budget_suggestions_and_status(user):
    s = an.suggest_budgets(user.tx, user.budgets, AS_OF)
    by = {i["category"]: i for i in s["suggestions"]}
    assert by["Housing"]["suggested"] == 1650.0
    assert all(i["suggested"] * 100 % 500 == 0 for i in s["suggestions"])  # rounded to $5 or $10
    total = sum(round(i["suggested"] * 100) for i in s["suggestions"])
    assert s["total_suggested"] == total / 100
    st = an.budget_status(user.tx, {"Dining": 10_000}, "2026-08", AS_OF)
    assert st["items"][0]["status"] == "over"


def test_forecast_health_and_simulation(user):
    f = an.cashflow_forecast(user.tx, user.accounts, 30, AS_OF)
    assert len(f["series"]) == 30 and f["scheduled_income"] > 0
    h = an.health_score(user.tx, user.accounts, user.budgets, AS_OF)
    assert 0 <= h["score"] <= 100 and len(h["components"]) == 5
    sim = an.simulate_savings(user.tx, user.goals, ["Hulu", "Max"], {"Dining": 50}, AS_OF)
    assert sim["monthly_savings"] == round(17.99 + 16.99 + sim["category_cuts"][0]["monthly_savings"], 2)
    assert sim["annual_savings"] == round(sim["monthly_savings"] * 12, 2)


def test_csv_import_parsing():
    csv = b"Date,Description,Debit,Credit\n2026-09-01,STARBUCKS 123,5.25,\n2026-09-02,ACME PAYROLL,,2000.00\n"
    rows = parse_csv(csv, None)
    assert [r.amount_cents for r in rows] == [-525, 200000]
    csv2 = b"Posted Date,Payee,Amount\n09/03/2026,NETFLIX.COM,15.49\n"
    assert parse_csv(csv2, None, expenses_positive=True)[0].amount_cents == -1549


def test_reimport_is_idempotent(user):
    from app.ingest import RawTxn, insert_transactions
    rows = [RawTxn("2026-09-10", "TEST MERCHANT X", -1000, 1), RawTxn("2026-09-10", "TEST MERCHANT X", -1000, 1)]
    assert insert_transactions(1, rows)["inserted"] == 2  # identical charges in one file are both kept
    assert insert_transactions(1, rows)["inserted"] == 0  # re-uploading the file is a no-op
