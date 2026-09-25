"""Generate a bank CSV you can import to exercise the app, with dates relative to today.

The demo sandbox gives you 24 months of history in one click, but it's fixed: you can't make it
overspend a budget on demand, or add a charge that trips a specific alert. This writes a small CSV
in the same shape a real bank exports, so you can drive a feature and watch it react.

    python tools/sample_csv.py                       # a month of INR spending -> sample.csv
    python tools/sample_csv.py --currency USD        # US merchants instead
    python tools/sample_csv.py --overspend Dining    # heavy on one category, to trip its budget
    python tools/sample_csv.py --days 90 -o big.csv  # a longer window

Import it at Data & settings -> Import bank CSV. Re-importing the same file is a no-op (rows are
de-duplicated by a content hash), so run it again with a new window to add more.
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

#: Everyday spending: (descriptor, low, high, rough monthly frequency). Dates are scattered, which
#: is what stops these being mistaken for subscriptions.
VARIABLE = {
    "INR": [
        ("UPI/DR/{ref}/SWIGGY/YESB/swiggy@ybl/Payment", 220, 950, 8),
        ("UPI/DR/{ref}/ZOMATO/HDFC/zomato@hdfcbank/Payment", 240, 900, 5),
        ("UPI/DR/{ref}/BLINKIT/ICIC/blinkit.rzp@icici/Order", 380, 2400, 6),
        ("DMART AVENUE SUPERMART BLR", 800, 3200, 3),
        ("UPI/DR/{ref}/THIRD WAVE COFFEE/YESB/thirdwave@ybl/Payment", 140, 420, 10),
        ("UPI/DR/{ref}/OLA/OKAX/ola@okaxis/Payment", 80, 520, 6),
        ("INDIAN OIL PETROL PUMP", 1200, 3500, 2),
        ("FLIPKART INTERNET PVT LTD", 400, 6500, 3),
        ("BOOKMYSHOW PVR ORION MALL", 400, 1300, 1),
        ("APOLLO PHARMACY BENGALURU", 180, 1400, 1),
    ],
    "USD": [
        ("TST* CHIPOTLE 2231", 12, 28, 6),
        ("DOORDASH*THAI VILLA", 24, 58, 4),
        ("WHOLE FOODS MKT #10234", 45, 185, 4),
        ("STARBUCKS STORE 08812", 4, 9, 10),
        ("UBER *TRIP HELP.UBER.COM", 11, 42, 5),
        ("SHELL OIL 57442", 34, 62, 2),
        ("AMZN Mktp US*{ref}", 9, 125, 4),
        ("AMC THEATRES 34TH ST", 18, 42, 1),
        ("CVS/PHARMACY #4471", 9, 48, 1),
    ],
}

#: Subscriptions and bills: (descriptor, amount, day of month, +/- wobble).
#:
#: These MUST land on the same day each month at a steady amount, or `analytics.detect_recurring`
#: will not see them: it wants at least three charges from one merchant whose interval medians
#: classify as a cadence and whose amounts are stable within 2% (or 35% for a utility bill). That
#: is also why --days defaults to four months — two charges are never enough for a monthly series.
RECURRING = {
    "INR": [
        ("NETFLIX.COM MUMBAI IN", 649, 15, 0),
        ("SPOTIFY INDIA SUBSCRIPTION", 119, 3, 0),
        ("DISNEY+ HOTSTAR SUBSCRIPTION", 299, 9, 0),
        ("SONYLIV PREMIUM SUBSCRIPTION", 299, 20, 0),
        ("ADOBE SYSTEMS SOFTWARE IN", 1691, 27, 0),
        ("GOOGLE ONE STORAGE IN", 130, 10, 0),
        ("AIRTEL POSTPAID BILL BBPS", 799, 11, 0),
        ("ACT FIBERNET BROADBAND AUTOPAY", 1399, 8, 0),
        ("BESCOM ELECTRICITY BILL BBPS", 2400, 6, 700),   # a utility bill that moves month to month
        ("NACH DR STAR HEALTH INSURANCE PREM", 2450, 14, 0),
    ],
    "USD": [
        ("NETFLIX.COM 866-579-7172 CA", 17.99, 15, 0),
        ("SPOTIFY USA 877-778-1161", 11.99, 3, 0),
        ("DISNEYPLUS 888-905-7888", 13.99, 9, 0),
        ("HULU 877-8244858 HULU.COM", 17.99, 20, 0),
        ("ADOBE *CREATIVE CLOUD", 59.99, 27, 0),
        ("APPLE.COM/BILL 866-712-7753", 2.99, 10, 0),
        ("T-MOBILE AUTOPAY", 55, 11, 0),
        ("COMCAST XFINITY INTERNET", 69.99, 8, 0),
        ("CON ED OF NY ELECTRIC", 95, 6, 30),
        ("GEICO AUTO INSURANCE", 128.40, 14, 0),
    ],
}

#: Charged more in the most recent month, so the "price rise" flag and its alert have something to
#: find. Matched by the leading word of the descriptor.
PRICE_RISE = {"INR": ("NETFLIX", 1.30), "USD": ("NETFLIX", 1.16)}

#: Paid in, so income-side analytics (savings rate, safe-to-spend, the tax tools) have something.
INCOME = {
    "INR": ("NEFT-AXISP00456123-ACME SOFTWARE PVT LTD-SALARY CREDIT", 142000),
    "USD": ("ACME CORP PAYROLL DIRECT DEP", 2575),
}

#: Descriptors whose category the built-in rules map to, for --overspend.
CATEGORY_MERCHANTS = {
    "Dining": {"INR": ["UPI/DR/{ref}/SWIGGY/YESB/swiggy@ybl/Payment",
                       "UPI/DR/{ref}/ZOMATO/HDFC/zomato@hdfcbank/Payment"],
               "USD": ["TST* CHIPOTLE 2231", "DOORDASH*THAI VILLA"]},
    "Groceries": {"INR": ["DMART AVENUE SUPERMART BLR",
                          "UPI/DR/{ref}/BLINKIT/ICIC/blinkit.rzp@icici/Order"],
                  "USD": ["WHOLE FOODS MKT #10234"]},
    "Shopping": {"INR": ["FLIPKART INTERNET PVT LTD"], "USD": ["AMZN Mktp US*{ref}"]},
    "Transport": {"INR": ["UPI/DR/{ref}/OLA/OKAX/ola@okaxis/Payment"],
                  "USD": ["UBER *TRIP HELP.UBER.COM"]},
}


def _month_starts(start: date, end: date):
    d = date(start.year, start.month, 1)
    while d <= end:
        yield d
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def _on_day(month_start: date, day: int) -> date:
    nxt = date(month_start.year + (month_start.month == 12), month_start.month % 12 + 1, 1)
    return month_start.replace(day=min(day, (nxt - timedelta(days=1)).day))


def rows(days: int, currency: str, overspend: str | None, seed: int) -> tuple[list[dict], int]:
    rng = random.Random(seed)
    today = date.today()
    start = today - timedelta(days=days)
    out: list[dict] = []

    def add(when: date, descriptor: str, amount: float) -> None:
        out.append({"Date": when.isoformat(),
                    "Description": descriptor.format(ref=rng.randint(10**11, 10**12 - 1)),
                    "Amount": f"-{abs(amount):.2f}"})

    for descriptor, low, high, per_month in VARIABLE[currency]:
        for _ in range(max(1, round(per_month * days / 30))):
            add(start + timedelta(days=rng.randint(0, days)), descriptor,
                round(rng.uniform(low, high), 2))

    # Subscriptions and bills, on their own day every month. The most recent month of the merchant
    # in PRICE_RISE is charged more, so the price-increase detection has something to catch.
    rise_prefix, rise_factor = PRICE_RISE[currency]
    months = [m for m in _month_starts(start, today)]
    subscription_charges = 0
    for descriptor, amount, dom, wobble in RECURRING[currency]:
        charged = [d for d in (_on_day(m, dom) for m in months) if start <= d <= today]
        for when in charged:
            value = amount + (rng.uniform(-wobble, wobble) if wobble else 0)
            if descriptor.upper().startswith(rise_prefix) and when == charged[-1] and len(charged) > 2:
                value *= rise_factor
            add(when, descriptor, round(value, 2))
        subscription_charges = max(subscription_charges, len(charged))

    if overspend:
        picks = CATEGORY_MERCHANTS.get(overspend, {}).get(currency)
        if not picks:
            sys.exit(f"--overspend: no sample merchants for {overspend!r} in {currency}. "
                     f"Choose one of: {', '.join(CATEGORY_MERCHANTS)}")
        # a deliberate burst in the current month, which is the window budgets and alerts watch
        for _ in range(18):
            when = today - timedelta(days=rng.randint(0, min(days, today.day - 1) or 1))
            add(when, rng.choice(picks), round(rng.uniform(600, 2500) if currency == "INR"
                                               else rng.uniform(25, 90), 2))

    descriptor, amount = INCOME[currency]
    when = start
    while when <= today:
        out.append({"Date": when.isoformat(), "Description": descriptor, "Amount": f"{amount:.2f}"})
        when += timedelta(days=30)

    out.sort(key=lambda r: r["Date"])
    return out, subscription_charges


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-o", "--out", default="sample.csv", help="file to write (default: sample.csv)")
    # Six months. Three monthly charges are the minimum for a series to be detected at all, but a
    # price rise needs five before the amount-stability check stops treating it as noise.
    p.add_argument("--days", type=int, default=185, help="how far back to generate (default: 185)")
    p.add_argument("--currency", choices=sorted(VARIABLE), default="INR")
    p.add_argument("--overspend", metavar="CATEGORY",
                   help=f"pile spending into one category to trip its budget: {', '.join(CATEGORY_MERCHANTS)}")
    p.add_argument("--seed", type=int, default=None, help="repeat an exact file (default: random)")
    args = p.parse_args(argv)

    seed = args.seed if args.seed is not None else random.randrange(10**6)
    data, charges = rows(args.days, args.currency, args.overspend, seed)
    path = Path(args.out)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["Date", "Description", "Amount"])
        writer.writeheader()
        writer.writerows(data)

    spent = sum(float(r["Amount"]) for r in data if float(r["Amount"]) < 0)
    print(f"{path}: {len(data)} rows, {args.days} days, {args.currency}, seed {seed}")
    print(f"  spending {abs(spent):,.2f}  income {sum(float(r['Amount']) for r in data if float(r['Amount']) > 0):,.2f}")
    subs = len(RECURRING[args.currency])
    if charges >= 5:
        print(f"  {subs} recurring charges x{charges} months -> detectable subscriptions, "
              f"incl. a {PRICE_RISE[args.currency][0].title()} price rise")
    elif charges >= 3:
        print(f"  {subs} recurring charges x{charges} months -> subscriptions detected, but the "
              f"price rise needs 5 months to register. Rerun with --days 185 for that.")
    else:
        print(f"  WARNING: only {charges} charge(s) per subscription in this window. Detection needs "
              f"3, so the Subscriptions tab will be EMPTY — rerun with --days 185.")
    print("  import at Data & settings -> Import bank CSV")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
