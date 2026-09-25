"""Accounts and sessions.

* Passwords: PBKDF2-HMAC-SHA256 (stdlib, 600k iterations, per-user salt), compared in constant time.
* Sessions: a random 256-bit token in an httpOnly, SameSite=Lax cookie. Only its SHA-256 is stored,
  so a leaked database can't be replayed as live sessions. `Authorization: Bearer <token>` also works
  for API clients.
* Brute force: failed sign-ins are rate-limited per email (in-process; use Redis when running several workers).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from .config import settings
from .db import get_conn

SESSION_COOKIE = "ledgerly_session"
PBKDF2_ITERATIONS = 600_000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_FAILURES, FAILURE_WINDOW_S = 5, 15 * 60


def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"pbkdf2_sha256${iterations}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        # still spend the hashing time so response timing doesn't reveal whether the account exists
        hashlib.pbkdf2_hmac("sha256", password.encode(), b"\0" * 16, PBKDF2_ITERATIONS)
        return False
    try:
        algo, iters, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    calc = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iters))
    return hmac.compare_digest(calc, base64.b64decode(digest))


def normalize_email(email: str) -> str:
    return email.strip().lower()


# ----------------------------------------------------------------------------- sessions

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


#: How stale `sessions.last_seen_at` may get before a request refreshes it. Writing on every request
#: would turn each read into a write; the device list only needs "active today", not the exact second.
LAST_SEEN_RESOLUTION = timedelta(minutes=5)

#: Public id of a session in the device list: a prefix of its hash. It names the row so it can be
#: revoked, but can't be turned back into a working cookie (it's a hash of a hash-preimage).
SESSION_ID_LEN = 16


def create_session(user_id: int, user_agent: str | None = None, ip: str | None = None) -> str:
    token = secrets.token_urlsafe(32)
    expires = _now() + timedelta(days=settings.session_days)
    with get_conn() as conn:
        conn.execute("INSERT INTO sessions (token_hash, user_id, expires_at, user_agent, ip, last_seen_at) VALUES (?,?,?,?,?,?)",
                     (_token_hash(token), user_id, _ts(expires), (user_agent or "")[:300] or None, ip, _ts(_now())))
    return token


def user_for_token(token: str | None) -> int | None:
    if not token:
        return None
    now = _now()
    with get_conn() as conn:
        row = conn.execute("SELECT user_id, expires_at, last_seen_at FROM sessions WHERE token_hash = ?",
                           (_token_hash(token),)).fetchone()
        if not row:
            return None
        if row["expires_at"] < _ts(now):
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
            return None
        if not row["last_seen_at"] or row["last_seen_at"] < _ts(now - LAST_SEEN_RESOLUTION):
            conn.execute("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (_ts(now), _token_hash(token)))
    return int(row["user_id"])


def session_id(token: str | None) -> str | None:
    return _token_hash(token)[:SESSION_ID_LEN] if token else None


def list_sessions(user_id: int, current_token: str | None) -> list[dict]:
    """Every live session for the device list, newest activity first, with the caller's own marked."""
    current = session_id(current_token)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT token_hash, user_agent, ip, created_at, last_seen_at, expires_at FROM sessions "
            "WHERE user_id = ? AND expires_at >= ? ORDER BY COALESCE(last_seen_at, created_at) DESC",
            (user_id, _ts(_now()))).fetchall()
    return [{"id": r["token_hash"][:SESSION_ID_LEN], "device": describe_agent(r["user_agent"]),
             "user_agent": r["user_agent"], "ip": r["ip"], "created_at": r["created_at"],
             "last_seen_at": r["last_seen_at"] or r["created_at"], "expires_at": r["expires_at"],
             "current": r["token_hash"][:SESSION_ID_LEN] == current} for r in rows]


def revoke_session(user_id: int, sid: str) -> bool:
    """Sign out one device. Scoped to the owner, so a guessed id can't touch anyone else's sessions."""
    if len(sid) != SESSION_ID_LEN or not all(c in "0123456789abcdef" for c in sid):
        return False
    with get_conn() as conn:
        return conn.execute("DELETE FROM sessions WHERE user_id = ? AND substr(token_hash, 1, ?) = ?",
                            (user_id, SESSION_ID_LEN, sid)).rowcount > 0


def revoke_other_sessions(user_id: int, keep_token: str | None) -> int:
    with get_conn() as conn:
        return conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                            (user_id, _token_hash(keep_token) if keep_token else "")).rowcount


_BROWSERS = (("Edg/", "Edge"), ("OPR/", "Opera"), ("SamsungBrowser", "Samsung Internet"), ("Firefox/", "Firefox"),
             ("Chrome/", "Chrome"), ("Safari/", "Safari"))
_SYSTEMS = (("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"), ("Windows", "Windows"),
            ("Mac OS X", "macOS"), ("CrOS", "ChromeOS"), ("Linux", "Linux"))


def describe_agent(user_agent: str | None) -> str:
    """'Chrome on Windows' from a User-Agent string — enough for someone to recognise their own device."""
    if not user_agent:
        return "Unknown device"
    browser = next((name for marker, name in _BROWSERS if marker in user_agent), None)
    system = next((name for marker, name in _SYSTEMS if marker in user_agent), None)
    if browser and system:
        return f"{browser} on {system}"
    return browser or system or user_agent.split("/")[0][:40] or "Unknown device"


# ----------------------------------------------------------------------------- security activity

def log_event(user_id: int, kind: str, ip: str | None = None, user_agent: str | None = None) -> None:
    with get_conn() as conn:
        conn.execute("INSERT INTO security_events (user_id, kind, ip, user_agent) VALUES (?,?,?,?)",
                     (user_id, kind, ip, (user_agent or "")[:300] or None))


def recent_events(user_id: int, limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT kind, ip, user_agent, created_at FROM security_events WHERE user_id = ? "
                            "ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()
    return [{**dict(r), "device": describe_agent(r["user_agent"])} for r in rows]


def delete_session(token: str | None) -> None:
    if token:
        with get_conn() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def purge_expired() -> dict:
    """Drop expired sessions and demo sandboxes older than the retention window (run at startup)."""
    now = _now()
    with get_conn() as conn:
        s = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(sep=" ", timespec="seconds"),)).rowcount
        conn.execute("DELETE FROM auth_tokens WHERE expires_at < ?", (now.isoformat(sep=" ", timespec="seconds"),))
        d = conn.execute("DELETE FROM users WHERE is_demo = 1 AND created_at < ?",
                         ((now - timedelta(days=settings.demo_retention_days)).isoformat(sep=" ", timespec="seconds"),)).rowcount
        # the activity log is for spotting something odd recently, not a permanent record of every IP
        conn.execute("DELETE FROM security_events WHERE created_at < ?", (_ts(now - timedelta(days=180)),))
    return {"sessions": s, "demo_users": d}


# ----------------------------------------------------------------------------- one-time email tokens

#: `mfa_login` is the short gap between a correct password and a correct authenticator code. It is
#: never emailed; the browser holds it just long enough to submit the code.
TOKEN_TTL = {"verify_email": timedelta(hours=24), "reset_password": timedelta(hours=1),
             "mfa_login": timedelta(minutes=5)}


def _ts(d: datetime) -> str:
    return d.isoformat(sep=" ", timespec="seconds")


def issue_email_token(user_id: int, purpose: str) -> str:
    """A fresh single-use token; any earlier unused token for the same purpose stops working."""
    token = secrets.token_urlsafe(32)
    with get_conn() as conn:
        conn.execute("DELETE FROM auth_tokens WHERE user_id = ? AND purpose = ?", (user_id, purpose))
        conn.execute("INSERT INTO auth_tokens (token_hash, user_id, purpose, expires_at) VALUES (?,?,?,?)",
                     (_token_hash(token), user_id, purpose, _ts(_now() + TOKEN_TTL[purpose])))
    return token


def consume_email_token(token: str, purpose: str) -> int | None:
    """Redeem a token exactly once. Returns the user id, or None if unknown, expired, used or for another purpose."""
    with get_conn() as conn:
        # the UPDATE is the atomic claim: two concurrent redemptions can't both succeed
        cur = conn.execute(
            "UPDATE auth_tokens SET used_at = ? WHERE token_hash = ? AND purpose = ? AND used_at IS NULL AND expires_at >= ?",
            (_ts(_now()), _token_hash(token), purpose, _ts(_now())))
        if not cur.rowcount:
            return None
        return int(conn.execute("SELECT user_id FROM auth_tokens WHERE token_hash = ?", (_token_hash(token),)).fetchone()[0])


def peek_token(token: str, purpose: str) -> int | None:
    """Whose token this is, without redeeming it — so a mistyped 2FA code doesn't burn the sign-in attempt."""
    with get_conn() as conn:
        row = conn.execute("SELECT user_id FROM auth_tokens WHERE token_hash = ? AND purpose = ? AND used_at IS NULL "
                           "AND expires_at >= ?", (_token_hash(token), purpose, _ts(_now()))).fetchone()
    return int(row["user_id"]) if row else None


def mark_verified(user_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE users SET email_verified_at = COALESCE(email_verified_at, ?) WHERE id = ?", (_ts(_now()), user_id))


def set_password(user_id: int, password: str) -> None:
    """Change a password and sign the user out everywhere (sessions and outstanding reset links)."""
    with get_conn() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM auth_tokens WHERE user_id = ? AND purpose = 'reset_password'", (user_id,))


# ----------------------------------------------------------------------------- rate limiting

_failures: dict[str, deque] = defaultdict(deque)


def _recent(key: str) -> deque:
    q = _failures[key]
    while q and q[0] < time.monotonic() - FAILURE_WINDOW_S:
        q.popleft()
    return q


def is_locked(email: str, limit: int = MAX_FAILURES) -> bool:
    return len(_recent(email)) >= limit


def record_failure(email: str) -> None:
    _recent(email).append(time.monotonic())


def clear_failures(email: str) -> None:
    _failures.pop(email, None)
