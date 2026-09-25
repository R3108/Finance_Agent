"""Currency formatting, the India merchant pack and the rupee demo persona."""
from __future__ import annotations

import pytest

from app import analytics as an
from app import money
from app.categorizer import categorize, parse_upi


# ----------------------------------------------------------------------------- formatting

@pytest.mark.parametrize("code, amount, expected", [
    ("INR", 1234567.891, "₹12,34,567.89"),      # Indian grouping: 3 digits, then pairs
    ("INR", 100000, "₹1,00,000.00"),
    ("INR", 999.5, "₹999.50"),
    ("INR", -1234.5, "-₹1,234.50"),
    ("USD", 1234567.891, "$1,234,567.89"),
    ("USD", -1234.5, "-$1,234.50"),
    ("EUR", 1000, "€1,000.00"),
    ("GBP", 0, "£0.00"),
])
def test_format_money(code, amount, expected):
    assert money.format_money(amount, code) == expected


def test_unknown_currency_falls_back_rather_than_raising():
    assert money.normalize("XYZ") == money.DEFAULT_CODE
    assert money.normalize(None) == money.DEFAULT_CODE
    assert money.format_money(5, "XYZ") == money.format_money(5, money.DEFAULT_CODE)


def test_every_currency_has_two_decimals():
    """`amount_cents` means 1/100 of a unit everywhere; a 0- or 3-decimal currency would break that."""
    assert {c.decimals for c in money.CURRENCIES.values()} == {2}


def test_format_minor_matches_major():
    assert money.format_minor(123456, "INR") == money.format_money(1234.56, "INR")


def test_context_is_restored_after_use_currency():
    money.set_currency("USD")
    with money.use_currency("INR"):
        assert an.fmt_money(1000) == "₹1,000.00"
    assert an.fmt_money(1000) == "$1,000.00"


def test_rounding_step_scales_with_currency():
    """A budget rounded to the nearest $10 must not round to the nearest ₹10."""
    assert money.rounding_step(10, "USD") == 10
    assert money.rounding_step(10, "INR") == 500


def test_detection_thresholds_scale_with_currency():
    """A "$50/month is costly" cut-off must not become "₹50/month is costly"."""
    assert money.scaled_minor(50, "USD") == 5000          # $50
    assert money.scaled_minor(50, "INR") == 250000        # ₹2,500, not ₹50


def test_a_cheap_rupee_subscription_is_not_called_high_cost(user_in):
    """Spotify India at ₹119/month is not a costly subscription; the dollar threshold said it was."""
    money.set_currency("INR")
    costly = {f["merchant"] for f in an.unusual_subscriptions(user_in.tx, user_in.as_of)
              if f["type"] == "high_cost"}
    assert "Spotify" not in costly and "Google One" not in costly


def test_an_expensive_dollar_subscription_is_still_called_high_cost(user):
    """The US behaviour must be unchanged — the scaling factor is 1 for dollars."""
    money.set_currency("USD")
    costly = {f["merchant"] for f in an.unusual_subscriptions(user.tx, user.as_of)
              if f["type"] == "high_cost"}
    assert "Adobe Creative Cloud" in costly      # $59.99/month


# ----------------------------------------------------------------------------- UPI descriptors

@pytest.mark.parametrize("descriptor, merchant, category", [
    ("UPI/DR/412345678901/SWIGGY/YESB/swiggy@ybl/Payment", "Swiggy", "Dining"),
    ("UPI-ZOMATO LTD-ZOMATO@HDFCBANK-HDFC0000001-1234-PAYMENT", "Zomato", "Dining"),
    ("UPI/DR/441122330099/BLINKIT/ICIC/blinkit.rzp@icici/Order", "Blinkit", "Groceries"),
    ("NEFT-AXISP00456123-ACME SOFTWARE PVT LTD-SALARY CREDIT", "Employer Payroll", "Income"),
    ("DMART AVENUE SUPERMART BLR", "DMart", "Groceries"),
    ("NETC FASTAG RECHARGE", "FASTag Toll", "Transport"),
    ("BESCOM ELECTRICITY BILL BBPS", "Electricity Board", "Utilities"),
])
def test_india_descriptors_are_categorised(descriptor, merchant, category):
    got_merchant, got_category, _ = categorize(descriptor)
    assert (got_merchant, got_category) == (merchant, category)


def test_psp_code_is_not_mistaken_for_a_merchant():
    """`PYTM` is the payment provider, not the payee — a person-to-person transfer has no merchant."""
    assert parse_upi("UPI/DR/305512345678/9876543210/PYTM/q123456@paytm/NA") == "q123456"


def test_parse_upi_ignores_non_upi_descriptors():
    assert parse_upi("TST* CHIPOTLE 2231") is None
    assert parse_upi("DMART AVENUE SUPERMART BLR") is None


def test_us_rules_still_win_for_us_descriptors():
    """The India pack is additive: adding it must not change how a US statement is read."""
    assert categorize("TST* CHIPOTLE 2231")[:2] == ("Chipotle", "Dining")
    assert categorize("WHOLE FOODS MKT #10234")[:2] == ("Whole Foods", "Groceries")


# ----------------------------------------------------------------------------- the rupee persona

def test_india_demo_is_seeded_in_rupees(user_in):
    assert user_in.currency == "INR"
    assert len(user_in.tx) > 1000
    assert user_in.tx["merchant"].isin(["Swiggy", "Zomato", "DMart"]).any()


def test_india_demo_plants_the_same_edge_cases(user_in):
    """The analytics must find the rupee dataset's planted problems, not just the dollar one's."""
    flags = an.unusual_subscriptions(user_in.tx, user_in.as_of)
    kinds = {f["type"] for f in flags}
    assert {"price_increase", "duplicate", "overlap"} <= kinds
    increase = next(f for f in flags if f["type"] == "price_increase")
    assert increase["merchant"] == "Netflix"
    duplicate = next(f for f in flags if f["type"] == "duplicate")
    assert duplicate["merchant"] == "Spotify"


def test_narrative_copy_is_written_in_the_users_currency(user_in):
    """Insight text is built deep inside analytics; it must follow the signed-in user's currency."""
    money.set_currency(user_in.currency)
    text = " ".join(i["title"] + i["detail"] for i in
                    an.insights(user_in.tx, user_in.accounts, user_in.budgets, user_in.as_of))
    assert "₹" in text and "$" not in text


def test_budget_suggestions_are_rounded_to_sensible_rupee_steps(user_in):
    money.set_currency(user_in.currency)
    out = an.suggest_budgets(user_in.tx, user_in.budgets, user_in.as_of)
    assert out["suggestions"], "the rupee persona should spend enough to suggest budgets"
    for s in out["suggestions"]:
        # every suggestion lands on a whole multiple of ₹250 (the ₹5 fine step x50) or better
        assert round(s["suggested"] * 100) % 25000 == 0, s
        assert "$" not in s["rationale"]
