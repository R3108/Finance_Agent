"""Freelancer bookkeeping, tax estimates and receipt matching."""
from __future__ import annotations

import dataclasses
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import receipts, tax
from app.db import get_conn

PW = "correct horse battery"


@pytest.fixture()
def client(seeded, tmp_path, monkeypatch):
    """A Pro account with the India sample ledger; receipts land in a temp directory."""
    from app import auth, config, mailer
    from app.main import app
    monkeypatch.setattr(mailer, "send", lambda *a, **k: None)
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, receipts_dir=tmp_path / "receipts"))
    auth._failures.clear()
    c = TestClient(app)
    c.email = f"tax-{uuid.uuid4().hex[:8]}@example.com"
    c.post("/api/auth/signup", json={"name": "Riya", "email": c.email, "password": PW,
                                     "sample_data": True, "currency": "INR"})
    with get_conn() as conn:
        conn.execute("UPDATE users SET plan = 'pro' WHERE email = ?", (c.email,))
        c.user_id = conn.execute("SELECT id FROM users WHERE email = ?", (c.email,)).fetchone()["id"]
    c.put("/api/tax/profile", json={"financial_year": "2025-26"})
    return c


def tag_some(client, n: int = 8) -> list[dict]:
    """Confirm the top suggestions as business expenses."""
    suggestions = client.get("/api/tax").json()["suggestions"][:n]
    for s in suggestions:
        client.put(f"/api/tax/tags/{s['transaction_id']}",
                   json={"kind": "business", "deduction": s["deduction"], "share_pct": s["suggested_share"]})
    return suggestions


# ----------------------------------------------------------------------------- rate tables

def test_financial_year_follows_the_indian_april_march_boundary():
    assert tax.financial_year(date(2026, 9, 20)) == "2026-27"
    assert tax.financial_year(date(2026, 3, 31)) == "2025-26"
    assert tax.financial_year(date(2026, 4, 1)) == "2026-27"
    assert tax.financial_year(date(2026, 6, 1), country="US") == "2026"


def test_missing_year_falls_back_and_says_so():
    """A new financial year must not break the estimate — but it must not pretend to be current."""
    year, stale = tax._rate_year("IN_new", "2099-00")
    assert stale is True and (("IN_new", year) in tax.SLABS)
    assert tax._rate_year("IN_new", "2025-26") == ("2025-26", False)


@pytest.mark.parametrize("taxable, expected", [
    (300_000_00, 0),                                   # below the first slab
    (600_000_00, 10_000_00),                           # 5% of the 2L above 4L
    (1_000_000_00, 20_000_00 + 20_000_00),             # 5% band full, then 10% of 2L
])
def test_slab_tax_is_computed_band_by_band(taxable, expected):
    total, bands = tax.slab_tax(taxable, "IN_new", "2025-26")
    assert total == expected
    assert sum(round(b["tax"] * 100) for b in bands) == total


def test_rebate_zeroes_small_incomes_under_the_new_regime():
    summary = {"gross_receipts_cents": 1_100_000_00, "net_profit_cents": 1_100_000_00}
    e = tax.estimate(summary, "IN_new", "2025-26")
    assert e["total_tax"] == 0 and e["rebate"] > 0


def test_cess_is_added_above_the_rebate_limit():
    summary = {"gross_receipts_cents": 2_000_000_00, "net_profit_cents": 2_000_000_00}
    e = tax.estimate(summary, "IN_new", "2025-26")
    assert e["rebate"] == 0
    assert e["cess"] == pytest.approx(round(e["tax_before_rebate"] * 0.04, 2), abs=0.01)
    assert e["total_tax"] == pytest.approx(e["tax_before_rebate"] + e["cess"], abs=0.01)


def test_presumptive_taxes_half_of_receipts_and_ignores_expenses():
    summary = {"gross_receipts_cents": 3_000_000_00, "net_profit_cents": 500_000_00}
    e = tax.estimate(summary, "IN_44ADA", "2025-26")
    assert e["taxable_income"] == 1_500_000.0      # half of gross, not the actual profit
    assert e["presumptive_share_pct"] == 50


def test_presumptive_is_refused_above_the_receipt_limit():
    summary = {"gross_receipts_cents": tax.PRESUMPTIVE["receipt_limit"] + 1, "net_profit_cents": 0}
    e = tax.estimate(summary, "IN_44ADA", "2025-26")
    assert e["eligible"] is False and "above the" in e["eligibility_note"]
    assert not any(r["regime"] == "IN_44ADA" for r in tax.compare_regimes(summary, "2025-26"))


def test_no_regime_returns_no_estimate():
    e = tax.estimate({"gross_receipts_cents": 100, "net_profit_cents": 100}, "none", "2025-26")
    assert e["applicable"] is False


def test_every_estimate_carries_the_disclaimer():
    e = tax.estimate({"gross_receipts_cents": 5_000_000_00, "net_profit_cents": 5_000_000_00},
                     "IN_new", "2025-26")
    assert "isn't tax advice" in e["disclaimer"]


# ----------------------------------------------------------------------------- advance tax

def test_advance_tax_is_not_required_for_small_liabilities():
    s = tax.advance_tax_schedule(5_000_00, "2025-26", date(2025, 12, 1))
    assert s["required"] is False and not s["instalments"]


def test_advance_tax_instalments_accumulate_to_the_full_liability():
    s = tax.advance_tax_schedule(100_000_00, "2025-26", date(2025, 10, 1))
    assert s["required"] is True and len(s["instalments"]) == 4
    assert s["instalments"][-1]["cumulative_cents"] == 100_000_00
    assert [i["share_pct"] for i in s["instalments"]] == [15, 45, 75, 100]
    # 15 Jun and 15 Sep have passed by 1 Oct; 15 Dec and 15 Mar have not
    assert [i["status"] for i in s["instalments"]] == ["overdue", "overdue", "upcoming", "upcoming"]
    assert s["shortfall"] == 45_000.0


def test_payments_reduce_the_shortfall():
    s = tax.advance_tax_schedule(100_000_00, "2025-26", date(2025, 10, 1), paid_cents=45_000_00)
    assert s["shortfall"] == 0
    assert s["instalments"][0]["status"] == "paid"


# ----------------------------------------------------------------------------- tagging

def test_tagging_moves_spend_into_the_business_summary(client):
    before = client.get("/api/tax").json()["summary"]
    assert before["total_expenses"] == 0
    tag_some(client)
    after = client.get("/api/tax").json()["summary"]
    assert after["total_expenses"] > 0 and after["buckets"]


def test_mixed_use_share_only_counts_its_business_portion(client):
    s = client.get("/api/tax").json()["suggestions"][0]
    client.put(f"/api/tax/tags/{s['transaction_id']}",
               json={"kind": "business", "deduction": "phone_internet", "share_pct": 50})
    summary = client.get("/api/tax").json()["summary"]
    assert summary["total_expenses"] == pytest.approx(s["amount"] / 2, abs=0.01)


def test_personal_tags_are_excluded(client):
    s = client.get("/api/tax").json()["suggestions"][0]
    client.put(f"/api/tax/tags/{s['transaction_id']}", json={"kind": "personal"})
    assert client.get("/api/tax").json()["summary"]["total_expenses"] == 0


def test_suggestions_are_proposals_not_tags(client):
    """Nothing is classified until a person confirms it."""
    suggestions = client.get("/api/tax").json()["suggestions"]
    assert suggestions
    assert tax.stats(client.user_id)["tagged"] == 0


def test_confirmed_suggestions_stop_being_suggested(client):
    first = tag_some(client, 3)
    still = {s["transaction_id"] for s in client.get("/api/tax").json()["suggestions"]}
    assert not still & {s["transaction_id"] for s in first}


def test_untag_removes_the_classification(client):
    s = tag_some(client, 1)[0]
    assert client.delete(f"/api/tax/tags/{s['transaction_id']}").json()["removed"] == 1
    assert client.get("/api/tax").json()["summary"]["total_expenses"] == 0


def test_cannot_tag_someone_elses_transaction(client):
    with get_conn() as conn:
        other = conn.execute("SELECT id FROM transactions WHERE user_id != ? LIMIT 1",
                             (client.user_id,)).fetchone()
    assert client.put(f"/api/tax/tags/{other['id']}", json={"kind": "business"}).status_code == 422


@pytest.mark.parametrize("body", [
    {"kind": "maybe"},
    {"kind": "business", "deduction": "not-a-bucket"},
    {"kind": "business", "share_pct": 0},
    {"kind": "business", "share_pct": 101},
])
def test_bad_tag_input_is_rejected(client, body):
    txn = client.get("/api/tax/transactions?limit=1").json()["transactions"][0]
    assert client.put(f"/api/tax/tags/{txn['id']}", json=body).status_code == 422


def test_rules_need_a_consistent_history(client):
    """One example isn't a pattern, and a merchant tagged both ways has no safe default."""
    txns = client.get("/api/tax/transactions?limit=500").json()["transactions"]
    swiggy = [t for t in txns if t["merchant"] == "Swiggy"][:3]
    assert len(swiggy) >= 3

    client.put(f"/api/tax/tags/{swiggy[0]['id']}", json={"kind": "business", "deduction": "meals"})
    assert tax.rules_from_tags(client.user_id) == {}, "a single example must not become a rule"

    client.put(f"/api/tax/tags/{swiggy[1]['id']}", json={"kind": "personal"})
    assert "Swiggy" not in tax.rules_from_tags(client.user_id), "inconsistent tags have no default"


def test_apply_rules_tags_consistent_merchants(client):
    """Rules apply to the whole ledger, not just the selected year — the merchant is the merchant."""
    all_swiggy = [t for t in client.get("/api/transactions?q=swiggy&limit=500").json()["transactions"]
                  if t["merchant"] == "Swiggy"]
    assert len(all_swiggy) > 2
    for t in all_swiggy[:2]:
        client.put(f"/api/tax/tags/{t['id']}", json={"kind": "business", "deduction": "meals", "share_pct": 50})
    assert client.post("/api/tax/tags/apply-rules").json()["tagged"] == len(all_swiggy) - 2


# ----------------------------------------------------------------------------- export & gating

def test_export_lists_business_rows_with_deductible_amounts(client):
    tag_some(client, 5)
    res = client.get("/api/tax/export.csv?fy=2025-26")
    assert res.status_code == 200 and "text/csv" in res.headers["content-type"]
    lines = [line for line in res.text.splitlines() if line.strip()]
    assert any("Gross Receipts" in line for line in lines)
    assert any(col in lines[-1] for col in ("business",))
    assert "deductible_amount" in "\n".join(lines)


def test_freelancer_tools_need_pro(client):
    with get_conn() as conn:
        conn.execute("UPDATE users SET plan = 'free' WHERE id = ?", (client.user_id,))
    assert client.get("/api/tax").status_code == 402
    assert client.get("/api/receipts").status_code == 402


# ----------------------------------------------------------------------------- receipts

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000a"
                    "49444154789c63000100000500010d0a2db40000000049454e44ae426082")


def test_receipt_upload_stores_and_lists(client):
    res = client.post("/api/receipts", files={"file": ("bill.png", PNG, "image/png")})
    assert res.status_code == 201
    listed = client.get("/api/receipts").json()
    assert listed["items"][0]["filename"] == "bill.png"


def test_unsupported_and_oversized_files_are_refused(client):
    assert client.post("/api/receipts", files={"file": ("x.exe", b"MZ", "application/x-msdownload")}
                       ).status_code == 422
    assert client.post("/api/receipts", files={"file": ("big.png", b"x" * (receipts.MAX_BYTES + 1), "image/png")}
                       ).status_code == 422
    assert client.post("/api/receipts", files={"file": ("empty.png", b"", "image/png")}).status_code == 422


def test_identical_uploads_share_one_stored_file(client, tmp_path):
    a = client.post("/api/receipts", files={"file": ("one.png", PNG, "image/png")}).json()
    b = client.post("/api/receipts", files={"file": ("two.png", PNG, "image/png")}).json()
    assert a["id"] != b["id"]
    stored = list((tmp_path / "receipts" / str(client.user_id)).iterdir())
    assert len(stored) == 1, "same bytes must not be written twice"


def test_deleting_one_copy_keeps_the_shared_file(client, tmp_path):
    a = client.post("/api/receipts", files={"file": ("one.png", PNG, "image/png")}).json()
    client.post("/api/receipts", files={"file": ("two.png", PNG, "image/png")})
    assert client.delete(f"/api/receipts/{a['id']}").status_code == 200
    assert list((tmp_path / "receipts" / str(client.user_id)).iterdir()), "the other row still needs it"


def test_receipt_can_be_attached_and_downloaded(client):
    txn = client.get("/api/tax/transactions?limit=1").json()["transactions"][0]
    r = client.post("/api/receipts", files={"file": ("bill.png", PNG, "image/png")},
                    data={"transaction_id": str(txn["id"])}).json()
    assert client.get(f"/api/receipts/{r['id']}/file").status_code == 200
    assert client.get("/api/receipts").json()["items"][0]["transaction_id"] == txn["id"]


def test_cannot_attach_to_someone_elses_transaction(client):
    r = client.post("/api/receipts", files={"file": ("bill.png", PNG, "image/png")}).json()
    with get_conn() as conn:
        other = conn.execute("SELECT id FROM transactions WHERE user_id != ? LIMIT 1",
                             (client.user_id,)).fetchone()
    assert client.put(f"/api/receipts/{r['id']}/attach",
                      json={"transaction_id": other["id"]}).status_code == 422


def test_another_users_receipt_is_not_downloadable(client, api_second=None):
    r = client.post("/api/receipts", files={"file": ("bill.png", PNG, "image/png")}).json()
    from app.main import app
    other = TestClient(app)
    other_email = f"tax-{uuid.uuid4().hex[:8]}@example.com"
    other.post("/api/auth/signup", json={"name": "Other", "email": other_email, "password": PW,
                                         "sample_data": False, "currency": "INR"})
    with get_conn() as conn:
        conn.execute("UPDATE users SET plan = 'pro' WHERE email = ?", (other_email,))
    assert other.get(f"/api/receipts/{r['id']}/file").status_code == 422


# ----------------------------------------------------------------------------- receipt parsing

def test_parse_total_prefers_the_labelled_total():
    text = "Cafe Ludo\nLatte 250.00\nSandwich 400.00\nTotal: 650.00"
    assert receipts.parse_total(text) == 650_00


def test_parse_total_falls_back_to_the_largest_currency_amount():
    assert receipts.parse_total("Paid ₹1,234.50 by UPI") == 1_234_50


def test_parse_handles_indian_grouping():
    assert receipts.parse_total("Total ₹1,23,456.78") == 1_23_456_78


@pytest.mark.parametrize("text, expected", [
    ("Invoice date 2026-03-14", date(2026, 3, 14)),
    ("Date: 14/03/2026", date(2026, 3, 14)),
    ("14 March 2026", date(2026, 3, 14)),
    ("March 14, 2026", date(2026, 3, 14)),
])
def test_parse_date_reads_common_formats(text, expected):
    assert receipts.parse_date(text) == expected


def test_parse_date_ignores_impossible_dates():
    assert receipts.parse_date("Warranty until 31/12/2099") is None
    assert receipts.parse_date("no date here") is None


def test_unparseable_file_is_still_stored(client):
    """A receipt that can't be read is still proof; it just gets no suggested match."""
    r = client.post("/api/receipts", files={"file": ("blurry.png", PNG, "image/png")}).json()
    assert r["parsed"] is False
    assert client.get(f"/api/receipts/{r['id']}/file").status_code == 200


# ----------------------------------------------------------------------------- receipt matching

def _receipt_for(client, txn, *, amount: float | None = None, day_shift: int = 0) -> int:
    """Store a receipt whose parsed fields point at `txn` (parsing itself is exercised above)."""
    r = client.post("/api/receipts", files={"file": ("bill.txt", b"receipt", "text/plain")}).json()
    when = date.fromisoformat(txn["date"]) + timedelta(days=day_shift)
    with get_conn() as conn:
        conn.execute("UPDATE receipts SET parsed_total_cents = ?, parsed_date = ? WHERE id = ?",
                     (round((amount if amount is not None else abs(txn["amount"])) * 100),
                      when.isoformat(), r["id"]))
    return r["id"]


def _spend_txn(client):
    return next(t for t in client.get("/api/tax/transactions?limit=200").json()["transactions"]
                if t["amount"] < -500)


def test_exact_amount_and_date_is_the_top_match(client):
    txn = _spend_txn(client)
    matches = client.get(f"/api/receipts/{_receipt_for(client, txn)}/matches").json()
    assert matches[0]["transaction_id"] == txn["id"] and matches[0]["confidence"] == "exact"


def test_a_near_amount_is_offered_as_possible(client):
    txn = _spend_txn(client)
    near = abs(txn["amount"]) * 1.02
    matches = client.get(f"/api/receipts/{_receipt_for(client, txn, amount=near)}/matches").json()
    assert any(m["transaction_id"] == txn["id"] and m["confidence"] == "possible" for m in matches)


def test_matches_outside_the_date_window_are_not_offered(client):
    """Another charge of a similar size may still match; the far-away one must not."""
    txn = _spend_txn(client)
    receipt_id = _receipt_for(client, txn, day_shift=receipts.MATCH_WINDOW_DAYS + 10)
    matches = client.get(f"/api/receipts/{receipt_id}/matches").json()
    assert txn["id"] not in {m["transaction_id"] for m in matches}


def test_auto_attach_only_takes_unambiguous_matches(client):
    txn = _spend_txn(client)
    _receipt_for(client, txn)
    attached = client.post("/api/receipts/auto-attach").json()["attached"]
    assert attached >= 1
    assert client.get("/api/receipts").json()["items"][0]["transaction_id"] is not None


def test_auto_attach_skips_duplicate_amount_days(client):
    """The India demo bills DMart twice for the same amount on the same day — exactly the case
    where guessing would put the receipt on the wrong charge."""
    # searched across the whole ledger: the planted duplicate is recent, not in the selected FY
    charges = [t for t in client.get("/api/transactions?q=dmart&limit=500").json()["transactions"]
               if t["merchant"] == "DMart"]
    pair = [t for t in charges if sum(1 for o in charges
                                      if o["amount"] == t["amount"] and o["date"] == t["date"]) > 1]
    assert pair, "the demo should contain a duplicated charge"
    _receipt_for(client, pair[0])
    assert client.post("/api/receipts/auto-attach").json()["attached"] == 0


def test_coverage_reports_receipted_business_spend(client):
    tagged = tag_some(client, 3)
    txn = client.get("/api/tax/transactions?limit=500").json()["transactions"]
    target = next(t for t in txn if t["id"] == tagged[0]["transaction_id"])
    client.post("/api/receipts", files={"file": ("b.png", PNG, "image/png")},
                data={"transaction_id": str(target["id"])})
    cov = client.get("/api/tax").json()["receipts"]
    assert cov["business_transactions"] == 3 and cov["with_receipt"] == 1
