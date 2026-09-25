"""Sign in with Google — OAuth 2.0 authorization-code flow with PKCE (OpenID Connect).

1. /api/auth/google/start: random `state`, PKCE `code_verifier` and `nonce` go into a short-lived httpOnly
   cookie; the browser is redirected to Google.
2. /api/auth/google/callback: `state` must match the cookie (CSRF), the code is exchanged server-to-server
   with the verifier, and the ID token's issuer, audience, expiry, nonce and `email_verified` are checked.
   The token comes straight from Google's token endpoint over TLS, which OIDC Core 3.1.3.7 allows in place
   of a signature check.
3. The Google account is matched by its stable `sub`, then by verified email (see `link_or_create_user`).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode

import httpx

from . import auth, config
from .db import get_conn
from .net import TLS_VERIFY

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
FLOW_COOKIE = "ledgerly_oauth"
FLOW_TTL_S = 600


class GoogleAuthError(Exception):
    pass


def redirect_uri() -> str:
    return f"{config.settings.app_url}/api/auth/google/callback"


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def start(next_path: str) -> tuple[str, str]:
    """Returns (Google authorization URL, value for the flow cookie)."""
    state, verifier, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(48), secrets.token_urlsafe(24)
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    url = AUTH_URL + "?" + urlencode({
        "client_id": config.settings.google_client_id, "redirect_uri": redirect_uri(), "response_type": "code",
        "scope": "openid email profile", "state": state, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account",
    })
    cookie = ".".join([state, verifier, nonce, _b64url(next_path.encode())])
    return url, cookie


def parse_flow_cookie(cookie: str | None, state: str | None) -> tuple[str, str, str]:
    """(verifier, nonce, next_path) if the callback's state matches the cookie set at start."""
    try:
        c_state, verifier, nonce, next_b64 = (cookie or "").split(".")
        next_path = base64.urlsafe_b64decode(next_b64 + "=" * (-len(next_b64) % 4)).decode()
    except ValueError as exc:
        raise GoogleAuthError("Sign-in session expired. Please try again.") from exc
    if not state or not hmac.compare_digest(c_state, state):
        raise GoogleAuthError("Sign-in request didn't match. Please try again.")
    return verifier, nonce, next_path


def exchange_code(code: str, verifier: str) -> dict:
    """Server-to-server code exchange. Returns Google's token response (contains `id_token`)."""
    s = config.settings
    try:
        r = httpx.post(TOKEN_URL, timeout=10, verify=TLS_VERIFY, data={
            "code": code, "client_id": s.google_client_id, "client_secret": s.google_client_secret,
            "redirect_uri": redirect_uri(), "grant_type": "authorization_code", "code_verifier": verifier,
        })
    except httpx.HTTPError as exc:
        raise GoogleAuthError("Couldn't reach Google. Please try again.") from exc
    if r.status_code != 200:
        raise GoogleAuthError("Google rejected the sign-in. Please try again.")
    return r.json()


def verify_id_token(id_token: str, nonce: str) -> dict:
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as exc:
        raise GoogleAuthError("Invalid response from Google.") from exc
    if claims.get("iss") not in ISSUERS or claims.get("aud") != config.settings.google_client_id:
        raise GoogleAuthError("Invalid response from Google.")
    if int(claims.get("exp", 0)) < time.time():
        raise GoogleAuthError("Google sign-in expired. Please try again.")
    if not claims.get("nonce") or not hmac.compare_digest(str(claims["nonce"]), nonce):
        raise GoogleAuthError("Sign-in request didn't match. Please try again.")
    if not claims.get("sub") or not claims.get("email") or claims.get("email_verified") not in (True, "true"):
        raise GoogleAuthError("Your Google account's email isn't verified.")
    return claims


def link_or_create_user(sub: str, email: str, name: str | None) -> int:
    """Find the user for a Google identity, linking or creating as needed.

    Linking to an existing email account whose address was never verified wipes that account's password
    and sessions first. Otherwise someone who pre-registered the victim's email could keep a back door
    into the account the victim then uses via Google.
    """
    email = auth.normalize_email(email)
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE google_sub = ?", (sub,)).fetchone()
        if row:
            return int(row["id"])
        row = conn.execute("SELECT id, email_verified_at FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            if row["email_verified_at"] is None:
                conn.execute("UPDATE users SET password_hash = NULL WHERE id = ?", (row["id"],))
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
            conn.execute("UPDATE users SET google_sub = ?, email_verified_at = COALESCE(email_verified_at, datetime('now')) "
                         "WHERE id = ?", (sub, row["id"]))
            return int(row["id"])
        return int(conn.execute(
            "INSERT INTO users (name, email, google_sub, email_verified_at, plan) VALUES (?,?,?, datetime('now'), 'free')",
            ((name or email.split("@")[0])[:80], email, sub)).lastrowid)


def complete(code: str, verifier: str, nonce: str) -> int:
    tokens = exchange_code(code, verifier)
    claims = verify_id_token(tokens.get("id_token", ""), nonce)
    return link_or_create_user(claims["sub"], claims["email"], claims.get("name"))
