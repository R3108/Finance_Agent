"""Transaction ingestion: categorise, de-duplicate and persist rows (seed data or CSV uploads)."""
from __future__ import annotations

import hashlib
import io
from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import pandas as pd

from .categorizer import categorize, llm_categorize
from .db import get_conn


@dataclass
class RawTxn:
    date: str             # yyyy-mm-dd
    description: str
    amount_cents: int     # negative = expense
    account_id: int | None = None
    is_transfer: bool = False


def to_cents(value) -> int:
    """Exact dollars -> cents conversion (no float rounding surprises)."""
    try:
        return int((Decimal(str(value).replace("$", "").replace(",", "").strip()) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except InvalidOperation as exc:
        raise ValueError(f"Invalid amount: {value!r}") from exc


def user_rules(user_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT pattern, category, priority FROM category_rules WHERE user_id = ?", (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def insert_transactions(user_id: int, rows: list[RawTxn], use_llm: bool = False) -> dict:
    """Categorise and insert rows. Returns counts of inserted / duplicate rows.

    The import hash includes an occurrence counter so two genuinely identical
    charges in one file are both kept, while re-uploading the same file is a no-op.
    """
    rules = user_rules(user_id)
    prepared = []
    seen: Counter = Counter()
    for r in rows:
        merchant, category, source = categorize(r.description, rules)
        if r.is_transfer:
            category, source = "Transfers", "rule"
        key = f"{r.date}|{r.description}|{r.amount_cents}|{r.account_id}"
        seen[key] += 1
        digest = hashlib.sha1(f"{key}|{seen[key]}".encode()).hexdigest()
        prepared.append([r, merchant, category, source, digest])

    if use_llm:
        unknown = sorted({p[1] for p in prepared if p[2] == "Uncategorized"})
        mapping = llm_categorize(unknown)
        for p in prepared:
            if p[2] == "Uncategorized" and p[1] in mapping:
                p[2], p[3] = mapping[p[1]], "llm"

    inserted = 0
    with get_conn() as conn:
        for r, merchant, category, source, digest in prepared:
            cur = conn.execute(
                """INSERT OR IGNORE INTO transactions
                   (user_id, account_id, date, description, merchant, amount_cents,
                    category, category_source, is_transfer, import_hash)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (user_id, r.account_id, r.date, r.description, merchant, r.amount_cents,
                 category, source, int(r.is_transfer or category == "Transfers"), digest),
            )
            inserted += cur.rowcount
    return {"received": len(rows), "inserted": inserted, "duplicates_skipped": len(rows) - inserted}


_DATE_COLS = ("date", "transaction date", "posted date", "posting date")
_DESC_COLS = ("description", "merchant", "name", "payee", "details")
_AMOUNT_COLS = ("amount", "transaction amount")


def parse_csv(content: bytes, account_id: int | None, expenses_positive: bool = False) -> list[RawTxn]:
    """Parse a bank CSV export. Accepts either a signed Amount column or Debit/Credit columns."""
    df = pd.read_csv(io.BytesIO(content), dtype=str).fillna("")
    cols = {c.lower().strip(): c for c in df.columns}

    def pick(options):
        return next((cols[o] for o in options if o in cols), None)

    date_col, desc_col, amt_col = pick(_DATE_COLS), pick(_DESC_COLS), pick(_AMOUNT_COLS)
    debit_col, credit_col = cols.get("debit"), cols.get("credit")
    if not date_col or not desc_col or not (amt_col or debit_col or credit_col):
        raise ValueError("CSV needs Date, Description and Amount (or Debit/Credit) columns")

    out: list[RawTxn] = []
    for _, row in df.iterrows():
        if amt_col:
            cents = to_cents(row[amt_col])
            if expenses_positive:
                cents = -cents
        else:
            debit = to_cents(row[debit_col]) if debit_col and row[debit_col] else 0
            credit = to_cents(row[credit_col]) if credit_col and row[credit_col] else 0
            cents = credit - abs(debit)
        parsed = pd.to_datetime(row[date_col], errors="coerce")
        if pd.isna(parsed) or not row[desc_col].strip():
            continue
        out.append(RawTxn(parsed.strftime("%Y-%m-%d"), row[desc_col].strip(), cents, account_id))
    return out


def recategorize_all(user_id: int) -> int:
    """Re-apply rules to every non user-edited transaction. Returns number of rows changed."""
    rules = user_rules(user_id)
    changed = 0
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, description, category, is_transfer FROM transactions "
            "WHERE user_id = ? AND category_source != 'manual'", (user_id,)
        ).fetchall()
        for row in rows:
            if row["is_transfer"]:
                continue
            merchant, category, source = categorize(row["description"], rules)
            if category != row["category"]:
                conn.execute(
                    "UPDATE transactions SET merchant = ?, category = ?, category_source = ? WHERE id = ?",
                    (merchant, category, source, row["id"]),
                )
                changed += 1
    return changed
