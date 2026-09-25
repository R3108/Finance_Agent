import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import planning as pl
from app import plans

from .conftest import AS_OF


def test_monthly_interest_is_exact_half_up():
    assert pl._monthly_interest(184000, 2699) == 4138          # 1840 * 26.99% / 12 = 41.3847
    assert pl._monthly_interest(100000, 1200) == 1000
    assert pl._monthly_interest(50, 1200) == 1                 # 0.5 cent rounds up
    assert pl._monthly_interest(123456, 0) == 0


def test_debt_payoff_strategies(user):
    p = pl.debt_payoff_plan(user.debts, 20000, AS_OF, user.tx)
    av, sb, mn = (p["strategies"][s] for s in ("avalanche", "snowball", "minimum"))
    assert av["feasible"] and sb["feasible"] and mn["feasible"]
    # avalanche never pays more interest than snowball; both beat minimum-only
    assert av["total_interest"] <= sb["total_interest"] < mn["total_interest"]
    assert av["months"] < mn["months"]
    assert av["payoff_order"][0]["name"] == "Store credit card"   # highest APR
    assert sb["payoff_order"][0]["name"] == "Furniture 0% plan"   # smallest balance
    assert p["recommended"] == "avalanche"
    # every cent borrowed plus interest is paid back
    assert av["total_paid"] == pytest.approx(p["total_debt"] + av["total_interest"], abs=0.001)
    assert p["chart"][0]["avalanche"] == p["total_debt"] and p["chart"][-1]["minimum"] == 0.0


def test_debt_plan_detects_negative_amortisation():
    debts = pd.DataFrame([{"id": 1, "name": "Payday", "kind": "loan", "balance_cents": 100000, "apr_bps": 9900, "min_payment_cents": 500}])
    p = pl.debt_payoff_plan(debts, 0, AS_OF)
    assert p["strategies"]["minimum"]["feasible"] is False
    assert p["strategies"]["minimum"]["debt_free_date"] is None


def test_net_worth_balances_out(user):
    n = pl.net_worth(user.tx, user.accounts, user.assets, user.debts, AS_OF)
    assert n["net_worth"] == round(n["total_assets"] - n["total_liabilities"], 2)
    assert n["total_liabilities"] == round(n["card_balances"] + n["debts"], 2)
    assert len(n["cash_history"]) == 12 and n["cash_history"][-1]["partial"]
    assert sum(a["value"] for a in n["asset_mix"]) == pytest.approx(n["total_assets"])


def test_bill_calendar_current_and_future(user):
    c = pl.bill_calendar(user.tx, None, AS_OF)
    events = {(d["date"], e["name"]): e for d in c["days"] for e in d["events"]}
    assert events[("2026-09-01", "Rent")]["status"] == "posted"
    assert events[("2026-09-22", "Insurance")]["status"] == "upcoming"
    assert all(e["status"] == "upcoming" for (d, _), e in events.items() if d > "2026-09-19")
    nxt = pl.bill_calendar(user.tx, "2026-10", AS_OF)
    assert any(e["name"] == "Rent" for d in nxt["days"] for e in d["events"])
    assert nxt["still_due"] == nxt["recurring_out"]  # nothing posted yet in a future month


def test_challenges_are_scored_from_transactions(user):
    daily = pl._discretionary_daily(user.tx)
    ch = {"id": 1, "type": "merchant_break", "title": "t", "params_json": json.dumps({"merchant": "Starbucks"}),
          "start_date": "2026-08-01", "end_date": "2026-08-31"}
    r = pl.evaluate_challenge(ch, user.tx, AS_OF)
    assert r["status"] == "lost" and r["detail"].startswith("Broken on 2026-08-")
    cap = {"id": 2, "type": "category_cap", "title": "t", "params_json": json.dumps({"category": "Travel", "cap_cents": 10000}),
           "start_date": "2026-08-01", "end_date": "2026-08-31"}
    assert pl.evaluate_challenge(cap, user.tx, AS_OF)["status"] == "won"
    no_spend = {"id": 3, "type": "no_spend", "title": "t", "params_json": json.dumps({"target_days": 1}),
                "start_date": "2026-08-01", "end_date": "2026-08-31"}
    expected, _ = pl._no_spend_days(daily, pd.Timestamp("2026-08-01").date(), pd.Timestamp("2026-08-31").date())
    r = pl.evaluate_challenge(no_spend, user.tx, AS_OF)
    assert r["detail"].startswith(f"{expected} of 1") and r["status"] == ("won" if expected else "lost")
    ov = pl.challenges_overview(user.challenges, user.tx, AS_OF)
    assert ov["active"] == 1 and {s["type"] for s in ov["suggestions"]} == {"category_cap", "merchant_break"}


def test_year_in_review(user):
    w = pl.year_in_review(user.tx, AS_OF)
    assert w["start"] == "2025-09-01" and w["end"] == "2026-08-31"
    assert w["saved"] == round(w["income"] - w["spending"], 2)
    assert w["biggest_purchase"]["merchant"] == "Best Buy"
    assert w["coffee"]["visits"] > 0 and w["persona"]
    assert 0 < w["no_spend_days"] <= w["days"]
    assert pl.year_in_review(user.tx, AS_OF, 2026)["months"] == 9


def test_plan_gating_and_quota(seeded):
    from app.auth import SESSION_COOKIE, create_session
    from app.db import get_conn
    from app.main import app
    client = TestClient(app, cookies={SESSION_COOKIE: create_session(1)})
    assert client.get("/api/debts/plan").status_code == 200          # demo user is Pro
    with get_conn() as conn:
        conn.execute("UPDATE users SET plan = 'free' WHERE id = 1")
    try:
        r = client.get("/api/wrapped")
        assert r.status_code == 402 and r.json()["feature"] == "wrapped"
        assert client.get("/api/net-worth").status_code == 200       # free feature stays open
        assert plans.allows("free", "bill_calendar") and not plans.allows("free", "debt_planner")
        # Family is a real, purchasable tier now; this account is refused because it's a demo sandbox
        family = client.post("/api/billing/checkout", json={"plan": "family", "period": "monthly"})
        assert family.status_code == 403 and "Demo" in family.json()["detail"]
        assert client.post("/api/household", json={"name": "Nope"}).status_code == 402
        with get_conn() as conn:
            conn.executemany("INSERT INTO chat_messages (user_id, thread_id, role, content) VALUES (1, 'q', 'user', 'hi')",
                             [()] * 15)
        assert client.post("/api/chat", json={"message": "hello"}).status_code == 402
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM chat_messages WHERE thread_id = 'q'")
            conn.execute("UPDATE users SET plan = 'pro' WHERE id = 1")
