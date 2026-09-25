"""Receipt files attached to transactions, and the matching that saves people from filing them by hand.

Parsing is **best effort and entirely optional**. A receipt's job is to be there when someone asks
for proof, so a file that can't be read is still stored and still attachable — it just doesn't get a
suggested match. Text extraction uses `pypdf` for PDFs and `pytesseract` for images when those
happen to be installed; neither is a dependency, and `parse()` degrades to "stored, unparsed"
rather than failing an upload.

Files are written under `config.settings.receipts_dir/<user_id>/`, named by content hash so re-uploading
the same receipt doesn't store it twice. Nothing about a stored file is trusted: the extension comes
from an allow-list of content types, never from the uploaded filename.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from . import analytics as an
from . import config
from .db import get_conn, query_df

log = logging.getLogger("ledgerly.receipts")

MAX_BYTES = 8 * 1024 * 1024

#: content type -> the extension we store it under. The uploaded filename never decides this.
ALLOWED = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "text/plain": ".txt",
}

#: Days either side of a transaction that a receipt of the same amount may belong to. Card
#: settlement usually posts within a couple of days of the purchase.
MATCH_WINDOW_DAYS = 5


class ReceiptError(ValueError):
    """A receipt upload that can't be accepted, phrased for the person who tried it."""


@dataclass
class Parsed:
    total_cents: int | None = None
    when: date | None = None
    merchant: str | None = None

    @property
    def empty(self) -> bool:
        return self.total_cents is None and self.when is None and self.merchant is None


# ----------------------------------------------------------------------------- text extraction

def _pdf_text(data: bytes) -> str:
    try:
        import io

        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:5])
    except Exception as exc:
        log.info("PDF text extraction failed: %s", exc)
        return ""


def _image_text(data: bytes) -> str:
    try:
        import io

        import pytesseract
        from PIL import Image
    except ImportError:
        return ""
    try:
        return pytesseract.image_to_string(Image.open(io.BytesIO(data)))
    except Exception as exc:
        # a missing tesseract binary raises at call time, not import time
        log.info("OCR failed: %s", exc)
        return ""


def extract_text(data: bytes, content_type: str) -> str:
    if content_type == "application/pdf":
        return _pdf_text(data)
    if content_type == "text/plain":
        return data[:200_000].decode("utf-8", errors="replace")
    if content_type.startswith("image/"):
        return _image_text(data)
    return ""


# ----------------------------------------------------------------------------- parsing

#: Amounts near a total-ish word. Handles ₹1,234.56, Rs. 1234, $1,234.56 and Indian grouping.
_TOTAL_RE = re.compile(
    r"(?:grand\s+total|total\s+amount|amount\s+payable|net\s+payable|total|amount|paid)\s*[:\-]?\s*"
    r"(?:₹|rs\.?|inr|\$|usd|€|£)?\s*([\d][\d,]*(?:\.\d{1,2})?)",
    re.IGNORECASE)
_ANY_AMOUNT_RE = re.compile(r"(?:₹|rs\.?|inr|\$|usd|€|£)\s*([\d][\d,]*(?:\.\d{1,2})?)", re.IGNORECASE)

_DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), ("y", "m", "d")),
    (re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b"), ("d", "m", "y")),
    (re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b"), ("d", "mon", "y")),
    (re.compile(r"\b([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})\b"), ("mon", "d", "y")),
]
_MONTHS = {m.lower(): i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _to_cents(raw: str) -> int | None:
    try:
        return int(round(float(raw.replace(",", "")) * 100))
    except ValueError:
        return None


def parse_total(text: str) -> int | None:
    """The largest amount labelled as a total — receipts list line items before the total."""
    candidates = [c for c in (_to_cents(m.group(1)) for m in _TOTAL_RE.finditer(text)) if c]
    if not candidates:
        candidates = [c for c in (_to_cents(m.group(1)) for m in _ANY_AMOUNT_RE.finditer(text)) if c]
    return max(candidates) if candidates else None


def parse_date(text: str) -> date | None:
    for pattern, order in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            parts = dict(zip(order, m.groups()))
            try:
                month = (_MONTHS[parts["mon"][:3].lower()] if "mon" in parts else int(parts["m"]))
                found = date(int(parts["y"]), month, int(parts["d"]))
            except (KeyError, ValueError):
                continue
            # a receipt is never for the future and rarely older than a few years
            if date(2000, 1, 1) <= found <= date.today() + timedelta(days=1):
                return found
    return None


def parse_merchant(text: str) -> str | None:
    """The first substantial line — receipts almost always lead with the seller's name."""
    for line in text.splitlines():
        cleaned = line.strip(" *-—_\t")
        if 3 <= len(cleaned) <= 60 and any(c.isalpha() for c in cleaned):
            if re.fullmatch(r"[\d\W]+", cleaned):
                continue
            return cleaned[:60]
    return None


def parse(data: bytes, content_type: str) -> Parsed:
    text = extract_text(data, content_type)
    if not text.strip():
        return Parsed()
    return Parsed(parse_total(text), parse_date(text), parse_merchant(text))


# ----------------------------------------------------------------------------- storage

def _dir_for(user_id: int) -> Path:
    path = config.settings.receipts_dir / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def store(user_id: int, filename: str, content_type: str, data: bytes) -> dict:
    """Save an uploaded receipt and return its row. Re-uploading the same bytes reuses the file."""
    if content_type not in ALLOWED:
        raise ReceiptError(f"That file type isn't supported. Upload a PDF or an image "
                           f"({', '.join(sorted({e.lstrip('.') for e in ALLOWED.values()}))}).")
    if not data:
        raise ReceiptError("That file is empty.")
    if len(data) > MAX_BYTES:
        raise ReceiptError(f"Receipts are limited to {MAX_BYTES // (1024 * 1024)} MB.")

    digest = hashlib.sha256(data).hexdigest()
    stored_name = f"{digest}{ALLOWED[content_type]}"
    path = _dir_for(user_id) / stored_name
    if not path.exists():
        path.write_bytes(data)

    parsed = parse(data, content_type)
    safe_name = Path(filename).name[:120] or stored_name
    with get_conn() as conn:
        receipt_id = conn.execute(
            """INSERT INTO receipts (user_id, filename, content_type, size_bytes, stored_path,
                                     parsed_total_cents, parsed_date, parsed_merchant)
               VALUES (?,?,?,?,?,?,?,?)""",
            (user_id, safe_name, content_type, len(data), f"{user_id}/{stored_name}",
             parsed.total_cents, parsed.when.isoformat() if parsed.when else None,
             parsed.merchant)).lastrowid
    return {"id": receipt_id, "filename": safe_name, "parsed": not parsed.empty,
            "parsed_total": an.money(parsed.total_cents) if parsed.total_cents else None,
            "parsed_date": parsed.when.isoformat() if parsed.when else None,
            "parsed_merchant": parsed.merchant}


def path_for(user_id: int, receipt_id: int) -> tuple[Path, str, str]:
    with get_conn() as conn:
        row = conn.execute("SELECT stored_path, filename, content_type FROM receipts WHERE id = ? AND user_id = ?",
                           (receipt_id, user_id)).fetchone()
    if not row:
        raise ReceiptError("Receipt not found.")
    path = (config.settings.receipts_dir / row["stored_path"]).resolve()
    # stored_path is ours, but resolve-and-check anyway: a path that escapes the receipts directory
    # must never be served, whatever put it in the database
    if not str(path).startswith(str(config.settings.receipts_dir.resolve())) or not path.exists():
        raise ReceiptError("That receipt's file is missing.")
    return path, row["filename"], row["content_type"]


def delete(user_id: int, receipt_id: int) -> int:
    """Remove the row, and the file too once nothing else points at it."""
    with get_conn() as conn:
        row = conn.execute("SELECT stored_path FROM receipts WHERE id = ? AND user_id = ?",
                           (receipt_id, user_id)).fetchone()
        if not row:
            return 0
        removed = conn.execute("DELETE FROM receipts WHERE id = ? AND user_id = ?",
                               (receipt_id, user_id)).rowcount
        still_used = conn.execute("SELECT 1 FROM receipts WHERE stored_path = ?",
                                  (row["stored_path"],)).fetchone()
    if removed and not still_used:
        (config.settings.receipts_dir / row["stored_path"]).unlink(missing_ok=True)
    return removed


def attach(user_id: int, receipt_id: int, transaction_id: int | None) -> None:
    with get_conn() as conn:
        if transaction_id is not None:
            owns = conn.execute("SELECT 1 FROM transactions WHERE id = ? AND user_id = ?",
                                (transaction_id, user_id)).fetchone()
            if not owns:
                raise ReceiptError("That transaction isn't yours.")
        changed = conn.execute("UPDATE receipts SET transaction_id = ? WHERE id = ? AND user_id = ?",
                               (transaction_id, receipt_id, user_id)).rowcount
    if not changed:
        raise ReceiptError("Receipt not found.")


def listing(user_id: int, unattached_only: bool = False) -> list[dict]:
    where = "WHERE r.user_id = ?" + (" AND r.transaction_id IS NULL" if unattached_only else "")
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT r.id, r.filename, r.content_type, r.size_bytes, r.transaction_id,
                       r.parsed_total_cents, r.parsed_date, r.parsed_merchant, r.created_at,
                       t.merchant AS transaction_merchant, t.date AS transaction_date
                FROM receipts r LEFT JOIN transactions t ON t.id = r.transaction_id
                {where} ORDER BY r.id DESC""", (user_id,)).fetchall()
    return [{**dict(r),
             "parsed_total": an.money(r["parsed_total_cents"]) if r["parsed_total_cents"] else None,
             "parsed_total_text": an.fmt_cents(r["parsed_total_cents"]) if r["parsed_total_cents"] else None}
            for r in rows]


def suggest_matches(user_id: int, tx: pd.DataFrame, receipt_id: int, limit: int = 5) -> list[dict]:
    """Transactions a receipt probably belongs to, best first.

    Matching is on amount then date: an exact amount within a few days is almost always right, and
    a near amount is worth offering because tips and rounding move totals slightly.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT parsed_total_cents, parsed_date, parsed_merchant FROM receipts WHERE id = ? AND user_id = ?",
            (receipt_id, user_id)).fetchone()
    if not row:
        raise ReceiptError("Receipt not found.")
    total, when = row["parsed_total_cents"], row["parsed_date"]
    if not total and not when:
        return []

    candidates = tx[tx["amount_cents"] < 0].copy()
    if candidates.empty:
        return []
    candidates["abs_cents"] = -candidates["amount_cents"]

    if when:
        target = date.fromisoformat(when)
        candidates["day_gap"] = (candidates["date"].dt.date - target).map(lambda d: abs(d.days))
        candidates = candidates[candidates["day_gap"] <= MATCH_WINDOW_DAYS]
    else:
        candidates["day_gap"] = 0
    if candidates.empty:
        return []

    if total:
        candidates["amount_gap"] = (candidates["abs_cents"] - total).abs()
        # within 5% or ₹50-equivalent, whichever is larger — enough for a tip or a rounding difference
        tolerance = max(int(total * 0.05), 50_00)
        candidates = candidates[candidates["amount_gap"] <= tolerance]
    else:
        candidates["amount_gap"] = 0
    if candidates.empty:
        return []

    candidates = candidates.sort_values(["amount_gap", "day_gap"])
    out = []
    for r in candidates.head(limit).itertuples():
        exact = total is not None and r.amount_gap == 0
        out.append({
            "transaction_id": int(r.id), "date": r.date.date().isoformat(), "merchant": r.merchant,
            "amount": an.money(r.amount_cents), "amount_text": an.fmt_cents(-r.amount_cents),
            "category": r.category,
            "confidence": "exact" if exact and r.day_gap <= 1 else ("likely" if exact else "possible"),
            "why": ("Same amount, same day" if exact and r.day_gap == 0 else
                    f"Same amount, {r.day_gap} day(s) apart" if exact else
                    f"Within {an.fmt_cents(int(r.amount_gap))} of the receipt total"),
        })
    return out


def auto_attach(user_id: int, tx: pd.DataFrame) -> int:
    """Attach every unmatched receipt that has exactly one confident candidate. Returns the count."""
    attached = 0
    for r in listing(user_id, unattached_only=True):
        matches = suggest_matches(user_id, tx, r["id"], limit=2)
        confident = [m for m in matches if m["confidence"] == "exact"]
        # only when it's unambiguous: two same-amount charges on the same day is exactly the case
        # where guessing puts the receipt on the wrong one
        if len(confident) == 1:
            attach(user_id, r["id"], confident[0]["transaction_id"])
            attached += 1
    return attached


def coverage(user_id: int, tx: pd.DataFrame) -> dict:
    """How much of the tagged business spending actually has a receipt behind it."""
    business = query_df(
        """SELECT g.transaction_id FROM tax_tags g
           WHERE g.user_id = ? AND g.kind = 'business'""", (user_id,))
    if business.empty:
        return {"business_transactions": 0, "with_receipt": 0, "coverage_pct": None}
    with get_conn() as conn:
        have = {r[0] for r in conn.execute(
            "SELECT transaction_id FROM receipts WHERE user_id = ? AND transaction_id IS NOT NULL",
            (user_id,))}
    ids = set(business["transaction_id"])
    covered = len(ids & have)
    return {"business_transactions": len(ids), "with_receipt": covered,
            "coverage_pct": an.pct(covered, len(ids))}
