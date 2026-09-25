"""Seeded synthetic transaction generator — India persona (INR).

The sibling of `synthetic.generate`, for a salaried Bengaluru professional earning ~₹1.4L/month
net. Descriptors are written the way Indian statements actually read (UPI strings, NACH mandates,
IMPS/NEFT), so the UPI parser and the India merchant pack in `categorizer` are exercised by the
demo rather than only by tests.

It plants the same shapes of edge case the US dataset does, so every analytic has something to
find and the test suite can assert on either dataset:
  * a Netflix price rise                      * Spotify billed on two accounts
  * 4 stacked video subscriptions             * a brand-new ChatGPT subscription
  * a cancelled cult.fit membership           * an Amazon Prime annual renewal due soon
  * a ₹86,000 one-off purchase                * a duplicated grocery charge
  * an unrecognised merchant                  * food delivery creeping up recently
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from .ingest import RawTxn

CHECKING, CARD, SAVINGS = 1, 2, 3


def _paise(rupees: float) -> int:
    return int(round(rupees * 100))


def _month_starts(start: date, end: date):
    d = date(start.year, start.month, 1)
    while d <= end:
        yield d
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def _on_day(month_start: date, day: int) -> date:
    nxt = date(month_start.year + (month_start.month == 12), month_start.month % 12 + 1, 1)
    last = (nxt - timedelta(days=1)).day
    return month_start.replace(day=min(day, last))


def generate(end: date, months: int = 24, seed: int = 42) -> list[RawTxn]:
    rng = random.Random(seed)

    def _upi(payee: str, handle: str = "ybl", mode: str = "Payment") -> str:
        """A UPI descriptor in the shape most Indian banks emit."""
        ref = rng.randint(10**11, 10**12 - 1)
        return f"UPI/DR/{ref}/{payee}/{handle.upper()[:4]}/{payee.lower().replace(' ', '')}@{handle}/{mode}"

    idx = end.year * 12 + end.month - 1 - (months - 1)
    start = date(idx // 12, idx % 12 + 1, 1)
    txns: list[RawTxn] = []

    def add(d: date, desc: str, rupees: float, acct: int = CARD, transfer: bool = False):
        if start <= d <= end:
            txns.append(RawTxn(d.isoformat(), desc, _paise(rupees), acct, transfer))

    days_before = lambda n: end - timedelta(days=n)  # noqa: E731

    # ---- income: monthly salary on the last working day, with a hike ~8 months ago
    for ms in _month_starts(start, end):
        pay_day = _on_day(ms, 28)
        while pay_day.weekday() >= 5:      # salary lands on the previous working day
            pay_day -= timedelta(days=1)
        gross = 142000.00 if pay_day >= days_before(240) else 126500.00
        add(pay_day, "NEFT-AXISP00456123-ACME SOFTWARE PVT LTD-SALARY CREDIT", gross, CHECKING)
    for n in (400, 250, 95):
        add(days_before(n), "IMPS/P2A/CONSULTING RETAINER/PAYOUT", rng.choice([25000, 40000, 65000]), CHECKING)

    for ms in _month_starts(start, end):
        months_ago = (end.year - ms.year) * 12 + end.month - ms.month
        summer = ms.month in (3, 4, 5)     # AC season: the electricity bill roughly doubles

        # ---- bills (mostly NACH mandates and UPI autopay)
        add(_on_day(ms, 3), "NACH DR PRESTIGE SOCIETY FLAT RENT", -38000.00, CHECKING)
        add(_on_day(ms, 6), "BESCOM ELECTRICITY BILL BBPS", -round(rng.uniform(3200, 4800) if summer else rng.uniform(1400, 2200), 2), CHECKING)
        add(_on_day(ms, 8), "ACT FIBERNET BROADBAND AUTOPAY", -1399.00)
        add(_on_day(ms, 11), "AIRTEL POSTPAID BILL BBPS", -799.00)
        add(_on_day(ms, 14), "NACH DR STAR HEALTH INSURANCE PREM", -2450.00, CHECKING)
        add(_on_day(ms, 4), "MAHANAGAR GAS PIPED GAS BBPS", -round(rng.uniform(420, 780), 2), CHECKING)
        add(_on_day(ms, 2), "IMPS/SELF TRANSFER/HDFC SAVINGS", -25000.00, CHECKING, True)
        add(_on_day(ms, 2), "IMPS/SELF TRANSFER/FROM CHECKING", 25000.00, SAVINGS, True)
        add(_on_day(ms, 5), "NACH DR ZERODHA COIN SIP MUTUAL FUND", -15000.00, CHECKING, True)
        add(_on_day(ms, 30), "INTEREST CREDIT SAVINGS ACCOUNT", round(760 + (24 - months_ago) * 42, 2), SAVINGS)

        # ---- subscriptions
        add(_on_day(ms, 15), "NETFLIX.COM MUMBAI IN", -(649.00 if months_ago <= 1 else 499.00))
        add(_on_day(ms, 3), "SPOTIFY INDIA SUBSCRIPTION", -119.00)
        if months_ago <= 3:
            add(_on_day(ms, 7), _upi("SPOTIFY", "icici"), -119.00, CHECKING)   # duplicate billing
        add(_on_day(ms, 9), "DISNEY+ HOTSTAR SUBSCRIPTION", -299.00)
        add(_on_day(ms, 20), "SONYLIV PREMIUM SUBSCRIPTION", -299.00)
        if months_ago <= 9:
            add(_on_day(ms, 25), "ZEE5 PREMIUM ANNUAL EMI", -199.00)
        add(_on_day(ms, 10), "GOOGLE ONE STORAGE IN", -130.00)
        add(_on_day(ms, 27), "ADOBE SYSTEMS SOFTWARE IN", -1691.00)
        if months_ago <= 2:
            add(_on_day(ms, 11), "OPENAI *CHATGPT SUBSCR", -1950.00)
        if 4 <= months_ago <= 14:
            add(_on_day(ms, 19), "CULTFIT MEMBERSHIP BENGALURU", -1499.00)

        # ---- variable spending
        food_boost = 1.35 if months_ago <= 2 else 1.0
        cursor = ms
        nxt = _on_day(ms, 28) + timedelta(days=4)
        month_end = date(nxt.year, nxt.month, 1) - timedelta(days=1)
        while cursor <= month_end:
            wd = cursor.weekday()
            if rng.random() < 0.18:
                store = rng.choice(["DMART AVENUE SUPERMART BLR", _upi("BIGBASKET", "icici"),
                                    _upi("ZEPTO", "axis"), _upi("BLINKIT", "icici")])
                add(cursor, store, -round(rng.uniform(380, 2400), 2))
            if wd < 5 and rng.random() < 0.45 * food_boost:
                add(cursor, rng.choice([_upi("THIRD WAVE COFFEE"), _upi("CHAAYOS", "paytm"), "BLUE TOKAI COFFEE BLR"]),
                    -round(rng.uniform(140, 420), 2))
            if rng.random() < 0.22 * food_boost:
                add(cursor, rng.choice([_upi("SWIGGY"), _upi("ZOMATO", "hdfcbank")]), -round(rng.uniform(220, 950), 2))
            if wd >= 4 and rng.random() < 0.28 * food_boost:
                add(cursor, rng.choice(["DOMINOS PIZZA BENGALURU", _upi("TRUFFLES CAFE"), "BIKANERVALA JAYANAGAR"]),
                    -round(rng.uniform(450, 1800), 2))
            if rng.random() < 0.14:
                add(cursor, rng.choice([_upi("OLA", "okaxis"), _upi("RAPIDO", "ybl"), "UBER INDIA SYSTEMS"]),
                    -round(rng.uniform(80, 520), 2))
            if rng.random() < 0.07:
                add(cursor, rng.choice(["INDIAN OIL PETROL PUMP", "BPCL FUEL STATION BLR"]), -round(rng.uniform(1200, 3500), 2))
            if rng.random() < 0.12:
                add(cursor, rng.choice([f"FLIPKART INTERNET PVT{rng.randint(1000, 9999)}", "MYNTRA DESIGNS BLR", "AMAZON PAY INDIA"]),
                    -round(rng.uniform(400, 6500), 2))
            if rng.random() < 0.05:
                add(cursor, "NETC FASTAG RECHARGE", -500.00)
            cursor += timedelta(days=1)

        add(_on_day(ms, rng.randint(3, 26)), "APOLLO PHARMACY BENGALURU", -round(rng.uniform(180, 1400), 2))
        add(_on_day(ms, rng.randint(3, 26)), _upi("LOOKS SALON", "paytm"), -650.00)
        if rng.random() < 0.7:
            add(_on_day(ms, rng.randint(3, 26)), "BOOKMYSHOW PVR ORION MALL", -round(rng.uniform(400, 1300), 2))
        if rng.random() < 0.25:
            add(_on_day(ms, rng.randint(3, 26)), "NYKAA FASHION ONLINE", -round(rng.uniform(900, 4200), 2))
        if ms.month == 10:            # Diwali: gifting and a donation
            for _ in range(4):
                add(_on_day(ms, rng.randint(5, 20)), f"AMAZON IN GIFT ORDER {rng.randint(100, 999)}", -round(rng.uniform(1200, 6000), 2))
            add(_on_day(ms, 28), _upi("GOONJ NGO DONATION", "sbi"), -5100.00)
        if ms.month in (3, 9):
            add(_on_day(ms, 14), "CLOVE DENTAL CLINIC BLR", -4500.00)

    # ---- annual & one-off events
    for n in (705, 340):
        add(days_before(n), "AMAZON PRIME MEMBERSHIP IN", -1499.00)
    for n in (565, 200):
        add(days_before(n), "GOOGLE WORKSPACE ANNUAL IN", -8832.00)
    # A news subscription billed every 4 weeks; the intro price expired a few cycles ago
    d = days_before(26 * 28)
    while d <= end:
        add(d, "THE KEN DIGITAL SUBSCRIPTION", -(99.00 if d < days_before(80) else 449.00))
        d += timedelta(days=28)
    trip = days_before(130)
    add(trip - timedelta(days=21), "INDIGO AIRLINES BLR-DEL", -8460.00)
    add(trip, "IRCTC TOURISM HOTEL BOOKING", -14250.00)
    add(trip + timedelta(days=1), "GST ON TRAVEL BOOKING", -1283.00)
    add(trip + timedelta(days=2), _upi("BARBEQUE NATION", "hdfcbank"), -3200.00)
    add(days_before(410), "UNACADEMY LEARNING PVT LTD", -4999.00)
    add(days_before(40), "CROMA ELECTRONICS MG ROAD", -86990.00)
    add(days_before(12), "DMART AVENUE SUPERMART BLR", -2847.50)
    add(days_before(12), "DMART AVENUE SUPERMART BLR", -2847.50)        # duplicated charge
    add(days_before(6), "UPI/DR/556677889900/QXZ DIGITAL VENTURES/YESB/qxz9931@ybl/Payment", -2999.00)

    # ---- pay last month's card spend from checking on the 5th
    for ms in _month_starts(start, end):
        prev_end = ms - timedelta(days=1)
        prev_start = date(prev_end.year, prev_end.month, 1)
        owed = -sum(t.amount_cents for t in txns if t.account_id == CARD
                    and prev_start.isoformat() <= t.date <= prev_end.isoformat() and t.amount_cents < 0)
        if owed > 0:
            pay = _on_day(ms, 5)
            add(pay, "CRED CREDIT CARD BILL PAYMENT", -owed / 100, CHECKING, True)
            add(pay, "PAYMENT RECEIVED THANK YOU", owed / 100, CARD, True)

    txns.sort(key=lambda t: t.date)
    return txns


ACCOUNTS = [(CHECKING, "Salary Account", "checking", "HDFC Bank", 18500000),
            (CARD, "Millennia Credit Card", "credit", "HDFC Bank", 0),
            (SAVINGS, "Savings Account", "savings", "ICICI Bank", 42000000)]

GOALS = [("Emergency fund (6 months)", 60000000, 0, 365),
         ("Japan trip", 25000000, 6000000, 240)]

BUDGETS = [("Dining", 1200000), ("Shopping", 1500000), ("Groceries", 1800000)]

DEBTS = [("HDFC credit card EMI", "credit_card", 8600000, 4200, 430000),
         ("Furniture 0% EMI", "personal", 4500000, 0, 375000),
         ("Car loan", "auto_loan", 48000000, 925, 1180000),
         ("Education loan", "student_loan", 92000000, 1050, 1240000)]

ASSETS = [("EPF balance", "retirement", 128000000), ("Mutual funds (SIP)", "investment", 61500000),
          ("PPF account", "retirement", 47000000), ("2021 Maruti Baleno", "vehicle", 62000000)]

CHALLENGE = ("no_spend", "8 no-spend days this month", '{"target_days": 8}')
