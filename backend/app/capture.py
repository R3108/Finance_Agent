"""Transaction capture from bank alert SMS and emails — hands-free tracking without a bank API.

Every Indian bank texts on every UPI payment, card swipe, salary credit and ATM withdrawal. Those
alerts are the most complete, most timely feed of a person's money that exists, and reading them
needs no bank partnership, no Account Aggregator licence and no screen-scraping. This module turns
them into ledger rows:

    "Sent Rs.250.00 From HDFC Bank A/C *1234 To SWIGGY On 14/09/26 Ref 425712345678"
        -> 2026-09-14, UPI/DR/425712345678/SWIGGY, -25000  (then categorised as Swiggy / Dining)

Two ways in: paste a batch of messages (free), or have the phone forward each SMS as it arrives to
a per-user webhook (`/api/capture/inbound`, Pro) — Android SMS-forwarder apps and iOS Shortcuts
automations can both do this.

Design choices:

* **Deterministic parsing only.** Amounts go through `Decimal` into integer minor units, like CSV
  import. No model ever reads the text, so an amount can't be hallucinated.
* **Refuse rather than guess.** OTPs, declined payments, bill reminders, "will be debited" mandate
  notices and anything without a clear amount and direction are skipped *with a reason*, so the
  preview shows exactly what was left out.
* **Balances are never the amount.** Figures introduced by "Avl Bal", "limit" or "due" are excluded
  before the transaction amount is chosen — the classic failure of naive SMS parsers.
* **UPI rows are written as UPI descriptors**, so the existing UPI parser and India merchant pack
  categorise them exactly as they would a bank-statement line, and user rules apply unchanged.
* **Idempotent.** Rows go through `ingest.insert_transactions`, whose import hash makes a re-pasted or
  re-forwarded SMS a no-op; within one batch, messages sharing a UPI reference collapse to one.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from .db import get_conn
from .ingest import RawTxn, insert_transactions, to_cents

MAX_MESSAGES = 200
MAX_TEXT = 100_000

# ----------------------------------------------------------------------------- vocabulary

_SKIP = [
    (re.compile(r"\bOTP\b|one[\s-]?time[\s-]?password|verification code|\bdo not share\b", re.I), "One-time password, not a transaction"),
    (re.compile(r"\bdeclined\b|\bfailed\b|\bunsuccessful\b|\breversed\b|could not be processed", re.I), "Declined or failed payment"),
    (re.compile(r"\bwill be (?:debited|deducted|charged)\b|\bis due\b|\bdue (?:on|by|date)\b|min(?:imum)?\.? (?:amt|amount) due|total (?:amt|amount) due", re.I),
     "Reminder or upcoming charge, not a completed transaction"),
    (re.compile(r"\bcollect request\b|\brequested (?:money|rs|inr|₹)|has requested", re.I), "Payment request, not a payment"),
]

_EXPENSE = re.compile(r"\b(debited|spent|sent|paid|withdrawn|purchase[d]?|debit|charged|transaction|txn of|transferred)\b", re.I)
_INCOME = re.compile(r"\b(credited|received|deposited|refund(?:ed)?|cashback|reversal)\b", re.I)

_CUR = r"(?:rs\.?|inr|₹|\$|usd|eur|€|£|gbp|aed|sgd|s\$|a\$|c\$)"
_NUM = r"(\d[\d,]*(?:\.\d{1,2})?)"
_AMOUNT = re.compile(rf"{_CUR}\s*{_NUM}", re.I)
_AMOUNT_AFTER = re.compile(rf"{_NUM}\s*(?:rs\.?|inr|₹)(?![a-z])", re.I)          # "250.00 INR"
_AMOUNT_BARE = re.compile(rf"\b(?:debited|credited|spent|sent|paid|withdrawn)\s+(?:by|for|of|with)?\s*{_NUM}", re.I)
# Words that make the amount right after them a balance or limit, not the transaction.
_NOT_TXN_AMOUNT = re.compile(r"(?:bal(?:ance)?|lmt|limit|avl|available|due|outstanding|o/s)[\s.:\-]*(?:is|of)?[\s:]*$", re.I)

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_DATE_ISO = re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b")
_DATE_NUM = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})\b")
_DATE_MON = re.compile(r"\b(\d{1,2})[-\s]?([A-Za-z]{3})[a-z]*[-\s,]?\s?(\d{2,4})\b")
_DATE_MON_US = re.compile(r"\b([A-Za-z]{3})[a-z]*\.?\s(\d{1,2}),?\s(\d{4})\b")

_VPA = re.compile(r"\b([\w.\-]{2,})@([a-z][a-z0-9]{1,15})\b", re.I)
_ACCOUNT = re.compile(r"(?:a/?c|acct|account|card(?:\s*no\.?)?|ending(?:\s*in)?)\s*(?:no\.?\s*)?[:\s]*[x*\s]*(\d{3,4})\b", re.I)
_REF = re.compile(r"(?:upi\s*ref(?:\s*no)?|ref(?:\s*no)?|refno|upi|utr|rrn|txn\s*id)[\s.:#-]*(\d{9,})", re.I)

_STOP = (r"(?=\s+(?:on|via|ref\w*|upi|avl|using|for|from|by|dated|at|towards|credited|debited|is|has|was)\b"
         r"|[.;,(\n]|\s-|$)")
_PAYEE_PATTERNS = [
    re.compile(r"\binfo[:\s]+(?:upi[/-])?(.+?)(?=[.;\n]|$)", re.I),
    re.compile(rf"\btrf to\s+(.+?){_STOP}", re.I),
    re.compile(rf"\bat\s+(?!atm\b)(.+?){_STOP}", re.I),
    re.compile(rf"\bto\s+(?!(?:your|a/?c|ac|acct|account|the|you|beneficiary)\b)(?:vpa\s+)?(.+?){_STOP}", re.I),
    re.compile(r";\s*(.+?)\s+credited", re.I),                                 # ICICI: "...; AMAZON PAY credited"
    re.compile(rf"\bfrom\s+(?!(?:your|a/?c|ac|acct|account|hdfc bank|sbi|icici bank|axis bank|kotak bank)\b)(.+?){_STOP}", re.I),
    re.compile(rf"\bwith\s+(.+?){_STOP}", re.I),
]
_PAYEE_JUNK = re.compile(r"^(?:a/?c|acct|account|card|your|bank|xx+\d*|\*+\d*|x\d+|mr\.?|ms\.?|rs\.?|inr|vpa|upi|neft|imps|rtgs)$", re.I)
_LINE_JUNK = re.compile(r"not you|avl|limit|call|sms|block|ref|\bon\b|bank|card|a/c|acct|account|www|http|dear|thank", re.I)


class CaptureError(Exception):
    pass


@dataclass
class Parsed:
    """One message's outcome — a transaction to import, or the reason it was skipped."""
    text: str
    ok: bool
    reason: str | None = None
    date: str | None = None
    amount_cents: int | None = None       # signed: negative = money out
    payee: str | None = None
    mode: str | None = None               # upi | card | atm | bank | unknown
    account_last4: str | None = None
    reference: str | None = None
    description: str | None = None        # what's stored as the bank descriptor
    is_transfer: bool = False
    date_assumed: bool = False            # no date in the message; the received date was used
    warnings: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------- splitting

def split_messages(text: str) -> list[str]:
    """A pasted blob -> individual messages.

    Blank lines separate messages. Without any, each line that carries its own amount is treated as
    a message and lines without one are folded into the previous line (multi-line bank formats).
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if len(blocks) > 1:
        return blocks[:MAX_MESSAGES]
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    starts_new = [bool((_AMOUNT.search(ln) or _AMOUNT_AFTER.search(ln)) and (_EXPENSE.search(ln) or _INCOME.search(ln)))
                  for ln in lines]
    if sum(starts_new) <= 1:
        return [text]
    out: list[str] = []
    for ln, new in zip(lines, starts_new):
        if new or not out:
            out.append(ln)
        else:
            out[-1] += "\n" + ln
    return out[:MAX_MESSAGES]


# ----------------------------------------------------------------------------- field extraction

def _amount(flat: str) -> int | None:
    """The transaction amount: the first currency figure that isn't a balance, limit or amount due."""
    for pattern in (_AMOUNT, _AMOUNT_AFTER, _AMOUNT_BARE):
        for m in pattern.finditer(flat):
            if _NOT_TXN_AMOUNT.search(flat[max(0, m.start() - 30):m.start()]):
                continue
            try:
                cents = abs(to_cents(m.group(1)))
            except ValueError:
                continue
            if cents > 0:
                return cents
    return None


def _direction(flat: str) -> str | None:
    exp, inc = _EXPENSE.search(flat), _INCOME.search(flat)
    if exp and inc:
        return "expense" if exp.start() < inc.start() else "income"
    return "expense" if exp else "income" if inc else None


def _year(y: str) -> int:
    n = int(y)
    return 2000 + n if n < 100 else n


def _date(flat: str, dayfirst: bool) -> date | None:
    candidates: list[tuple[int, date]] = []
    for m in _DATE_ISO.finditer(flat):
        try:
            candidates.append((m.start(), date(int(m[1]), int(m[2]), int(m[3]))))
        except ValueError:
            pass
    for m in _DATE_MON.finditer(flat):
        mon = _MONTHS.get(m[2][:3].lower())
        if mon:
            try:
                candidates.append((m.start(), date(_year(m[3]), mon, int(m[1]))))
            except ValueError:
                pass
    for m in _DATE_MON_US.finditer(flat):
        mon = _MONTHS.get(m[1][:3].lower())
        if mon:
            try:
                candidates.append((m.start(), date(int(m[3]), mon, int(m[2]))))
            except ValueError:
                pass
    for m in _DATE_NUM.finditer(flat):
        if any(abs(pos - m.start()) < 3 for pos, _ in candidates):
            continue   # the ISO pattern already read this one
        a, b, y = int(m[1]), int(m[2]), _year(m[3])
        day, month = (a, b) if dayfirst else (b, a)
        if month > 12 and day <= 12:   # unambiguous the other way round, e.g. 09/14/2026 on a rupee ledger
            day, month = month, day
        try:
            candidates.append((m.start(), date(y, month, day)))
        except ValueError:
            pass
    return min(candidates)[1] if candidates else None


def _clean_payee(raw: str) -> str | None:
    s = re.sub(r"\s+", " ", raw).strip(" .:;,-*'\"")
    s = re.sub(r"^(?:vpa|upi|m/s|mr\.?|ms\.?)\s+", "", s, flags=re.I)
    if "@" in s:
        s = s.split("@")[0]
    s = re.sub(r"\s+(?:on|via|ref|upi|avl)$", "", s, flags=re.I).strip(" .-")
    if not s or len(s) < 2 or _PAYEE_JUNK.match(s) or re.fullmatch(r"[\dx*\s/-]+", s, re.I):
        return None
    if _AMOUNT.search(s) or re.search(r"\d{5,}", s):
        return None
    return s[:60]


def _payee(text: str, flat: str) -> str | None:
    for pattern in _PAYEE_PATTERNS:
        for m in pattern.finditer(flat):
            name = _clean_payee(m.group(1))
            if name:
                return name
    vpa = _VPA.search(flat)
    if vpa:
        # a phone-number VPA is a person or a small shop, not a brand the rules could recognise
        return "UPI transfer" if re.fullmatch(r"\d{10,12}", vpa.group(1)) else _clean_payee(vpa.group(1))
    # multi-line card alerts put the merchant on a line of its own: "Spent INR 450 / Card XX12 / 14-09-26 / DMART"
    for line in text.split("\n")[1:]:
        line = line.strip()
        if line and not re.search(r"\d", line) and not _LINE_JUNK.search(line) and len(line) <= 40:
            return _clean_payee(line)
    return None


def _mode(flat: str) -> str:
    low = flat.lower()
    if "atm" in low and ("withdraw" in low or "cash" in low):
        return "atm"
    if "upi" in low or _VPA.search(flat):
        return "upi"
    if "card" in low:
        return "card"
    if any(w in low for w in ("neft", "imps", "rtgs", "a/c", "acct", "account")):
        return "bank"
    return "unknown"


# ----------------------------------------------------------------------------- one message

def parse_message(text: str, today: date | None = None, dayfirst: bool = True) -> Parsed:
    today = today or date.today()
    text = text.strip()
    flat = re.sub(r"\s+", " ", text)
    for pattern, reason in _SKIP:
        if pattern.search(flat):
            return Parsed(text, False, reason)
    direction = _direction(flat)
    if direction is None:
        return Parsed(text, False, "No debit or credit wording found")
    amount = _amount(flat)
    if amount is None:
        return Parsed(text, False, "No amount found")

    p = Parsed(text, True)
    when = _date(flat, dayfirst)
    if when is None:
        when, p.date_assumed = today, True
        p.warnings.append("No date in the message — used the day it was received")
    elif when > today + timedelta(days=1) or when < today - timedelta(days=730):
        p.warnings.append("The date looks unusual — check it before importing")
    p.date = when.isoformat()
    p.amount_cents = -amount if direction == "expense" else amount
    p.mode = _mode(flat)
    acct = _ACCOUNT.search(flat)
    p.account_last4 = acct.group(1) if acct else None
    ref = _REF.search(flat)
    p.reference = ref.group(1) if ref else None
    low = flat.lower()

    if p.mode == "atm":
        p.payee, p.description = "ATM cash withdrawal", "ATM CASH WITHDRAWAL"
        return p
    if re.search(r"credit ?card (?:bill )?payment|towards (?:your )?(?:\w+ )?(?:bank )?credit ?card|payment .{0,40}received.{0,40}card", low):
        # paying the card bill moves money between the user's own accounts; it isn't spending
        p.payee, p.description, p.is_transfer = "Credit card payment", "CREDIT CARD PAYMENT", True
        return p

    p.payee = _payee(text, flat)
    if not p.payee:
        p.warnings.append("Couldn't tell who was paid — it will import as Unknown")
    name = (p.payee or "UNKNOWN").upper()
    if direction == "income" and "salary" in low and "SALARY" not in name:
        name = f"SALARY {name}"
    if p.mode == "upi":
        p.description = "/".join(x for x in ("UPI", "DR" if direction == "expense" else "CR", p.reference, name) if x)
    elif p.mode == "bank" and re.search(r"\b(neft|imps|rtgs)\b", low):
        rail = re.search(r"\b(neft|imps|rtgs)\b", low).group(1).upper()
        p.description = f"{rail}/{'DR' if direction == 'expense' else 'CR'}/{name}"
    else:
        p.description = name
    return p


# ----------------------------------------------------------------------------- batches

def _account_for(user_id: int, last4: str | None, default_id: int | None) -> int | None:
    """An account whose name or institution mentions these last digits, else the chosen default."""
    if last4:
        with get_conn() as conn:
            row = conn.execute("SELECT id FROM accounts WHERE user_id = ? AND (name LIKE ? OR institution LIKE ?) "
                               "ORDER BY id LIMIT 1", (user_id, f"%{last4}%", f"%{last4}%")).fetchone()
        if row:
            return int(row["id"])
    return default_id


def parse_batch(text: str, today: date | None = None, dayfirst: bool = True) -> list[Parsed]:
    if len(text) > MAX_TEXT:
        raise CaptureError(f"That's too much text at once ({MAX_TEXT // 1000}k characters max). Paste it in parts.")
    out: list[Parsed] = []
    seen_refs: set[str] = set()
    for msg in split_messages(text):
        p = parse_message(msg, today, dayfirst)
        if p.ok and p.reference:
            if p.reference in seen_refs:
                p.ok, p.reason = False, "Duplicate of another message in this batch (same reference number)"
            seen_refs.add(p.reference)
        out.append(p)
    return out


def preview(user_id: int, text: str, today: date | None = None, dayfirst: bool = True) -> dict:
    items = parse_batch(text, today, dayfirst)
    from .categorizer import categorize
    from .ingest import user_rules
    rules = user_rules(user_id)
    rows = []
    for p in items:
        row = asdict(p)
        row["amount"] = p.amount_cents / 100 if p.amount_cents is not None else None
        if p.ok:
            merchant, category, _ = categorize(p.description or "", rules)
            row.update(merchant=merchant, category="Transfers" if p.is_transfer else category)
        rows.append(row)
    return {"items": rows, "parsed": sum(p.ok for p in items), "skipped": sum(not p.ok for p in items)}


def import_text(user_id: int, text: str, account_id: int | None = None, today: date | None = None,
                dayfirst: bool = True, use_llm: bool = False) -> dict:
    items = parse_batch(text, today, dayfirst)
    rows = [RawTxn(p.date, p.description, p.amount_cents, _account_for(user_id, p.account_last4, account_id),
                   p.is_transfer) for p in items if p.ok]
    result = insert_transactions(user_id, rows, use_llm=use_llm) if rows else {"received": 0, "inserted": 0, "duplicates_skipped": 0}
    return {**result, "skipped": [{"text": p.text[:160], "reason": p.reason} for p in items if not p.ok]}


# ----------------------------------------------------------------------------- forwarding tokens

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_status(user_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT account_id, last_used_at, created_at FROM capture_tokens WHERE user_id = ?",
                           (user_id,)).fetchone()
    return {"active": bool(row), **({k: row[k] for k in ("account_id", "last_used_at", "created_at")} if row else {})}


def mint_token(user_id: int, account_id: int | None) -> str:
    """A new forwarding token (any earlier one stops working). Shown once; only its hash is kept."""
    if account_id is not None:
        with get_conn() as conn:
            if not conn.execute("SELECT 1 FROM accounts WHERE id = ? AND user_id = ?", (account_id, user_id)).fetchone():
                raise CaptureError("Unknown account")
    token = "lcap_" + secrets.token_urlsafe(24)
    with get_conn() as conn:
        conn.execute("INSERT INTO capture_tokens (user_id, token_hash, account_id) VALUES (?,?,?) "
                     "ON CONFLICT(user_id) DO UPDATE SET token_hash = excluded.token_hash, account_id = excluded.account_id, "
                     "created_at = datetime('now'), last_used_at = NULL", (user_id, _hash(token), account_id))
    return token


def revoke_token(user_id: int) -> bool:
    with get_conn() as conn:
        return conn.execute("DELETE FROM capture_tokens WHERE user_id = ?", (user_id,)).rowcount > 0


def user_for_token(token: str | None) -> tuple[int, int | None] | None:
    if not token or not token.startswith("lcap_"):
        return None
    with get_conn() as conn:
        row = conn.execute("SELECT user_id, account_id FROM capture_tokens WHERE token_hash = ?", (_hash(token),)).fetchone()
        if row:
            conn.execute("UPDATE capture_tokens SET last_used_at = datetime('now') WHERE user_id = ?", (row["user_id"],))
    return (int(row["user_id"]), row["account_id"]) if row else None
