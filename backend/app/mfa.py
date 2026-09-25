"""Two-factor sign-in: TOTP authenticator codes (RFC 6238) and single-use recovery codes.

Standard library only, and compatible with Google Authenticator, Microsoft Authenticator, Authy,
1Password and every other app that reads an `otpauth://` link.

* Setup is two-step. `begin_setup` stores a fresh secret but leaves 2FA *off*; only `enable`, given a
  code the app actually produced, switches it on. So a half-finished setup can never lock anyone out.
* A code is accepted for the current 30-second step and one step either side (clock drift), and never
  twice: the last accepted step is stored, which stops a shoulder-surfed code being replayed.
* Recovery codes are shown once and stored as SHA-256 hashes; each one works exactly once.
* Wrong codes are rate-limited per user through the same limiter as passwords.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

from . import auth
from .db import get_conn

ISSUER = "Ledgerly"
STEP_S = 30
DIGITS = 6
DRIFT_STEPS = 1
RECOVERY_CODES = 10
MAX_CODE_FAILURES = 5


class MfaError(Exception):
    pass


# ----------------------------------------------------------------------------- TOTP core

def new_secret() -> str:
    """160 random bits, base32 without padding — the format authenticator apps expect."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def code_at(secret: str, step: int) -> str:
    """The code for one 30-second step (RFC 4226 HOTP with the step as the counter)."""
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP_S)


def match_step(secret: str, code: str, now: float | None = None) -> int | None:
    """The step `code` belongs to (within the drift window), or None. Compared in constant time."""
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != DIGITS:
        return None
    base = current_step(now)
    found = None
    for step in range(base - DRIFT_STEPS, base + DRIFT_STEPS + 1):
        if hmac.compare_digest(code_at(secret, step), code):
            found = step  # keep looping: same work whether or not the first step matched
    return found


def otpauth_uri(secret: str, account: str) -> str:
    label = quote(f"{ISSUER}:{account}")
    return f"otpauth://totp/{label}?" + urlencode(
        {"secret": secret, "issuer": ISSUER, "algorithm": "SHA1", "digits": DIGITS, "period": STEP_S})


# ----------------------------------------------------------------------------- recovery codes

def _normalise_recovery(code: str) -> str:
    return "".join(ch for ch in code.lower() if ch.isalnum())


def _hash_recovery(code: str) -> str:
    return hashlib.sha256(_normalise_recovery(code).encode()).hexdigest()


def _new_recovery_codes() -> list[str]:
    # 10 lowercase base32 characters, shown as xxxxx-xxxxx: easy to read aloud, 50 bits each
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    raw = ["".join(secrets.choice(alphabet) for _ in range(10)) for _ in range(RECOVERY_CODES)]
    return [f"{c[:5]}-{c[5:]}" for c in raw]


def _store_recovery_codes(conn, user_id: int) -> list[str]:
    codes = _new_recovery_codes()
    conn.execute("DELETE FROM mfa_recovery_codes WHERE user_id = ?", (user_id,))
    conn.executemany("INSERT INTO mfa_recovery_codes (user_id, code_hash) VALUES (?,?)",
                     [(user_id, _hash_recovery(c)) for c in codes])
    return codes


# ----------------------------------------------------------------------------- per-user state

def status(user_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT totp_enabled_at FROM users WHERE id = ?", (user_id,)).fetchone()
        left = conn.execute("SELECT COUNT(*) FROM mfa_recovery_codes WHERE user_id = ? AND used_at IS NULL",
                            (user_id,)).fetchone()[0]
    enabled = bool(row and row["totp_enabled_at"])
    return {"enabled": enabled, "enabled_at": row["totp_enabled_at"] if row else None,
            "recovery_codes_left": int(left) if enabled else 0}


def is_enabled(user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT totp_enabled_at FROM users WHERE id = ?", (user_id,)).fetchone()
    return bool(row and row["totp_enabled_at"])


def begin_setup(user_id: int) -> dict:
    """A new secret to scan. 2FA stays off until `enable` sees a working code from it."""
    with get_conn() as conn:
        row = conn.execute("SELECT email, totp_enabled_at, is_demo FROM users WHERE id = ?", (user_id,)).fetchone()
        if row["is_demo"]:
            raise MfaError("Demo sandboxes can't turn on two-factor sign-in. Create an account first.")
        if row["totp_enabled_at"]:
            raise MfaError("Two-factor sign-in is already on. Turn it off first to move it to a new device.")
        secret = new_secret()
        conn.execute("UPDATE users SET totp_secret = ?, totp_last_step = NULL WHERE id = ?", (secret, user_id))
    return {"secret": secret, "otpauth_uri": otpauth_uri(secret, row["email"] or f"user-{user_id}")}


def _check_rate(user_id: int) -> str:
    key = f"mfa:{user_id}"
    if auth.is_locked(key, limit=MAX_CODE_FAILURES):
        raise MfaError("Too many incorrect codes. Try again in 15 minutes.")
    return key


def _accept_totp(conn, user_id: int, secret: str, last_step: int | None, code: str) -> bool:
    step = match_step(secret, code)
    if step is None or (last_step is not None and step <= last_step):
        return False
    # conditional update = atomic claim, so two requests racing with the same code can't both pass
    return conn.execute(
        "UPDATE users SET totp_last_step = ? WHERE id = ? AND (totp_last_step IS NULL OR totp_last_step < ?)",
        (step, user_id, step)).rowcount == 1


def enable(user_id: int, code: str) -> list[str]:
    """Switch 2FA on after proving the app works. Returns the recovery codes — the only time they're shown."""
    key = _check_rate(user_id)
    with get_conn() as conn:
        row = conn.execute("SELECT totp_secret, totp_enabled_at, totp_last_step FROM users WHERE id = ?",
                           (user_id,)).fetchone()
        if row["totp_enabled_at"]:
            raise MfaError("Two-factor sign-in is already on.")
        if not row["totp_secret"]:
            raise MfaError("Start setup first, then enter the code your app shows.")
        if not _accept_totp(conn, user_id, row["totp_secret"], row["totp_last_step"], code):
            auth.record_failure(key)
            raise MfaError("That code didn't match. Check your phone's clock is set automatically and try the newest code.")
        conn.execute("UPDATE users SET totp_enabled_at = datetime('now') WHERE id = ?", (user_id,))
        codes = _store_recovery_codes(conn, user_id)
    auth.clear_failures(key)
    return codes


def verify(user_id: int, code: str) -> str:
    """Check a sign-in code: an authenticator code or an unused recovery code.

    Returns which one worked ('totp' | 'recovery'), or raises MfaError. Wrong guesses count towards
    the lockout, so six-digit codes can't be brute-forced inside their 90-second window.
    """
    key = _check_rate(user_id)
    with get_conn() as conn:
        row = conn.execute("SELECT totp_secret, totp_enabled_at, totp_last_step FROM users WHERE id = ?",
                           (user_id,)).fetchone()
        if not row or not row["totp_enabled_at"]:
            raise MfaError("Two-factor sign-in isn't on for this account.")
        digits = "".join(ch for ch in code if ch.isdigit())
        if len(digits) == DIGITS and len(_normalise_recovery(code)) == DIGITS:
            if _accept_totp(conn, user_id, row["totp_secret"], row["totp_last_step"], digits):
                auth.clear_failures(key)
                return "totp"
        else:
            used = conn.execute(
                "UPDATE mfa_recovery_codes SET used_at = datetime('now') WHERE user_id = ? AND code_hash = ? AND used_at IS NULL",
                (user_id, _hash_recovery(code))).rowcount
            if used:
                auth.clear_failures(key)
                return "recovery"
    auth.record_failure(key)
    raise MfaError("That code isn't right. Use the newest code from your app, or one of your recovery codes.")


def disable(user_id: int, code: str) -> None:
    """Turn 2FA off. Needs a working code, so a stolen session alone can't strip the second factor."""
    verify(user_id, code)
    with get_conn() as conn:
        conn.execute("UPDATE users SET totp_secret = NULL, totp_enabled_at = NULL, totp_last_step = NULL WHERE id = ?",
                     (user_id,))
        conn.execute("DELETE FROM mfa_recovery_codes WHERE user_id = ?", (user_id,))


def regenerate_recovery_codes(user_id: int, code: str) -> list[str]:
    verify(user_id, code)
    with get_conn() as conn:
        return _store_recovery_codes(conn, user_id)
