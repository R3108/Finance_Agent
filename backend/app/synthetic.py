"""Seeded synthetic transaction generator.

Produces ~24 months of realistic, messy bank data for one persona, including
deliberate edge cases the analytics must catch:
  * Netflix and NYTimes price increases        * duplicate Spotify billing on two accounts
  * 4 overlapping streaming services           * a brand-new ChatGPT subscription
  * a cancelled Headspace subscription          * Amazon Prime annual renewal due soon
  * a one-off $1,299 Best Buy purchase          * a duplicated grocery charge
  * an unknown merchant charge                  * dining spend creeping up recently
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from .db import get_conn
from .ingest import RawTxn, insert_transactions

CHECKING, CARD, SAVINGS = 1, 2, 3


def _cents(dollars: float) -> int:
    return int(round(dollars * 100))


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
    idx = end.year * 12 + end.month - 1 - (months - 1)
    start = date(idx // 12, idx % 12 + 1, 1)
    txns: list[RawTxn] = []

    def add(d: date, desc: str, dollars: float, acct: int = CARD, transfer: bool = False):
        if start <= d <= end:
            txns.append(RawTxn(d.isoformat(), desc, _cents(dollars), acct, transfer))

    days_before = lambda n: end - timedelta(days=n)  # noqa: E731

    # ---- income: biweekly payroll with a raise ~8 months ago
    payday = start + timedelta(days=(4 - start.weekday()) % 7)  # first Friday
    while payday <= end:
        amount = 2575.00 if payday >= days_before(240) else 2450.00
        add(payday, "ACME CORP PAYROLL DIRECT DEP", amount, CHECKING)
        payday += timedelta(days=14)
    for n in (400, 250, 95):
        add(days_before(n), "UPWORK ESCROW STRIPE TRANSFER", rng.choice([450, 780, 1200]), CHECKING)

    for ms in _month_starts(start, end):
        months_ago = (end.year - ms.year) * 12 + end.month - ms.month
        winter = ms.month in (12, 1, 2)
        summer = ms.month in (6, 7, 8)

        # ---- bills
        add(_on_day(ms, 1), "AVALON APARTMENTS RENT ACH", -1650.00, CHECKING)
        add(_on_day(ms, 5), "CON ED OF NY ELECTRIC", -round(rng.uniform(95, 140) if (winter or summer) else rng.uniform(58, 85), 2), CHECKING)
        add(_on_day(ms, 12), "COMCAST XFINITY INTERNET", -69.99)
        add(_on_day(ms, 18), "T-MOBILE AUTOPAY", -55.00)
        add(_on_day(ms, 22), "GEICO AUTO INSURANCE", -128.40)
        add(_on_day(ms, 2), "ONLINE TRANSFER TO SAVINGS", -500.00, CHECKING, True)
        add(_on_day(ms, 2), "ONLINE TRANSFER FROM CHECKING", 500.00, SAVINGS, True)
        add(_on_day(ms, 28), "INTEREST PAYMENT", round(22 + (24 - months_ago) * 1.6, 2), SAVINGS)

        # ---- subscriptions
        add(_on_day(ms, 15), "NETFLIX.COM 866-579-7172 CA", -(17.99 if months_ago <= 1 else 15.49))
        add(_on_day(ms, 3), "SPOTIFY USA 877-778-1161", -11.99)
        if months_ago <= 3:
            add(_on_day(ms, 7), "SPOTIFY P1A2B3C4 STOCKHOLM", -11.99, CHECKING)  # duplicate billing
        add(_on_day(ms, 8), "DISNEYPLUS 888-905-7888", -13.99)
        add(_on_day(ms, 20), "HULU 877-8244858 HULU.COM", -17.99)
        if months_ago <= 9:
            add(_on_day(ms, 25), "HBO MAX HELPMAX.COM", -16.99)
        add(_on_day(ms, 9), "APPLE.COM/BILL 866-712-7753", -2.99)
        add(_on_day(ms, 27), "ADOBE *CREATIVE CLOUD", -59.99)
        add(_on_day(ms, 1), "PLANET FITNESS CLUB FEES", -24.99)
        if months_ago <= 2:
            add(_on_day(ms, 11), "OPENAI *CHATGPT SUBSCR", -20.00)
        if 4 <= months_ago <= 14:
            add(_on_day(ms, 19), "HEADSPACE.COM SUBSCRIPTION", -12.99)

        # ---- variable spending
        dining_boost = 1.35 if months_ago <= 2 else 1.0
        cursor = ms
        nxt = _on_day(ms, 28) + timedelta(days=4)
        month_end = date(nxt.year, nxt.month, 1) - timedelta(days=1)
        while cursor <= month_end:
            wd = cursor.weekday()
            if rng.random() < 0.17:
                store = rng.choice(["WHOLE FOODS MKT #10234", "TRADER JOE'S #552", "KROGER #0187", "COSTCO WHSE #1102"])
                add(cursor, store, -round(rng.uniform(45, 185), 2))
            if wd < 5 and rng.random() < 0.45 * dining_boost:
                add(cursor, rng.choice(["STARBUCKS STORE 08812", "SQ *BLUE BOTTLE COFFEE"]), -round(rng.uniform(4.5, 7.8), 2))
            if rng.random() < 0.2 * dining_boost:
                add(cursor, rng.choice(["TST* CHIPOTLE 2231", "SWEETGREEN BLEECKER", "JOE'S PIZZA BROADWAY", "NAKAMURA SUSHI BAR"]), -round(rng.uniform(14, 78), 2))
            if wd >= 4 and rng.random() < 0.3 * dining_boost:
                add(cursor, rng.choice(["DOORDASH*THAI VILLA", "UBER EATS PENDING", "GRUBHUB*HALAL GUYS"]), -round(rng.uniform(24, 58), 2))
            if rng.random() < 0.1:
                add(cursor, "UBER *TRIP HELP.UBER.COM", -round(rng.uniform(11, 42), 2))
            if rng.random() < 0.09:
                add(cursor, rng.choice(["SHELL OIL 57442", "CHEVRON 0091"]), -round(rng.uniform(34, 62), 2))
            if rng.random() < 0.13:
                add(cursor, f"AMZN Mktp US*{rng.randint(1000, 9999)}K{rng.randint(10, 99)}", -round(rng.uniform(9, 125), 2))
            if rng.random() < 0.06:
                add(cursor, "TARGET 00012345", -round(rng.uniform(18, 115), 2))
            cursor += timedelta(days=1)

        add(_on_day(ms, rng.randint(3, 26)), "CVS/PHARMACY #4471", -round(rng.uniform(9, 48), 2))
        add(_on_day(ms, rng.randint(3, 26)), "FADE MASTERS BARBER", -38.00)
        if rng.random() < 0.7:
            add(_on_day(ms, rng.randint(3, 26)), "AMC THEATRES 34TH ST", -round(rng.uniform(18, 42), 2))
        if rng.random() < 0.25:
            add(_on_day(ms, rng.randint(3, 26)), "UNIQLO USA 5TH AVE", -round(rng.uniform(40, 160), 2))
        if ms.month == 12:
            for _ in range(4):
                add(_on_day(ms, rng.randint(5, 20)), f"AMZN Mktp US*GIFT{rng.randint(100, 999)}", -round(rng.uniform(40, 150), 2))
            add(_on_day(ms, 28), "UNICEF USA DONATION", -100.00)
        if ms.month in (3, 9):
            add(_on_day(ms, 14), "BRIGHT SMILE DENTAL", -185.00)

    # ---- annual & one-off events
    for n in (705, 340):
        add(days_before(n), "AMAZON PRIME MEMBERSHIP AMZN.COM/PRME", -139.00)
    for n in (565, 200):
        add(days_before(n), "DROPBOX*PLUS ANNUAL", -119.88)
    # NYTimes bills every 4 weeks; the $4 promo expired a few cycles ago
    d = days_before(26 * 28)
    while d <= end:
        add(d, "NYTIMES DIGITAL SUBSCR", -(4.00 if d < days_before(80) else 25.00))
        d += timedelta(days=28)
    trip = days_before(130)
    add(trip - timedelta(days=21), "DELTA AIR LINES 0062345", -438.60)
    add(trip, "MARRIOTT LISBON", -712.35)
    add(trip + timedelta(days=1), "FOREIGN TRANSACTION FEE", -21.37)
    add(trip + timedelta(days=2), "RESTAURANTE TASCA BISTRO", -86.10)
    add(days_before(410), "COURSERA INC", -49.00)
    add(days_before(40), "BEST BUY 00001234", -1299.99)
    add(days_before(12), "WHOLE FOODS MKT #10234", -142.37)
    add(days_before(12), "WHOLE FOODS MKT #10234", -142.37)   # duplicated charge
    add(days_before(6), "ZXQ DIGITAL SVCS 8839", -89.00)       # unknown merchant

    # ---- pay last month's card spend from checking on the 25th
    for ms in _month_starts(start, end):
        prev_end = ms - timedelta(days=1)
        prev_start = date(prev_end.year, prev_end.month, 1)
        owed = -sum(t.amount_cents for t in txns if t.account_id == CARD
                    and prev_start.isoformat() <= t.date <= prev_end.isoformat() and t.amount_cents < 0)
        if owed > 0:
            pay = _on_day(ms, 25)
            add(pay, "SAPPHIRE CARD AUTOPAY PAYMENT", -owed / 100, CHECKING, True)
            add(pay, "AUTOPAY PAYMENT THANK YOU", owed / 100, CARD, True)

    txns.sort(key=lambda t: t.date)
    return txns


USER_DATA_TABLES = ("transactions", "accounts", "category_rules", "budgets", "goals", "subscription_actions",
                    "chat_messages", "debts", "assets", "challenges")

# --- the US persona's starting balance sheet, in the same shape as `synthetic_in` -----------------
ACCOUNTS = [(CHECKING, "Everyday Checking", "checking", "Chase", 420000),
            (CARD, "Sapphire Card", "credit", "Chase", 0),
            (SAVINGS, "High-Yield Savings", "savings", "Ally", 800000)]

GOALS = [("Emergency fund top-up", 1500000, 0, 365), ("Japan trip", 450000, 120000, 240)]

BUDGETS = [("Dining", 45000), ("Shopping", 40000), ("Groceries", 65000)]

DEBTS = [("Store credit card", "credit_card", 184000, 2699, 5500),
         ("Furniture 0% plan", "personal", 96000, 0, 8000),
         ("Car loan", "auto_loan", 1126000, 689, 29500),
         ("Student loan", "student_loan", 2240000, 505, 23800)]

ASSETS = [("401(k)", "retirement", 3860000), ("Brokerage account", "investment", 1245000),
          ("2019 Honda Civic", "vehicle", 1450000)]

CHALLENGE = ("no_spend", "8 no-spend days this month", '{"target_days": 8}')


def dataset_for(currency: str | None):
    """The persona module matching a currency: rupee accounts get the India dataset, everyone else the US one.

    Both modules expose the same names (`generate`, `ACCOUNTS`, `GOALS`, …), so `seed_demo` below is
    dataset-agnostic and a third persona only needs a new module.
    """
    from . import money as cur
    if cur.get(currency).region == "IN":
        from . import synthetic_in
        return synthetic_in
    import sys
    return sys.modules[__name__]


def create_demo_user(currency: str | None = None) -> int:
    """A fresh, private demo sandbox (Pro plan, no password). Purged after DEMO_RETENTION_DAYS."""
    from . import money as cur
    code = cur.normalize(currency)
    name = "Aarav Demo" if cur.get(code).region == "IN" else "Alex Demo"
    with get_conn() as conn:
        user_id = conn.execute("INSERT INTO users (name, plan, is_demo, currency) VALUES (?, 'pro', 1, ?)",
                               (name, code)).lastrowid
    seed_demo(user_id, currency=code)
    return user_id


def seed_demo(user_id: int = 1, end: date | None = None, months: int = 24, seed: int = 42,
              currency: str | None = None) -> dict:
    """Replace a user's data with the synthetic demo dataset. The user row, login and plan are kept."""
    from . import money as cur
    end = end or date.today()
    with get_conn() as conn:
        row = conn.execute("SELECT currency FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO users (id, name, plan, is_demo, currency) VALUES (?, 'Alex Demo', 'pro', 1, ?)",
                         (user_id, cur.normalize(currency)))
    # an explicit argument wins, otherwise seed in whatever currency the account already displays
    code = cur.normalize(currency or (row["currency"] if row else None))
    ds = dataset_for(code)

    with get_conn() as conn:
        for table in USER_DATA_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        ids = {}
        for key, name, type_, inst, opening in ds.ACCOUNTS:
            ids[key] = conn.execute(
                "INSERT INTO accounts (user_id, name, type, institution, opening_balance_cents) VALUES (?,?,?,?,?)",
                (user_id, name, type_, inst, opening)).lastrowid
        conn.executemany(
            "INSERT INTO goals (user_id, name, target_cents, saved_cents, target_date) VALUES (?,?,?,?,?)",
            [(user_id, name, target, saved, (end + timedelta(days=days)).isoformat())
             for name, target, saved, days in ds.GOALS],
        )
    rows = ds.generate(end, months, seed)
    for r in rows:
        r.account_id = ids[r.account_id]
    result = insert_transactions(user_id, rows)
    with get_conn() as conn:  # starter budgets, debts, assets and a running challenge so no page is empty
        conn.executemany(
            "INSERT OR REPLACE INTO budgets (user_id, category, monthly_limit_cents) VALUES (?,?,?)",
            [(user_id, cat, limit) for cat, limit in ds.BUDGETS],
        )
        conn.executemany(
            "INSERT INTO debts (user_id, name, kind, balance_cents, apr_bps, min_payment_cents) VALUES (?,?,?,?,?,?)",
            [(user_id, *d) for d in ds.DEBTS],
        )
        conn.executemany(
            "INSERT INTO assets (user_id, name, kind, value_cents) VALUES (?,?,?,?)",
            [(user_id, *a) for a in ds.ASSETS],
        )
        kind, title, params = ds.CHALLENGE
        conn.execute(
            "INSERT INTO challenges (user_id, type, title, params_json, start_date, end_date) VALUES (?,?,?,?,?,?)",
            (user_id, kind, title, params,
             (end - timedelta(days=11)).isoformat(), (end + timedelta(days=18)).isoformat()),
        )
    return result
