"""Data export and account deletion.

Two things a product handling someone's bank history has to be able to do on request, and which are
far cheaper to build now than to retrofit under a deadline.

`export` walks the schema rather than a hand-written list, so a table added later is included
automatically — a list would silently go stale and hand people an incomplete copy of their data.
`delete_account` relies on `ON DELETE CASCADE`, which every user-scoped table already declares, and
then verifies that nothing was left behind rather than assuming.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .config import settings
from .db import get_conn

log = logging.getLogger("ledgerly.privacy")

#: Columns never included in an export: secrets, or hashes that only exist to verify secrets.
REDACTED = {"password_hash", "token_hash", "stored_path", "totp_secret", "totp_last_step", "code_hash"}

#: Tables that belong to the operator, not the person, and so aren't part of their data.
SKIP_TABLES = {"billing_plans", "billing_events", "coupons", "sqlite_sequence"}


def _user_tables() -> list[tuple[str, str]]:
    """(table, user column) for every table keyed to a user, read from the live schema."""
    out = []
    with get_conn() as conn:
        tables = [r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            if table in SKIP_TABLES:
                continue
            columns = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for candidate in ("user_id", "referee_id", "id" if table == "users" else None):
                if candidate and candidate in columns:
                    out.append((table, candidate))
                    break
    return sorted(out)


def export(user_id: int) -> dict:
    """Everything stored about one person, as JSON-ready data."""
    data: dict = {}
    with get_conn() as conn:
        for table, column in _user_tables():
            rows = conn.execute(f"SELECT * FROM {table} WHERE {column} = ?", (user_id,)).fetchall()
            if rows:
                data[table] = [{k: v for k, v in dict(r).items() if k not in REDACTED} for r in rows]
    receipt_files = [r["filename"] for r in data.get("receipts", [])]
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app": "Ledgerly",
        "note": ("Everything Ledgerly stores about your account. Password hashes and session tokens "
                 "are deliberately excluded — they are secrets, not data about you. Uploaded receipt "
                 "files are listed by name; download them individually from the Receipts page."),
        "receipt_files": receipt_files,
        "data": data,
    }


def export_json(user_id: int) -> str:
    return json.dumps(export(user_id), indent=2, default=str)


def delete_account(user_id: int) -> dict:
    """Erase the account and everything belonging to it, including uploaded files.

    Verified rather than assumed: the cascade is re-checked afterwards and anything still holding a
    row is reported, so a table added without `ON DELETE CASCADE` surfaces as a failure instead of
    leaving orphaned financial data behind.
    """
    tables = _user_tables()
    receipts_dir = settings.receipts_dir / str(user_id)

    with get_conn() as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    leftovers = {}
    with get_conn() as conn:
        for table, column in tables:
            if table == "users":
                continue
            remaining = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?",
                                     (user_id,)).fetchone()[0]
            if remaining:
                leftovers[table] = int(remaining)
        # a table without ON DELETE CASCADE would leave rows here; remove them explicitly and say so
        for table in leftovers:
            column = dict(tables)[table]
            conn.execute(f"DELETE FROM {table} WHERE {column} = ?", (user_id,))

    removed_files = 0
    if receipts_dir.exists():
        for path in receipts_dir.iterdir():
            path.unlink(missing_ok=True)
            removed_files += 1
        receipts_dir.rmdir()

    if leftovers:
        log.warning("Deleting user %s left rows in %s — those tables need ON DELETE CASCADE",
                    user_id, ", ".join(leftovers))
    return {"deleted": True, "receipt_files_removed": removed_files,
            "cleaned_without_cascade": leftovers}
