"""Split & settle: shared bills with friends, who owes whom, and one-tap UPI settlement.

Every group dinner, cab and trip in India ends in "I'll UPI you later". This keeps that ledger next
to the charge it came from:

* Split a transaction (or any amount) equally or by custom shares. Shares are computed in integer
  minor units and the leftover paisa go to the first people listed, so the parts always add up to
  exactly the whole — never ₹333.33 × 3 = ₹999.99.
* Balances net per person: if Asha owes you ₹600 for dinner and you owe her ₹250 for a cab, she owes
  you ₹350.
* Settling produces a `upi://pay` link (when you owe someone and know their UPI id) or a ready-to-send
  reminder carrying *your* UPI id and a pay link (when they owe you). The link opens GPay, PhonePe,
  Paytm or any BHIM app with the amount filled in. Ledgerly never moves money itself.

A split doesn't rewrite the transaction: the bank row stays exactly as the bank reported it. What
changes is the "your real share" figure, which subtracts what others owe you for charges you paid.
"""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import quote, urlencode

from . import money
from .db import get_conn

VPA_RE = re.compile(r"^[\w.\-]{2,256}@[a-zA-Z][a-zA-Z0-9]{1,64}$")
MAX_PEOPLE = 20


class SplitError(Exception):
    pass


def valid_vpa(vpa: str | None) -> str | None:
    if vpa is None or not vpa.strip():
        return None
    vpa = vpa.strip()
    if not VPA_RE.match(vpa):
        raise SplitError(f"“{vpa}” isn't a UPI id. It looks like name@bank, e.g. asha@okicici.")
    return vpa


def equal_shares(total_cents: int, n: int) -> list[int]:
    """Split exactly: the first `total % n` shares carry one extra minor unit."""
    if n <= 0:
        raise SplitError("Add at least one person to split with.")
    base, extra = divmod(abs(total_cents), n)
    return [base + (1 if i < extra else 0) for i in range(n)]


def create(user_id: int, people: list[dict], *, transaction_id: int | None = None, total_cents: int | None = None,
           include_me: bool = True, custom_cents: list[int] | None = None, direction: str = "owed_to_me",
           note: str | None = None) -> dict:
    """Record who owes what for one bill.

    `direction='owed_to_me'` means the user paid and the others owe them (the usual case when the
    charge is on the user's own statement); `'i_owe'` means someone else paid and the user owes them.
    """
    if not people:
        raise SplitError("Add at least one person to split with.")
    if len(people) > MAX_PEOPLE:
        raise SplitError(f"Up to {MAX_PEOPLE} people per split.")
    names = [str(p.get("name", "")).strip()[:60] for p in people]
    if any(not n for n in names):
        raise SplitError("Every person needs a name.")
    if len({n.lower() for n in names}) != len(names):
        raise SplitError("The same person is listed twice.")
    vpas = [valid_vpa(p.get("vpa")) for p in people]

    merchant = None
    with get_conn() as conn:
        if transaction_id is not None:
            row = conn.execute("SELECT amount_cents, merchant, date FROM transactions WHERE id = ? AND user_id = ?",
                               (transaction_id, user_id)).fetchone()
            if not row:
                raise SplitError("Transaction not found.")
            total_cents = abs(int(row["amount_cents"]))
            merchant = row["merchant"]
            note = note or f"{row['merchant']} on {row['date']}"
        if not total_cents or total_cents <= 0:
            raise SplitError("Enter the bill amount.")

        if custom_cents is not None:
            if len(custom_cents) != len(people) or any(c <= 0 for c in custom_cents):
                raise SplitError("Give each person a positive share.")
            if sum(custom_cents) > total_cents:
                raise SplitError("The shares add up to more than the bill.")
            shares = custom_cents
        else:
            parts = equal_shares(total_cents, len(people) + (1 if include_me else 0))
            # the user's own part is the last one, so any rounding paisa land on the friends' side
            shares = parts[:len(people)]

        sign = 1 if direction == "owed_to_me" else -1
        ids = []
        for name, vpa, share in zip(names, vpas, shares):
            ids.append(conn.execute(
                "INSERT INTO splits (user_id, transaction_id, person, person_vpa, amount_cents, note) VALUES (?,?,?,?,?,?)",
                (user_id, transaction_id, name, vpa, sign * share, (note or "")[:140] or None)).lastrowid)
            if vpa:   # remember a friend's UPI id for next time
                conn.execute("UPDATE splits SET person_vpa = ? WHERE user_id = ? AND lower(person) = lower(?) "
                             "AND person_vpa IS NULL", (vpa, user_id, name))
    return {"ids": ids, "total": total_cents / 100, "merchant": merchant,
            "shares": [{"person": n, "amount": s / 100} for n, s in zip(names, shares)],
            "your_share": (total_cents - sum(shares)) / 100}


def _person_vpas(conn, user_id: int) -> dict[str, str]:
    rows = conn.execute("SELECT lower(person) AS p, person_vpa FROM splits WHERE user_id = ? AND person_vpa IS NOT NULL "
                        "ORDER BY id", (user_id,)).fetchall()
    return {r["p"]: r["person_vpa"] for r in rows}


def overview(user_id: int) -> dict:
    """Per-person net balances, open items, and recently settled ones."""
    with get_conn() as conn:
        me = conn.execute("SELECT name, upi_vpa FROM users WHERE id = ?", (user_id,)).fetchone()
        open_rows = conn.execute(
            "SELECT s.*, t.merchant, t.date AS txn_date FROM splits s LEFT JOIN transactions t ON t.id = s.transaction_id "
            "WHERE s.user_id = ? AND s.settled_at IS NULL ORDER BY s.id DESC", (user_id,)).fetchall()
        settled = conn.execute(
            "SELECT person, amount_cents, note, settled_at FROM splits WHERE user_id = ? AND settled_at IS NOT NULL "
            "ORDER BY settled_at DESC, id DESC LIMIT 20", (user_id,)).fetchall()
        vpas = _person_vpas(conn, user_id)

    people: dict[str, dict] = {}
    for r in open_rows:
        key = r["person"].lower()
        p = people.setdefault(key, {"person": r["person"], "net_cents": 0, "items": 0,
                                    "vpa": vpas.get(key), "oldest": r["created_at"]})
        p["net_cents"] += int(r["amount_cents"])
        p["items"] += 1
        p["oldest"] = min(p["oldest"], r["created_at"])

    balances = []
    for p in sorted(people.values(), key=lambda x: -abs(x["net_cents"])):
        net = p.pop("net_cents")
        entry = {**p, "net": net / 100, "net_text": money.format_minor(abs(net)),
                 "direction": "owes_you" if net > 0 else "you_owe" if net < 0 else "even"}
        entry.update(settle_links(p["person"], net, p["vpa"], me["name"] if me else None, me["upi_vpa"] if me else None))
        balances.append(entry)

    owed = sum(int(r["amount_cents"]) for r in open_rows if r["amount_cents"] > 0)
    owe = -sum(int(r["amount_cents"]) for r in open_rows if r["amount_cents"] < 0)
    return {
        "your_upi_vpa": me["upi_vpa"] if me else None,
        "owed_to_you": owed / 100, "you_owe": owe / 100, "net": (owed - owe) / 100,
        "balances": balances,
        "open": [{"id": r["id"], "person": r["person"], "amount": r["amount_cents"] / 100,
                  "amount_text": money.format_minor(abs(int(r["amount_cents"]))),
                  "direction": "owes_you" if r["amount_cents"] > 0 else "you_owe",
                  "note": r["note"], "transaction_id": r["transaction_id"], "merchant": r["merchant"],
                  "date": r["txn_date"] or r["created_at"][:10]} for r in open_rows],
        "settled": [{"person": r["person"], "amount": r["amount_cents"] / 100, "note": r["note"],
                     "settled_at": r["settled_at"]} for r in settled],
    }


def upi_link(vpa: str, name: str, amount_cents: int, note: str) -> str:
    """A `upi://pay` deep link (NPCI linking spec). Amount is fixed to two decimals, INR only."""
    params = {"pa": vpa, "pn": name[:50], "am": f"{abs(amount_cents) / 100:.2f}", "cu": "INR", "tn": note[:80]}
    return "upi://pay?" + urlencode(params, quote_via=quote)


def settle_links(person: str, net_cents: int, their_vpa: str | None, my_name: str | None, my_vpa: str | None) -> dict:
    """How to close out a balance: a pay link when the user owes, a reminder message when they're owed.

    UPI links are rupee-only by specification, so they're offered only for INR ledgers.
    """
    inr = money.current_code() == "INR"
    amount = money.format_minor(abs(net_cents))
    out: dict = {"pay_link": None, "reminder": None, "reminder_pay_link": None}
    if net_cents < 0 and their_vpa and inr:
        out["pay_link"] = upi_link(their_vpa, person, net_cents, "Settling up via Ledgerly")
    elif net_cents > 0:
        first = person.split()[0]
        text = f"Hi {first}! Just a nudge — your share comes to {amount}."
        if my_vpa and inr:
            link = upi_link(my_vpa, my_name or "Me", net_cents, "Split settle-up")
            out["reminder_pay_link"] = link
            text += f" You can pay me at {my_vpa} or tap: {link}"
        out["reminder"] = text + " Thanks!"
    return out


def settle(user_id: int, person: str | None = None, split_id: int | None = None) -> int:
    """Mark one split, or everything open with one person, as settled."""
    with get_conn() as conn:
        if split_id is not None:
            return conn.execute("UPDATE splits SET settled_at = datetime('now') WHERE id = ? AND user_id = ? "
                                "AND settled_at IS NULL", (split_id, user_id)).rowcount
        if not person:
            raise SplitError("Say who to settle with.")
        return conn.execute("UPDATE splits SET settled_at = datetime('now') WHERE user_id = ? AND lower(person) = lower(?) "
                            "AND settled_at IS NULL", (user_id, person.strip())).rowcount


def delete(user_id: int, split_id: int) -> bool:
    with get_conn() as conn:
        return conn.execute("DELETE FROM splits WHERE id = ? AND user_id = ?", (split_id, user_id)).rowcount > 0


def real_share(user_id: int, tx, as_of: date) -> dict:
    """This month's spending, and how much of it was really the user's once friends pay their part."""
    from . import analytics as an
    month = an.month_str(as_of)
    sp = an.spending_frame(tx)
    sp = sp[sp["month"] == month]
    owed_map = owed_by_transaction(user_id)
    fronted = sum(owed_map.get(int(i), 0) for i in sp["id"])
    spent = int(sp["spend_cents"].sum())
    return {"month": month, "spending": an.money(spent), "fronted_for_others": an.money(fronted),
            "your_share": an.money(spent - fronted)}


def owed_by_transaction(user_id: int) -> dict[int, int]:
    """Minor units others owe the user, per transaction — whether settled or not.

    This is the part of a charge that was never really the user's spending, which is what the "your
    real share" figure subtracts.
    """
    with get_conn() as conn:
        rows = conn.execute("SELECT transaction_id, SUM(amount_cents) AS c FROM splits WHERE user_id = ? "
                            "AND transaction_id IS NOT NULL AND amount_cents > 0 GROUP BY transaction_id",
                            (user_id,)).fetchall()
    return {int(r["transaction_id"]): int(r["c"]) for r in rows}
