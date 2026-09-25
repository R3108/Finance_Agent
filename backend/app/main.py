"""FastAPI application exposing analytics, CRUD and the agent to the Next.js UI."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date

import pandas as pd
from urllib.parse import urlencode

from fastapi import BackgroundTasks, Cookie, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import net  # noqa: F401  (must be first: verify outbound HTTPS with the OS trust store)
from . import analytics as an
from . import admin, afford, auth, automations, billing, capture, google_oauth, growth, household, mailer
from . import mfa, money
from . import planning as pl
from . import plans
from . import privacy, receipts, splits, tax
from .agent import run_agent
from .categorizer import CATEGORIES
from .config import settings
from .db import get_conn, init_db
from .ingest import insert_transactions, parse_csv, recategorize_all
from .services import UserData
from .synthetic import create_demo_user, seed_demo


#: Background jobs, each idempotent and safe to run from any process or as often as you like. The
#: loop ticks every 30 minutes; how often work actually happens is decided inside each job
#: (`automations.SWEEP_INTERVAL`, the digest period, the reminder's own "already sent" check).
BACKGROUND_JOBS = (
    ("billing", billing.send_renewal_reminders),
    ("automations", automations.run_due),
    ("digests", automations.send_due_digests),
)


async def _background_loop() -> None:
    while True:
        for name, job in BACKGROUND_JOBS:
            try:
                await asyncio.to_thread(job)
            except Exception:  # one failing job must never stop the others, or kill the loop
                logging.getLogger(f"ledgerly.{name}").exception("Background job %r failed", name)
        await asyncio.sleep(1800)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    auth.purge_expired()
    task = asyncio.create_task(_background_loop())
    yield
    task.cancel()


app = FastAPI(title="Ledgerly API", version="1.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

access_log = logging.getLogger("ledgerly.access")

#: Sent on every response. The API serves JSON and files, never pages, so framing and sniffing are
#: off outright; `no-store` keeps bank data out of shared and browser caches.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cache-Control": "no-store",
}


def client_ip(request: Request) -> str | None:
    """The caller's address, read from X-Forwarded-For only as far as our own proxies vouch for it."""
    hops = settings.trusted_proxy_hops
    forwarded = [p.strip() for p in (request.headers.get("x-forwarded-for") or "").split(",") if p.strip()]
    if hops > 0 and len(forwarded) >= hops:
        return forwarded[-hops]
    return request.client.host if request.client else None


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Request id, timing, security headers, and a clean 500 that support can trace.

    The id is taken from an upstream `X-Request-ID` when it looks sane (so one id follows a request
    through the proxy and into our logs) and echoed back, so a user can quote it in a bug report.
    Only the path is logged, never the query string — capture tokens and reset links can ride there.
    """
    incoming = request.headers.get("x-request-id", "")
    rid = incoming if 8 <= len(incoming) <= 64 and incoming.replace("-", "").isalnum() else uuid.uuid4().hex
    request.state.request_id = rid
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logging.getLogger("ledgerly.error").exception("Unhandled error rid=%s %s %s", rid, request.method, request.url.path)
        response = JSONResponse(status_code=500, content={
            "detail": f"Something went wrong on our side. If it keeps happening, quote reference {rid[:12]}.",
            "request_id": rid})
    ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = rid
    for key, value in SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)
    if settings.cookie_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.url.path != "/api/health/live":   # probes would drown the log
        if settings.log_format == "json":
            access_log.info(json.dumps({"rid": rid, "method": request.method, "path": request.url.path,
                                        "status": response.status_code, "ms": round(ms, 1), "ip": client_ip(request)}))
        else:
            access_log.info("%s %s %s %.0fms rid=%s", request.method, request.url.path, response.status_code, ms, rid)
    return response


def ip_limit(request: Request, name: str, limit: int) -> None:
    """Per-IP cap over the auth limiter's 15-minute window: slows credential stuffing and signup spam
    that rotates email addresses, which per-email limits can't see."""
    if not settings.ip_rate_limits:
        return
    key = f"ip:{name}:{client_ip(request)}"
    if auth.is_locked(key, limit=limit):
        raise HTTPException(429, "Too many requests from your network. Try again in a few minutes.")
    auth.record_failure(key)


@app.exception_handler(plans.PlanRequired)
def plan_required(_, exc: plans.PlanRequired):
    return JSONResponse(status_code=402, content={"detail": str(exc), "feature": exc.feature, "plan": exc.plan})


def session_token(ledgerly_session: str | None = Cookie(default=None),
                  authorization: str | None = Header(default=None)) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ledgerly_session


def current_user(token: str | None = Depends(session_token),
                 scope: str = Query("personal", pattern="^(personal|household)$")) -> UserData:
    """The signed-in user (session cookie or bearer token). Every query downstream is scoped to this id.

    `?scope=household` widens that to the whole household's ledger. Declaring it here means every
    data route supports the toggle without repeating the parameter, and `UserData` ignores it for
    anyone who isn't in a household.
    """
    user_id = auth.user_for_token(token)
    if user_id is None:
        raise HTTPException(401, "Not signed in")
    u = UserData(user_id, scope=scope)
    # Narrative copy built deep in analytics/planning (insight titles, budget rationales) formats from
    # this contextvar, so set it once here rather than threading a currency argument everywhere.
    money.set_currency(u.currency)
    return u


def require(feature: str):
    """Route dependency: the current user, if their plan includes `feature` (else HTTP 402)."""
    def dep(u: UserData = Depends(current_user)) -> UserData:
        if not plans.allows(u.plan, feature):
            raise plans.PlanRequired(feature, u.plan)
        return u
    return dep


def cents(dollars: float) -> int:
    return int(round(dollars * 100))


# ----------------------------------------------------------------------------- meta

@app.get("/api/health")
def health():
    return {"ok": True, "llm_enabled": settings.llm_enabled, "model": settings.openai_model}


@app.get("/api/health/live")
def health_live():
    """Liveness: the process is up and serving. Deliberately touches nothing else, so a slow database
    makes the instance unready rather than getting it restarted in a loop."""
    return {"ok": True}


@app.get("/api/health/ready")
def health_ready():
    """Readiness: the database answers and has the schema. 503 takes the instance out of rotation."""
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1 FROM users LIMIT 1").fetchall()
    except Exception as exc:  # noqa: BLE001 — any failure here means "don't send traffic"
        return JSONResponse(status_code=503, content={"ok": False, "database": f"unavailable: {type(exc).__name__}"})
    return {"ok": True, "database": "ok", "version": app.version, "email": mailer.delivery_mode(),
            "billing": settings.billing_enabled, "llm": settings.llm_enabled}


@app.get("/api/me")
def me(u: UserData = Depends(current_user)):
    _ = u.plan  # refresh first: an expired prepaid pass drops to Free before we report the plan
    with get_conn() as conn:
        row = dict(conn.execute("SELECT id, name, email, plan, currency, is_demo, email_verified_at, upi_vpa, "
                                "password_hash IS NOT NULL AS has_password, google_sub IS NOT NULL AS google_linked, "
                                "totp_enabled_at IS NOT NULL AS mfa_enabled "
                                "FROM users WHERE id = ?", (u.user_id,)).fetchone())
    verified = row.pop("email_verified_at") is not None
    c = money.get(row["currency"])
    # u.plan, not the raw column: a member of a Family household inherits the owner's plan while
    # their own row stays 'free', and the UI gates every paid feature on what this returns
    row["plan"] = u.plan
    return {**row, "currency": c.code, "is_demo": bool(row["is_demo"]), "email_verified": verified,
            "has_password": bool(row["has_password"]), "google_linked": bool(row["google_linked"]),
            "mfa_enabled": bool(row["mfa_enabled"]),
            # the browser formats with Intl using these, so server and client agree on grouping
            "currency_symbol": c.symbol.strip(), "currency_locale": c.locale,
            # drives whether the Operations link is shown; every admin route re-checks server-side
            "is_admin": admin.is_admin(row.get("email")),
            "in_household": u.membership is not None,
            "as_of": u.as_of.isoformat(), "transactions": len(u.tx), "llm_enabled": settings.llm_enabled}


@app.get("/api/currencies")
def currencies():
    return {"currencies": money.catalog(), "default": money.DEFAULT_CODE}


class ProfileIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    currency: str | None = Field(default=None, max_length=3)
    #: The user's own UPI id, put into split reminders so friends can pay them back. "" clears it.
    upi_vpa: str | None = Field(default=None, max_length=100)


@app.patch("/api/me")
def update_profile(body: ProfileIn, u: UserData = Depends(current_user)):
    """Change display name or currency. Currency is presentation only: stored minor units never move."""
    if body.currency is not None and body.currency.upper() not in money.CURRENCIES:
        raise HTTPException(422, f"Unsupported currency. Choose one of: {', '.join(money.CURRENCIES)}")
    if body.upi_vpa is not None:
        vpa = splits.valid_vpa(body.upi_vpa)
        with get_conn() as conn:
            conn.execute("UPDATE users SET upi_vpa = ? WHERE id = ?", (vpa, u.user_id))
    with get_conn() as conn:
        if body.name is not None:
            conn.execute("UPDATE users SET name = ? WHERE id = ?", (body.name.strip(), u.user_id))
        if body.currency is not None:
            conn.execute("UPDATE users SET currency = ? WHERE id = ?", (body.currency.upper(), u.user_id))
    return {"ok": True}


# ----------------------------------------------------------------------------- auth

class SignupIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=128)
    sample_data: bool = False
    currency: str = Field(default=money.DEFAULT_CODE, max_length=3)
    #: A referral code from the link they arrived on. Rewards are paid once they verify their email.
    ref: str | None = Field(default=None, max_length=32)


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


def _start_session(response: Response, user_id: int, request: Request | None = None,
                   event: str | None = "login") -> dict:
    ua = request.headers.get("user-agent") if request else None
    ip = client_ip(request) if request else None
    response.set_cookie(auth.SESSION_COOKIE, auth.create_session(user_id, ua, ip), max_age=settings.session_days * 86400,
                        httponly=True, samesite="lax", secure=settings.cookie_secure, path="/")
    if event:
        auth.log_event(user_id, event, ip, ua)
    return {"ok": True, "user_id": user_id}


def _second_factor_gate(user_id: int) -> dict | None:
    """If the account has 2FA, the half-way answer instead of a session: a 5-minute token to redeem with a code."""
    if not mfa.is_enabled(user_id):
        return None
    return {"ok": True, "mfa_required": True, "mfa_token": auth.issue_email_token(user_id, "mfa_login")}


def _send_verification(background: BackgroundTasks, user_id: int, email: str, name: str) -> None:
    token = auth.issue_email_token(user_id, "verify_email")
    background.add_task(mailer.send_verification, email, name, f"{settings.app_url}/verify-email?token={token}")


@app.post("/api/auth/signup", status_code=201)
def signup(body: SignupIn, request: Request, response: Response, background: BackgroundTasks):
    ip_limit(request, "signup", 10)
    email = auth.normalize_email(body.email)
    if not auth.EMAIL_RE.match(email):
        raise HTTPException(422, "Enter a valid email address")
    code = money.normalize(body.currency)
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise HTTPException(409, "An account with this email already exists. Sign in instead.")
        user_id = conn.execute(
            "INSERT INTO users (name, email, password_hash, plan, currency) VALUES (?,?,?, 'free', ?)",
            (body.name.strip(), email, auth.hash_password(body.password), code)).lastrowid
    if body.sample_data:
        seed_demo(user_id, currency=code)
    # install the starter watchers now, so the background sweep works even if they never open
    # the Automations page
    automations.ensure_default_rules(user_id)
    growth.attribute_referral(user_id, body.ref)
    growth.track(user_id, "signed_up", {"currency": code, "sample_data": body.sample_data,
                                        "referred": bool(body.ref)})
    _send_verification(background, user_id, email, body.name.strip())
    return _start_session(response, user_id, request, "signup")


@app.get("/api/auth/providers")
def auth_providers():
    return {"google": settings.google_enabled, "email_delivery": mailer.delivery_mode()}


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)


@app.post("/api/auth/verify")
def verify_email(body: TokenIn):
    user_id = auth.consume_email_token(body.token, "verify_email")
    if user_id is None:
        raise HTTPException(400, "This link is invalid or has expired. Request a new one from the banner in the app.")
    auth.mark_verified(user_id)
    # a referral pays out only now: a verified address is what makes throwaway signups pointless
    reward = growth.complete_referral(user_id)
    growth.track(user_id, "email_verified")
    return {"ok": True, "referral_reward_days": reward["days"] if reward else None}


@app.post("/api/auth/verify/resend")
def resend_verification(background: BackgroundTasks, u: UserData = Depends(current_user)):
    with get_conn() as conn:
        row = conn.execute("SELECT name, email, email_verified_at FROM users WHERE id = ?", (u.user_id,)).fetchone()
    if not row["email"] or row["email_verified_at"]:
        raise HTTPException(400, "This email is already verified")
    key = f"verify:{u.user_id}"
    if auth.is_locked(key, limit=3):
        raise HTTPException(429, "Too many emails sent. Try again in 15 minutes.")
    auth.record_failure(key)
    _send_verification(background, u.user_id, row["email"], row["name"])
    return {"ok": True}


class ForgotIn(BaseModel):
    email: str = Field(max_length=254)


@app.post("/api/auth/forgot")
def forgot_password(body: ForgotIn, request: Request, background: BackgroundTasks):
    """Always answers the same way so it can't be used to discover which emails have accounts."""
    ip_limit(request, "forgot", 20)
    email = auth.normalize_email(body.email)
    key = f"forgot:{email}"
    if auth.is_locked(key, limit=3):
        raise HTTPException(429, "Too many reset requests. Try again in 15 minutes.")
    auth.record_failure(key)
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE email = ? AND is_demo = 0", (email,)).fetchone()
    if row:
        token = auth.issue_email_token(int(row["id"]), "reset_password")
        background.add_task(mailer.send_password_reset, email, f"{settings.app_url}/reset-password?token={token}")
    return {"ok": True, "message": "If an account exists for that email, a reset link is on its way."}


class ResetIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(min_length=8, max_length=128)


@app.post("/api/auth/reset")
def reset_password(body: ResetIn, request: Request, response: Response, background: BackgroundTasks):
    user_id = auth.consume_email_token(body.token, "reset_password")
    if user_id is None:
        raise HTTPException(400, "This reset link is invalid, expired or already used. Request a new one.")
    auth.set_password(user_id, body.password)
    auth.mark_verified(user_id)  # they just proved they own the inbox
    with get_conn() as conn:
        email = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()["email"]
    auth.clear_failures(email)
    auth.log_event(user_id, "password_reset", client_ip(request), request.headers.get("user-agent"))
    background.add_task(mailer.send_password_changed, email)
    # Owning the inbox resets the password, but it isn't the second factor: with 2FA on, the
    # authenticator code is still required before a session is issued.
    if gate := _second_factor_gate(user_id):
        return gate
    return _start_session(response, user_id, request)


class PasswordChangeIn(BaseModel):
    current_password: str | None = Field(None, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


@app.post("/api/auth/password")
def change_password(body: PasswordChangeIn, request: Request, response: Response, background: BackgroundTasks,
                    u: UserData = Depends(current_user)):
    """Change (or, for Google-only accounts, create) a password. Signs out every other session."""
    with get_conn() as conn:
        row = conn.execute("SELECT email, password_hash, is_demo FROM users WHERE id = ?", (u.user_id,)).fetchone()
    if row["is_demo"]:
        raise HTTPException(400, "Demo sandboxes don't have passwords. Create an account instead.")
    if row["password_hash"] and not auth.verify_password(body.current_password or "", row["password_hash"]):
        raise HTTPException(400, "Your current password is incorrect")
    auth.set_password(u.user_id, body.new_password)
    background.add_task(mailer.send_password_changed, row["email"])
    return _start_session(response, u.user_id, request, "password_changed")


GOOGLE_UNAVAILABLE = "Google sign-in is currently unavailable. Please use email instead."


@app.get("/api/auth/google/start")
def google_start(next: str = "/"):
    if not settings.google_enabled:  # keys removed from .env: this is a full-page navigation, so go back politely
        return RedirectResponse(f"{settings.app_url}/login?{urlencode({'error': GOOGLE_UNAVAILABLE})}", status_code=302)
    url, flow = google_oauth.start(_safe_next(next))
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(google_oauth.FLOW_COOKIE, flow, max_age=google_oauth.FLOW_TTL_S, httponly=True, samesite="lax",
                    secure=settings.cookie_secure, path="/api/auth/google")
    return resp


@app.get("/api/auth/google/callback")
def google_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None,
                    ledgerly_oauth: str | None = Cookie(default=None)):
    def fail(msg: str) -> RedirectResponse:
        resp = RedirectResponse(f"{settings.app_url}/login?{urlencode({'error': msg})}", status_code=302)
        resp.delete_cookie(google_oauth.FLOW_COOKIE, path="/api/auth/google")
        return resp

    if not settings.google_enabled:
        return fail(GOOGLE_UNAVAILABLE)
    if error:  # e.g. the user pressed Cancel on Google's screen
        return fail("Google sign-in was cancelled.")
    try:
        verifier, nonce, next_path = google_oauth.parse_flow_cookie(ledgerly_oauth, state)
        if not code:
            raise google_oauth.GoogleAuthError("Google didn't return a sign-in code. Please try again.")
        user_id = google_oauth.complete(code, verifier, nonce)
    except google_oauth.GoogleAuthError as exc:
        return fail(str(exc))
    if gate := _second_factor_gate(user_id):
        # Google vouches for the first factor only; the sign-in page collects the authenticator code
        resp = RedirectResponse(f"{settings.app_url}/login?" + urlencode(
            {"mfa_token": gate["mfa_token"], "next": _safe_next(next_path)}), status_code=302)
        resp.delete_cookie(google_oauth.FLOW_COOKIE, path="/api/auth/google")
        return resp
    resp = RedirectResponse(f"{settings.app_url}{_safe_next(next_path)}", status_code=302)
    resp.delete_cookie(google_oauth.FLOW_COOKIE, path="/api/auth/google")
    _start_session(resp, user_id, request, "login_google")
    return resp


def _safe_next(path: str) -> str:
    """Only same-site relative paths, so ?next= can't bounce users to another domain."""
    return path if path.startswith("/") and not path.startswith("//") and "\\" not in path else "/"


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    ip_limit(request, "login", 30)
    email = auth.normalize_email(body.email)
    if auth.is_locked(email):
        raise HTTPException(429, "Too many failed attempts. Try again in 15 minutes.")
    with get_conn() as conn:
        row = conn.execute("SELECT id, password_hash FROM users WHERE email = ?", (email,)).fetchone()
    if not auth.verify_password(body.password, row["password_hash"] if row else None):
        auth.record_failure(email)
        if row:
            auth.log_event(int(row["id"]), "login_failed", client_ip(request), request.headers.get("user-agent"))
        raise HTTPException(401, "Incorrect email or password")
    auth.clear_failures(email)
    if gate := _second_factor_gate(int(row["id"])):
        return gate
    return _start_session(response, int(row["id"]), request)


class MfaLoginIn(BaseModel):
    mfa_token: str = Field(min_length=10, max_length=200)
    code: str = Field(min_length=6, max_length=20)


@app.post("/api/auth/login/mfa")
def login_second_factor(body: MfaLoginIn, request: Request, response: Response, background: BackgroundTasks):
    """Step two of signing in with 2FA on: the token from step one plus an authenticator or recovery code."""
    user_id = auth.peek_token(body.mfa_token, "mfa_login")
    if user_id is None:
        raise HTTPException(400, "This sign-in attempt expired. Enter your password again.")
    try:
        used = mfa.verify(user_id, body.code)
    except mfa.MfaError as exc:
        raise HTTPException(401, str(exc)) from exc
    if auth.consume_email_token(body.mfa_token, "mfa_login") is None:   # single use, even under a race
        raise HTTPException(400, "This sign-in attempt expired. Enter your password again.")
    result = _start_session(response, user_id, request, "login_recovery_code" if used == "recovery" else "login_2fa")
    if used == "recovery":
        left = mfa.status(user_id)["recovery_codes_left"]
        result["recovery_codes_left"] = left
        with get_conn() as conn:
            email = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()["email"]
        if email:
            background.add_task(mailer.send_security_notice, email, "A recovery code was used to sign in",
                                f"Someone signed in to your Ledgerly account with a recovery code. You have {left} left. "
                                "If this wasn't you, reset your password now and sign out other devices in Settings.")
    return result


@app.post("/api/auth/demo", status_code=201)
def demo_login(request: Request, response: Response, currency: str = Query(money.DEFAULT_CODE, max_length=3)):
    """Start a private demo sandbox seeded with synthetic data — nothing is shared between visitors.

    The currency picks the persona: rupee sandboxes get the India dataset (UPI descriptors, Indian
    merchants), everything else gets the US one.
    """
    ip_limit(request, "demo", 10)   # each sandbox seeds 24 months of data, so this is the costly endpoint
    return _start_session(response, create_demo_user(money.normalize(currency)), request, None)


@app.post("/api/auth/logout")
def logout(response: Response, token: str | None = Depends(session_token)):
    auth.delete_session(token)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"ok": True}


# ----------------------------------------------------------------------------- security: 2FA, devices, activity

@app.exception_handler(mfa.MfaError)
def mfa_error(_, exc: mfa.MfaError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/api/auth/security")
def security_overview(token: str | None = Depends(session_token), u: UserData = Depends(current_user)):
    """Two-factor status, signed-in devices and recent security activity for the Settings page."""
    return {"mfa": mfa.status(u.user_id), "sessions": auth.list_sessions(u.user_id, token),
            "events": auth.recent_events(u.user_id)}


@app.post("/api/auth/mfa/setup")
def mfa_setup(u: UserData = Depends(current_user)):
    """A fresh secret + otpauth link to scan. 2FA stays off until /enable sees a code from it."""
    return mfa.begin_setup(u.user_id)


class MfaCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=20)


def _event(request: Request, u: UserData, kind: str) -> None:
    auth.log_event(u.user_id, kind, client_ip(request), request.headers.get("user-agent"))


@app.post("/api/auth/mfa/enable")
def mfa_enable(body: MfaCodeIn, request: Request, background: BackgroundTasks, u: UserData = Depends(current_user)):
    codes = mfa.enable(u.user_id, body.code)
    _event(request, u, "mfa_enabled")
    with get_conn() as conn:
        email = conn.execute("SELECT email FROM users WHERE id = ?", (u.user_id,)).fetchone()["email"]
    if email:
        background.add_task(mailer.send_security_notice, email, "Two-factor sign-in is on",
                            "Two-factor sign-in was turned on for your Ledgerly account. From now on, signing in "
                            "needs a code from your authenticator app. Keep your recovery codes somewhere safe.")
    return {"ok": True, "recovery_codes": codes}


@app.post("/api/auth/mfa/disable")
def mfa_disable(body: MfaCodeIn, request: Request, background: BackgroundTasks, u: UserData = Depends(current_user)):
    mfa.disable(u.user_id, body.code)
    _event(request, u, "mfa_disabled")
    with get_conn() as conn:
        email = conn.execute("SELECT email FROM users WHERE id = ?", (u.user_id,)).fetchone()["email"]
    if email:
        background.add_task(mailer.send_security_notice, email, "Two-factor sign-in was turned off",
                            "Two-factor sign-in was turned off for your Ledgerly account. If this wasn't you, "
                            "reset your password now and sign out every other device from Settings.")
    return {"ok": True}


@app.post("/api/auth/mfa/recovery-codes")
def mfa_new_recovery_codes(body: MfaCodeIn, request: Request, u: UserData = Depends(current_user)):
    codes = mfa.regenerate_recovery_codes(u.user_id, body.code)
    _event(request, u, "recovery_codes_regenerated")
    return {"ok": True, "recovery_codes": codes}


@app.delete("/api/auth/sessions/{sid}")
def revoke_device(sid: str, request: Request, token: str | None = Depends(session_token),
                  u: UserData = Depends(current_user)):
    if sid == auth.session_id(token):
        raise HTTPException(400, "That's this device. Use Log out instead.")
    if not auth.revoke_session(u.user_id, sid):
        raise HTTPException(404, "That device is already signed out.")
    _event(request, u, "session_revoked")
    return {"ok": True}


@app.post("/api/auth/sessions/revoke-others")
def revoke_other_devices(request: Request, token: str | None = Depends(session_token),
                         u: UserData = Depends(current_user)):
    n = auth.revoke_other_sessions(u.user_id, token)
    if n:
        _event(request, u, "sessions_revoked")
    return {"ok": True, "signed_out": n}


@app.post("/api/demo/reset")
def reset_demo(u: UserData = Depends(current_user)):
    return seed_demo(u.user_id)


@app.get("/api/categories")
def categories():
    return CATEGORIES


# ----------------------------------------------------------------------------- dashboard & analytics

@app.get("/api/overview")
def overview(u: UserData = Depends(current_user)):
    return u.overview()


@app.get("/api/insights")
def get_insights(u: UserData = Depends(current_user)):
    return an.insights(u.tx, u.accounts, u.budgets, u.as_of)


@app.get("/api/spending/monthly")
def spending_monthly(months: int = Query(12, ge=1, le=36), u: UserData = Depends(current_user)):
    return an.monthly_summary(u.tx, months, u.as_of)


@app.get("/api/spending/categories")
def spending_categories(month: str | None = None, u: UserData = Depends(current_user)):
    return an.category_breakdown(u.tx, month, u.as_of)


@app.get("/api/spending/category-trend")
def category_trend(months: int = Query(6, ge=2, le=24), u: UserData = Depends(current_user)):
    window = [r["month"] for r in an.monthly_summary(u.tx, months, u.as_of)]
    sp = an.spending_frame(u.tx)
    piv = (sp[sp["month"].isin(window)].groupby(["month", "category"])["spend_cents"].sum()
           .unstack(fill_value=0).reindex(window, fill_value=0))
    return [{"month": m, **{c: an.money(v) for c, v in row.items()}} for m, row in piv.iterrows()]


@app.get("/api/spending/merchants")
def spending_merchants(start: str | None = None, end: str | None = None, limit: int = 10, u: UserData = Depends(current_user)):
    return an.top_merchants(u.tx, start, end, limit)


@app.get("/api/spending/weekday")
def spending_weekday(u: UserData = Depends(current_user)):
    return an.weekday_pattern(u.tx, as_of=u.as_of)


@app.get("/api/anomalies")
def anomalies(days: int = Query(180, ge=7, le=730), u: UserData = Depends(current_user)):
    return an.detect_anomalies(u.tx, u.as_of, days)


@app.get("/api/forecast")
def forecast(days: int = Query(60, ge=7, le=180), u: UserData = Depends(current_user)):
    return an.cashflow_forecast(u.tx, u.accounts, days, u.as_of)


@app.get("/api/safe-to-spend")
def get_safe_to_spend(savings_pct: float = Query(20, ge=0, le=80), u: UserData = Depends(current_user)):
    return an.safe_to_spend(u.tx, savings_pct, u.as_of)


@app.get("/api/health-score")
def get_health_score(u: UserData = Depends(current_user)):
    return an.health_score(u.tx, u.accounts, u.budgets, u.as_of)


@app.get("/api/report")
def monthly_report(month: str | None = None, u: UserData = Depends(current_user)):
    """Printable monthly digest — defaults to the last complete month."""
    month = month or an.complete_months(u.as_of, 1)[0]
    summary = next((r for r in an.monthly_summary(u.tx, 24, u.as_of) if r["month"] == month), None)
    if summary is None:
        raise HTTPException(404, "Month outside available history")
    y, m = map(int, month.split("-"))
    month_end = (pd.Timestamp(y, m, 1) + pd.offsets.MonthEnd(0)).date()
    return {
        "month": month, "summary": summary,
        "categories": an.category_breakdown(u.tx, month, u.as_of),
        "top_merchants": an.top_merchants(u.tx, f"{month}-01", month_end.isoformat(), 5),
        "budgets": an.budget_status(u.tx, u.budgets, month, u.as_of) if u.budgets else None,
        "subscriptions": {k: v for k, v in an.subscriptions_summary(u.tx, u.sub_statuses, month_end).items() if k != "items"},
        "anomalies": an.detect_anomalies(u.tx, month_end, 31),
    }


# ----------------------------------------------------------------------------- transactions

@app.get("/api/transactions")
def list_transactions(q: str | None = None, category: str | None = None, start: str | None = None,
                      end: str | None = None, limit: int = Query(50, le=500), offset: int = 0,
                      u: UserData = Depends(current_user)):
    return an.search_transactions(u.tx, q, category, start, end, None, None, limit, offset)


class TxnUpdate(BaseModel):
    category: str
    apply_to_merchant: bool = Field(False, description="Also create a rule so future & past charges from this merchant use this category")


@app.patch("/api/transactions/{txn_id}")
def update_transaction(txn_id: int, body: TxnUpdate, u: UserData = Depends(current_user)):
    if body.category not in CATEGORIES:
        raise HTTPException(400, f"Unknown category. Use one of {CATEGORIES}")
    with get_conn() as conn:
        row = conn.execute("SELECT merchant FROM transactions WHERE id = ? AND user_id = ?", (txn_id, u.user_id)).fetchone()
        if not row:
            raise HTTPException(404, "Transaction not found")
        conn.execute("UPDATE transactions SET category = ?, category_source = 'manual' WHERE id = ?", (body.category, txn_id))
        if body.apply_to_merchant:
            conn.execute(
                "INSERT INTO category_rules (user_id, pattern, category, priority) VALUES (?,?,?,200) "
                "ON CONFLICT(user_id, pattern) DO UPDATE SET category = excluded.category",
                (u.user_id, row["merchant"], body.category),
            )
    changed = recategorize_all(u.user_id) if body.apply_to_merchant else 0
    return {"ok": True, "also_updated": changed}


@app.post("/api/import")
async def import_csv(file: UploadFile = File(...), account_id: int | None = Form(None),
                     expenses_positive: bool = Form(False), use_ai: bool = Form(True),
                     u: UserData = Depends(current_user)):
    content = await file.read()
    if len(content) > 5_000_000:
        raise HTTPException(413, "File too large (5 MB max)")
    if account_id is not None and account_id not in set(u.accounts["id"].tolist()):
        raise HTTPException(400, "Unknown account")
    try:
        rows = parse_csv(content, account_id, expenses_positive)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    ai_allowed = plans.allows(u.plan, "ai_categorization")
    return {**insert_transactions(u.user_id, rows, use_llm=use_ai and ai_allowed),
            "ai_categorization": "used" if use_ai and ai_allowed else "not_in_plan" if use_ai else "off"}


@app.get("/api/export.csv")
def export_csv(u: UserData = Depends(current_user)):
    df = u.tx.assign(date=u.tx["date"].dt.strftime("%Y-%m-%d"), amount=u.tx["amount_cents"] / 100)
    buf = io.StringIO()
    df[["date", "account", "merchant", "description", "category", "amount", "notes"]].to_csv(buf, index=False)
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=transactions.csv"})


# ----------------------------------------------------------------------------- SMS / alert capture

@app.exception_handler(capture.CaptureError)
def capture_error(_, exc: capture.CaptureError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


def _dayfirst(currency: str) -> bool:
    """How to read 09/10/26: month-first only where that's the local habit."""
    return currency not in ("USD", "CAD")


class CaptureIn(BaseModel):
    text: str = Field(min_length=1, max_length=capture.MAX_TEXT)
    account_id: int | None = None


@app.post("/api/capture/preview")
def capture_preview(body: CaptureIn, u: UserData = Depends(require("sms_capture"))):
    """What a pasted batch of bank alerts would import, message by message, before anything is saved."""
    return capture.preview(u.user_id, body.text, date.today(), _dayfirst(u.currency))


@app.post("/api/capture/import")
def capture_import(body: CaptureIn, u: UserData = Depends(require("sms_capture"))):
    if body.account_id is not None and body.account_id not in set(u.accounts["id"].tolist()):
        raise HTTPException(400, "Unknown account")
    result = capture.import_text(u.user_id, body.text, body.account_id, date.today(), _dayfirst(u.currency),
                                 use_llm=plans.allows(u.plan, "ai_categorization"))
    growth.track(u.user_id, "sms_imported", {"inserted": result["inserted"], "via": "paste"})
    return result


@app.get("/api/capture/token")
def capture_token_status(u: UserData = Depends(current_user)):
    return {**capture.token_status(u.user_id), "endpoint": f"{settings.app_url}/api/capture/inbound",
            "allowed": plans.allows(u.plan, "sms_autocapture")}


class CaptureTokenIn(BaseModel):
    account_id: int | None = None


@app.post("/api/capture/token", status_code=201)
def capture_token_mint(body: CaptureTokenIn, u: UserData = Depends(require("sms_autocapture"))):
    """A new forwarding token, shown once. Minting again replaces the old one (e.g. a lost phone)."""
    token = capture.mint_token(u.user_id, body.account_id)
    return {"token": token, "endpoint": f"{settings.app_url}/api/capture/inbound"}


@app.delete("/api/capture/token")
def capture_token_revoke(u: UserData = Depends(current_user)):
    return {"revoked": capture.revoke_token(u.user_id)}


@app.post("/api/capture/inbound")
async def capture_inbound(request: Request, x_capture_token: str | None = Header(default=None),
                          token: str | None = Query(default=None, max_length=100)):
    """Phone → Ledgerly. Authenticated by the capture token, not a session, so an SMS-forwarder app
    or an iOS Shortcut can call it. Accepts JSON (`text`, `message`, `body` or `sms`) or plain text.

    The header is preferred; `?token=` exists because some forwarder apps can't set headers. The token
    can only *add* transactions to one ledger — it can't read anything back.
    """
    ip_limit(request, "capture", 300)
    found = capture.user_for_token(x_capture_token or token)
    if found is None:
        raise HTTPException(401, "Invalid capture token. Create a new one in Ledgerly → Quick add.")
    user_id, account_id = found
    u = UserData(user_id)
    if not plans.allows(u.plan, "sms_autocapture"):
        raise plans.PlanRequired("sms_autocapture", u.plan)
    if auth.is_locked(f"capture:{user_id}", limit=500):   # a runaway automation, not a person
        raise HTTPException(429, "Too many messages. Try again later.")
    auth.record_failure(f"capture:{user_id}")
    raw = await request.body()
    if len(raw) > 20_000:
        raise HTTPException(413, "Message too large")
    text = raw.decode("utf-8", errors="replace")
    if "json" in (request.headers.get("content-type") or ""):
        try:
            payload = json.loads(text or "{}")
        except ValueError as exc:
            raise HTTPException(400, "Body isn't valid JSON") from exc
        if isinstance(payload, dict):
            text = next((str(payload[k]) for k in ("text", "message", "body", "sms", "content") if payload.get(k)), "")
    if not text.strip():
        raise HTTPException(422, "No message text found. Send the SMS as the body, or as JSON {\"text\": \"...\"}.")
    result = capture.import_text(user_id, text, account_id, date.today(), _dayfirst(u.currency))
    if result["inserted"]:
        growth.track(user_id, "sms_imported", {"inserted": result["inserted"], "via": "forward"})
    return {"inserted": result["inserted"], "duplicates_skipped": result["duplicates_skipped"],
            "skipped": [s["reason"] for s in result["skipped"]]}


# ----------------------------------------------------------------------------- split & settle

@app.exception_handler(splits.SplitError)
def split_error(_, exc: splits.SplitError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/api/splits")
def list_splits(u: UserData = Depends(require("splits"))):
    return {**splits.overview(u.user_id), "this_month": splits.real_share(u.user_id, u.tx, u.as_of)}


class SplitPerson(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    vpa: str | None = Field(default=None, max_length=100)
    share: float | None = Field(default=None, gt=0)


class SplitIn(BaseModel):
    people: list[SplitPerson] = Field(min_length=1, max_length=splits.MAX_PEOPLE)
    transaction_id: int | None = None
    amount: float | None = Field(default=None, gt=0, description="The bill, when it isn't one of your transactions")
    include_me: bool = True
    direction: str = Field(default="owed_to_me", pattern="^(owed_to_me|i_owe)$")
    note: str | None = Field(default=None, max_length=140)


@app.post("/api/splits", status_code=201)
def create_split(body: SplitIn, u: UserData = Depends(require("splits"))):
    custom = [cents(p.share) for p in body.people] if any(p.share for p in body.people) else None
    if custom is not None and not all(p.share for p in body.people):
        raise HTTPException(422, "Give everyone a share, or leave all shares empty to split equally.")
    return splits.create(u.user_id, [p.model_dump() for p in body.people], transaction_id=body.transaction_id,
                         total_cents=cents(body.amount) if body.amount else None, include_me=body.include_me,
                         custom_cents=custom, direction=body.direction, note=body.note)


class SettleIn(BaseModel):
    person: str | None = Field(default=None, max_length=60)
    split_id: int | None = None


@app.post("/api/splits/settle")
def settle_split(body: SettleIn, u: UserData = Depends(require("splits"))):
    n = splits.settle(u.user_id, body.person, body.split_id)
    if not n:
        raise HTTPException(404, "Nothing open to settle.")
    return {"settled": n}


@app.delete("/api/splits/{split_id}")
def delete_split(split_id: int, u: UserData = Depends(require("splits"))):
    if not splits.delete(u.user_id, split_id):
        raise HTTPException(404, "Split not found")
    return {"ok": True}


# ----------------------------------------------------------------------------- can I afford it?

@app.exception_handler(afford.AffordError)
def afford_error(_, exc: afford.AffordError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


class AffordIn(BaseModel):
    amount: float = Field(gt=0, le=1_000_000_000)
    when: date | None = None
    recurring: bool = Field(False, description="A monthly cost (subscription, EMI, rent rise) rather than a one-off")
    label: str | None = Field(default=None, max_length=80)


@app.post("/api/afford")
def can_i_afford(body: AffordIn, u: UserData = Depends(require("afford"))):
    return afford.check(u.tx, u.accounts, u.goals, cents(body.amount), body.when, body.recurring, u.as_of, body.label)


# ----------------------------------------------------------------------------- category rules

class RuleIn(BaseModel):
    pattern: str = Field(min_length=2, max_length=80)
    category: str
    priority: int = 100


@app.get("/api/rules")
def list_rules(u: UserData = Depends(current_user)):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM category_rules WHERE user_id = ? ORDER BY priority DESC, id", (u.user_id,))]


@app.post("/api/rules")
def add_rule(body: RuleIn, u: UserData = Depends(current_user)):
    if body.category not in CATEGORIES:
        raise HTTPException(400, "Unknown category")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO category_rules (user_id, pattern, category, priority) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id, pattern) DO UPDATE SET category = excluded.category, priority = excluded.priority",
            (u.user_id, body.pattern.strip(), body.category, body.priority),
        )
    return {"ok": True, "recategorized": recategorize_all(u.user_id)}


@app.delete("/api/rules/{rule_id}")
def delete_rule(rule_id: int, u: UserData = Depends(current_user)):
    with get_conn() as conn:
        conn.execute("DELETE FROM category_rules WHERE id = ? AND user_id = ?", (rule_id, u.user_id))
    return {"ok": True, "recategorized": recategorize_all(u.user_id)}


# ----------------------------------------------------------------------------- subscriptions

@app.get("/api/subscriptions")
def subscriptions(u: UserData = Depends(current_user)):
    return an.subscriptions_summary(u.tx, u.sub_statuses, u.as_of)


@app.get("/api/subscriptions/unusual")
def subscriptions_unusual(u: UserData = Depends(current_user)):
    return an.unusual_subscriptions(u.tx, u.as_of)


class SubStatus(BaseModel):
    key: str
    status: str = Field(pattern="^(keep|cancel_planned|cancelled|ignored)$")
    note: str | None = None


@app.put("/api/subscriptions/status")
def set_sub_status(body: SubStatus, u: UserData = Depends(current_user)):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO subscription_actions (user_id, sub_key, status, note) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id, sub_key) DO UPDATE SET status = excluded.status, note = excluded.note, updated_at = datetime('now')",
            (u.user_id, body.key, body.status, body.note),
        )
    return {"ok": True}


# ----------------------------------------------------------------------------- budgets

@app.get("/api/budgets")
def budgets(month: str | None = None, u: UserData = Depends(current_user)):
    return an.budget_status(u.tx, u.budgets, month, u.as_of)


@app.get("/api/budgets/suggestions")
def budget_suggestions(u: UserData = Depends(current_user)):
    return an.suggest_budgets(u.tx, u.budgets, u.as_of)


class BudgetIn(BaseModel):
    category: str
    limit: float = Field(gt=0)


@app.put("/api/budgets")
def set_budget(body: BudgetIn, u: UserData = Depends(current_user)):
    if body.category not in CATEGORIES:
        raise HTTPException(400, "Unknown category")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO budgets (user_id, category, monthly_limit_cents) VALUES (?,?,?) "
            "ON CONFLICT(user_id, category) DO UPDATE SET monthly_limit_cents = excluded.monthly_limit_cents, updated_at = datetime('now')",
            (u.user_id, body.category, cents(body.limit)),
        )
    return {"ok": True}


@app.post("/api/budgets/apply-suggestions")
def apply_suggestions(u: UserData = Depends(current_user)):
    s = an.suggest_budgets(u.tx, u.budgets, u.as_of)["suggestions"]
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO budgets (user_id, category, monthly_limit_cents) VALUES (?,?,?) "
            "ON CONFLICT(user_id, category) DO UPDATE SET monthly_limit_cents = excluded.monthly_limit_cents, updated_at = datetime('now')",
            [(u.user_id, i["category"], cents(i["suggested"])) for i in s],
        )
    return {"ok": True, "applied": len(s)}


@app.delete("/api/budgets/{category}")
def delete_budget(category: str, u: UserData = Depends(current_user)):
    with get_conn() as conn:
        conn.execute("DELETE FROM budgets WHERE user_id = ? AND category = ?", (u.user_id, category))
    return {"ok": True}


# ----------------------------------------------------------------------------- goals & what-if

@app.get("/api/goals")
def goals(u: UserData = Depends(current_user)):
    return an.goals_progress(u.tx, u.goals, u.as_of)


class GoalIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    target: float = Field(gt=0)
    saved: float = Field(0, ge=0)
    target_date: date
    #: Share the goal with the household, so everyone can see it and contribute.
    shared: bool = False


@app.post("/api/goals")
def add_goal(body: GoalIn, u: UserData = Depends(current_user)):
    household_id = None
    if body.shared:
        if u.membership is None:
            raise HTTPException(409, "You're not in a household yet, so there's nobody to share this with.")
        if not u.membership.can_write:
            raise HTTPException(403, "Viewers can't create shared goals.")
        household_id = u.membership.household_id
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO goals (user_id, name, target_cents, saved_cents, target_date, household_id) VALUES (?,?,?,?,?,?)",
            (u.user_id, body.name, cents(body.target), cents(body.saved), body.target_date.isoformat(), household_id))
    return {"ok": True}


class GoalContribution(BaseModel):
    amount: float


def _goal_reach(u: UserData) -> tuple[str, tuple]:
    """Which goals this user may change: their own, plus their household's shared ones.

    A shared goal is the household's, so any member can put money into it — otherwise only whoever
    happened to create it could, which defeats the point of sharing it.
    """
    if u.membership is None or not u.membership.can_write:
        return "user_id = ?", (u.user_id,)
    return "(user_id = ? OR household_id = ?)", (u.user_id, u.membership.household_id)


@app.post("/api/goals/{goal_id}/contribute")
def contribute(goal_id: int, body: GoalContribution, u: UserData = Depends(current_user)):
    where, params = _goal_reach(u)
    with get_conn() as conn:
        cur = conn.execute(
            f"UPDATE goals SET saved_cents = MAX(0, saved_cents + ?) WHERE id = ? AND {where}",
            (cents(body.amount), goal_id, *params))
    if not cur.rowcount:
        raise HTTPException(404, "Goal not found")
    return {"ok": True}


@app.delete("/api/goals/{goal_id}")
def delete_goal(goal_id: int, u: UserData = Depends(current_user)):
    where, params = _goal_reach(u)
    with get_conn() as conn:
        conn.execute(f"DELETE FROM goals WHERE id = ? AND {where}", (goal_id, *params))
    return {"ok": True}


class SimulationIn(BaseModel):
    cancel: list[str] = []
    category_cuts: dict[str, float] = {}


@app.post("/api/simulate")
def simulate(body: SimulationIn, u: UserData = Depends(require("what_if"))):
    return an.simulate_savings(u.tx, u.goals, body.cancel, body.category_cuts, u.as_of)


# ----------------------------------------------------------------------------- net worth & debt

DEBT_KINDS = "^(credit_card|student_loan|auto_loan|mortgage|personal|loan)$"
ASSET_KINDS = "^(investment|retirement|property|vehicle|other)$"


@app.get("/api/net-worth")
def get_net_worth(u: UserData = Depends(current_user)):
    return pl.net_worth(u.tx, u.accounts, u.assets, u.debts, u.as_of)


class AssetIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: str = Field("other", pattern=ASSET_KINDS)
    value: float = Field(ge=0)


@app.post("/api/assets")
def add_asset(body: AssetIn, u: UserData = Depends(current_user)):
    with get_conn() as conn:
        conn.execute("INSERT INTO assets (user_id, name, kind, value_cents) VALUES (?,?,?,?)",
                     (u.user_id, body.name, body.kind, cents(body.value)))
    return {"ok": True}


@app.put("/api/assets/{asset_id}")
def update_asset(asset_id: int, body: AssetIn, u: UserData = Depends(current_user)):
    with get_conn() as conn:
        cur = conn.execute("UPDATE assets SET name = ?, kind = ?, value_cents = ?, updated_at = datetime('now') "
                           "WHERE id = ? AND user_id = ?", (body.name, body.kind, cents(body.value), asset_id, u.user_id))
    if not cur.rowcount:
        raise HTTPException(404, "Asset not found")
    return {"ok": True}


@app.delete("/api/assets/{asset_id}")
def delete_asset(asset_id: int, u: UserData = Depends(current_user)):
    with get_conn() as conn:
        conn.execute("DELETE FROM assets WHERE id = ? AND user_id = ?", (asset_id, u.user_id))
    return {"ok": True}


@app.get("/api/debts")
def list_debts(u: UserData = Depends(current_user)):
    return pl.debt_summary(u.debts)


class DebtIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: str = Field("loan", pattern=DEBT_KINDS)
    balance: float = Field(gt=0)
    apr: float = Field(ge=0, le=100, description="Annual percentage rate, e.g. 24.99")
    min_payment: float = Field(gt=0)


@app.post("/api/debts")
def add_debt(body: DebtIn, u: UserData = Depends(current_user)):
    with get_conn() as conn:
        conn.execute("INSERT INTO debts (user_id, name, kind, balance_cents, apr_bps, min_payment_cents) VALUES (?,?,?,?,?,?)",
                     (u.user_id, body.name, body.kind, cents(body.balance), int(round(body.apr * 100)), cents(body.min_payment)))
    return {"ok": True}


@app.put("/api/debts/{debt_id}")
def update_debt(debt_id: int, body: DebtIn, u: UserData = Depends(current_user)):
    with get_conn() as conn:
        cur = conn.execute("UPDATE debts SET name = ?, kind = ?, balance_cents = ?, apr_bps = ?, min_payment_cents = ? "
                           "WHERE id = ? AND user_id = ?",
                           (body.name, body.kind, cents(body.balance), int(round(body.apr * 100)), cents(body.min_payment),
                            debt_id, u.user_id))
    if not cur.rowcount:
        raise HTTPException(404, "Debt not found")
    return {"ok": True}


@app.delete("/api/debts/{debt_id}")
def delete_debt(debt_id: int, u: UserData = Depends(current_user)):
    with get_conn() as conn:
        conn.execute("DELETE FROM debts WHERE id = ? AND user_id = ?", (debt_id, u.user_id))
    return {"ok": True}


@app.get("/api/debts/plan")
def debt_plan(extra: float = Query(0, ge=0, le=100_000), u: UserData = Depends(require("debt_planner"))):
    return pl.debt_payoff_plan(u.debts, cents(extra), u.as_of, u.tx)


# ----------------------------------------------------------------------------- calendar, challenges, wrapped

@app.get("/api/calendar")
def calendar_month(month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"), u: UserData = Depends(current_user)):
    return pl.bill_calendar(u.tx, month, u.as_of)


@app.get("/api/challenges")
def challenges(u: UserData = Depends(require("challenges"))):
    return pl.challenges_overview(u.challenges, u.tx, u.as_of)


class ChallengeIn(BaseModel):
    type: str = Field(pattern="^(no_spend|category_cap|merchant_break)$")
    title: str = Field(min_length=1, max_length=120)
    params: dict
    duration_days: int = Field(30, ge=3, le=90)


@app.post("/api/challenges")
def start_challenge(body: ChallengeIn, u: UserData = Depends(require("challenges"))):
    p = body.params
    if body.type == "category_cap" and (p.get("category") not in CATEGORIES or int(p.get("cap_cents", 0)) <= 0):
        raise HTTPException(400, "category_cap needs a known category and a positive cap_cents")
    if body.type == "merchant_break" and not str(p.get("merchant", "")).strip():
        raise HTTPException(400, "merchant_break needs a merchant")
    if body.type == "no_spend" and not 1 <= int(p.get("target_days", 0)) <= body.duration_days:
        raise HTTPException(400, "no_spend needs target_days between 1 and the challenge length")
    start = u.as_of
    end = pd.Timestamp(start) + pd.Timedelta(days=body.duration_days - 1)
    with get_conn() as conn:
        conn.execute("INSERT INTO challenges (user_id, type, title, params_json, start_date, end_date) VALUES (?,?,?,?,?,?)",
                     (u.user_id, body.type, body.title, json.dumps(p), start.isoformat(), end.date().isoformat()))
    return {"ok": True}


@app.delete("/api/challenges/{challenge_id}")
def delete_challenge(challenge_id: int, u: UserData = Depends(current_user)):
    with get_conn() as conn:
        conn.execute("DELETE FROM challenges WHERE id = ? AND user_id = ?", (challenge_id, u.user_id))
    return {"ok": True}


@app.get("/api/wrapped")
def wrapped(year: int | None = Query(None, ge=2000, le=2100), u: UserData = Depends(require("wrapped"))):
    try:
        return pl.year_in_review(u.tx, u.as_of, year)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


# ----------------------------------------------------------------------------- growth: trials, coupons, referrals

@app.exception_handler(growth.GrowthError)
def growth_error(_, exc: growth.GrowthError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.get("/api/rewards")
def rewards(u: UserData = Depends(current_user)):
    """Trial state, referral link and stats, and any free access currently running."""
    return growth.overview(u.user_id, settings.app_url)


@app.post("/api/rewards/trial", status_code=201)
def start_trial(u: UserData = Depends(current_user)):
    return growth.start_trial(u.user_id)


class CouponIn(BaseModel):
    code: str = Field(min_length=3, max_length=32)


@app.post("/api/rewards/coupon")
def redeem_coupon(body: CouponIn, u: UserData = Depends(current_user)):
    return growth.redeem_coupon(u.user_id, body.code)


# ----------------------------------------------------------------------------- privacy

@app.get("/api/privacy/export.json")
def export_my_data(u: UserData = Depends(current_user)):
    """Everything stored about this account, as a downloadable file."""
    growth.track(u.user_id, "data_exported")
    return StreamingResponse(
        io.BytesIO(privacy.export_json(u.user_id).encode()), media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="ledgerly-my-data.json"'})


class DeleteAccountIn(BaseModel):
    #: Typed by the user to confirm. Guards against a misclick erasing a bank history.
    confirm: str = Field(max_length=20)


@app.post("/api/privacy/delete")
def delete_my_account(body: DeleteAccountIn, response: Response,
                      token: str | None = Depends(session_token), u: UserData = Depends(current_user)):
    if body.confirm.strip().upper() != "DELETE":
        raise HTTPException(422, 'Type DELETE to confirm. This cannot be undone.')
    if u.membership and u.membership.is_owner and len(household.member_ids(u.membership.household_id)) > 1:
        raise HTTPException(409, "You own a household with other people in it. Transfer ownership or "
                                 "delete the household first, so nobody else loses their shared view.")
    result = privacy.delete_account(u.user_id)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return result


# ----------------------------------------------------------------------------- operator metrics

def require_admin(u: UserData = Depends(current_user)) -> UserData:
    with get_conn() as conn:
        row = conn.execute("SELECT email FROM users WHERE id = ?", (u.user_id,)).fetchone()
    if not admin.is_admin(row["email"] if row else None):
        # same answer whether or not admins are configured, so this doesn't confirm who is one
        raise HTTPException(403, "Not available for this account.")
    return u


@app.get("/api/admin/metrics")
def admin_metrics(days: int = Query(30, ge=7, le=365), _: UserData = Depends(require_admin)):
    return admin.dashboard(days)


class AdminCouponIn(BaseModel):
    code: str = Field(min_length=3, max_length=32)
    days: int = Field(gt=0, le=3650)
    tier: str = Field(default="pro", pattern="^(pro|family)$")
    max_redemptions: int | None = Field(default=None, gt=0)
    expires_at: date | None = None
    note: str | None = Field(default=None, max_length=140)


@app.get("/api/admin/coupons")
def admin_list_coupons(_: UserData = Depends(require_admin)):
    return growth.list_coupons()


@app.post("/api/admin/coupons", status_code=201)
def admin_create_coupon(body: AdminCouponIn, _: UserData = Depends(require_admin)):
    return growth.create_coupon(body.code, body.days, body.tier, body.max_redemptions,
                                body.expires_at, body.note)


@app.delete("/api/admin/coupons/{code}")
def admin_delete_coupon(code: str, _: UserData = Depends(require_admin)):
    if not growth.delete_coupon(code):
        raise HTTPException(404, "Coupon not found")
    return {"ok": True}


# ----------------------------------------------------------------------------- freelancer & tax

@app.exception_handler(tax.TaxError)
def tax_error(_, exc: tax.TaxError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(receipts.ReceiptError)
def receipt_error(_, exc: receipts.ReceiptError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/api/tax")
def tax_overview(fy: str | None = Query(None, max_length=10),
                 u: UserData = Depends(require("freelancer"))):
    """Business P&L, the tax estimate, regime comparison and advance-tax schedule for one year."""
    return {**tax.overview(u.user_id, u.tx, u.as_of, fy),
            "receipts": receipts.coverage(u.user_id, u.tx)}


class TaxProfileIn(BaseModel):
    regime: str | None = Field(default=None, max_length=20)
    financial_year: str | None = Field(default=None, max_length=10)
    business_share_default: int | None = Field(default=None, ge=1, le=100)
    gst_registered: bool | None = None


@app.put("/api/tax/profile")
def save_tax_profile(body: TaxProfileIn, u: UserData = Depends(require("freelancer"))):
    return tax.save_profile(u.user_id, body.regime, body.financial_year,
                            body.business_share_default, body.gst_registered)


class TagIn(BaseModel):
    kind: str = Field(pattern="^(business|personal)$")
    deduction: str | None = Field(default=None, max_length=30)
    share_pct: int = Field(default=100, ge=1, le=100)
    client: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=280)


@app.put("/api/tax/tags/{transaction_id}")
def set_tax_tag(transaction_id: int, body: TagIn, u: UserData = Depends(require("freelancer"))):
    tax.tag(u.user_id, transaction_id, body.kind, body.deduction, body.share_pct,
            body.client, body.note)
    return {"ok": True}


@app.delete("/api/tax/tags/{transaction_id}")
def clear_tax_tag(transaction_id: int, u: UserData = Depends(require("freelancer"))):
    return {"removed": tax.untag(u.user_id, transaction_id)}


@app.post("/api/tax/tags/apply-rules")
def apply_tax_rules(u: UserData = Depends(require("freelancer"))):
    """Tag untagged charges from merchants already classified the same way at least twice."""
    return {"tagged": tax.apply_rules(u.user_id, u.tx)}


@app.get("/api/tax/transactions")
def tax_transactions(fy: str | None = Query(None, max_length=10),
                     kind: str | None = Query(None, pattern="^(business|personal|untagged)$"),
                     limit: int = Query(100, ge=1, le=500),
                     u: UserData = Depends(require("freelancer"))):
    """Transactions with their business classification attached, for the tagging screen."""
    prof = tax.profile(u.user_id, u.as_of)
    country = tax.REGIMES[prof["regime"]].country
    year = fy or prof["financial_year"]
    start, end = tax.fy_bounds(year, country)
    rows = tax.tagged(u.tx[(u.tx["date"].dt.date >= start) & (u.tx["date"].dt.date <= end)],
                      tax.tags_frame(u.user_id))
    if kind == "untagged":
        rows = rows[rows["kind"].isna()]
    elif kind:
        rows = rows[rows["kind"] == kind]
    rows = rows.sort_values(["date", "id"], ascending=False)
    with get_conn() as conn:
        has_receipt = {r[0] for r in conn.execute(
            "SELECT transaction_id FROM receipts WHERE user_id = ? AND transaction_id IS NOT NULL",
            (u.user_id,))}
    return {
        "financial_year": year,
        "total_count": int(len(rows)),
        "transactions": [{
            "id": int(r.id), "date": r.date.date().isoformat(), "merchant": r.merchant,
            "description": r.description, "category": r.category, "amount": an.money(r.amount_cents),
            "kind": r.kind if isinstance(r.kind, str) else None,
            "deduction": r.deduction if isinstance(r.deduction, str) else None,
            "share_pct": int(r.share_pct), "client": r.client if isinstance(r.client, str) else None,
            "source": r.tag_source if isinstance(r.tag_source, str) else None,
            "has_receipt": int(r.id) in has_receipt,
        } for r in rows.head(limit).itertuples()],
    }


class TaxPaymentIn(BaseModel):
    paid_on: date
    amount: float = Field(gt=0)
    kind: str = Field(default="advance", pattern="^(advance|self_assessment|tds)$")
    note: str | None = Field(default=None, max_length=140)


@app.post("/api/tax/payments", status_code=201)
def add_tax_payment(body: TaxPaymentIn, u: UserData = Depends(require("freelancer"))):
    return {"id": tax.add_payment(u.user_id, body.paid_on, cents(body.amount), body.kind, body.note)}


@app.delete("/api/tax/payments/{payment_id}")
def delete_tax_payment(payment_id: int, u: UserData = Depends(require("freelancer"))):
    if not tax.delete_payment(u.user_id, payment_id):
        raise HTTPException(404, "Payment not found")
    return {"ok": True}


@app.get("/api/tax/export.csv")
def export_tax_pack(fy: str | None = Query(None, max_length=10),
                    u: UserData = Depends(require("freelancer"))):
    """Every business transaction for the year, with deductible amounts — the accountant's copy."""
    prof = tax.profile(u.user_id, u.as_of)
    country = tax.REGIMES[prof["regime"]].country
    year = fy or prof["financial_year"]
    manifest = tax.export_manifest(u.user_id, u.tx, year, country)

    def stream():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([f"Ledgerly business expense report — FY {year}"])
        for key, value in manifest.items():
            writer.writerow([key.replace("_", " ").title(), value])
        writer.writerow([])
        for row in tax.export_rows(u.user_id, u.tx, year, country):
            writer.writerow(row)
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)
        yield buffer.getvalue()

    return StreamingResponse(stream(), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="ledgerly-tax-{year}.csv"'})


# ----------------------------------------------------------------------------- receipts

@app.post("/api/receipts", status_code=201)
async def upload_receipt(file: UploadFile = File(...), transaction_id: int | None = Form(None),
                         u: UserData = Depends(require("receipts"))):
    data = await file.read()
    stored = receipts.store(u.user_id, file.filename or "receipt", file.content_type or "", data)
    if transaction_id is not None:
        receipts.attach(u.user_id, stored["id"], transaction_id)
        stored["transaction_id"] = transaction_id
    else:
        stored["matches"] = receipts.suggest_matches(u.user_id, u.tx, stored["id"])
    return stored


@app.get("/api/receipts")
def list_receipts(unattached: bool = False, u: UserData = Depends(require("receipts"))):
    return {"items": receipts.listing(u.user_id, unattached), **receipts.coverage(u.user_id, u.tx)}


@app.get("/api/receipts/{receipt_id}/file")
def download_receipt(receipt_id: int, u: UserData = Depends(require("receipts"))):
    path, filename, content_type = receipts.path_for(u.user_id, receipt_id)
    return StreamingResponse(
        path.open("rb"), media_type=content_type,
        # inline so the browser can preview a PDF or image; the filename is the original one
        headers={"Content-Disposition": f'inline; filename="{filename}"'})


@app.get("/api/receipts/{receipt_id}/matches")
def receipt_matches(receipt_id: int, u: UserData = Depends(require("receipts"))):
    return receipts.suggest_matches(u.user_id, u.tx, receipt_id)


class AttachIn(BaseModel):
    transaction_id: int | None = None


@app.put("/api/receipts/{receipt_id}/attach")
def attach_receipt(receipt_id: int, body: AttachIn, u: UserData = Depends(require("receipts"))):
    receipts.attach(u.user_id, receipt_id, body.transaction_id)
    return {"ok": True}


@app.post("/api/receipts/auto-attach")
def auto_attach_receipts(u: UserData = Depends(require("receipts"))):
    """Attach every receipt with exactly one exact-amount candidate; leave ambiguous ones alone."""
    return {"attached": receipts.auto_attach(u.user_id, u.tx)}


@app.delete("/api/receipts/{receipt_id}")
def delete_receipt(receipt_id: int, u: UserData = Depends(require("receipts"))):
    if not receipts.delete(u.user_id, receipt_id):
        raise HTTPException(404, "Receipt not found")
    return {"ok": True}


# ----------------------------------------------------------------------------- household

@app.exception_handler(household.HouseholdError)
def household_error(_, exc: household.HouseholdError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


def require_household(write: bool = False):
    """Route dependency: the current user, if they're in a household (and may change shared things)."""
    def dep(u: UserData = Depends(current_user)) -> UserData:
        if u.membership is None:
            raise HTTPException(404, "You're not in a household yet.")
        if write and not u.membership.can_write:
            raise HTTPException(403, "Viewers can't change the household. Ask an owner or member.")
        return u
    return dep


class HouseholdIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class InviteIn(BaseModel):
    email: str = Field(max_length=254)
    role: str = Field(default="member", pattern="^(member|viewer)$")


class RoleIn(BaseModel):
    role: str = Field(pattern="^(member|viewer)$")


@app.get("/api/household")
def get_household(u: UserData = Depends(current_user)):
    """The user's household, or `null` with the reason they can't start one yet."""
    if u.membership is None:
        return {"household": None, "can_create": plans.allows(u.plan, "household"),
                "max_members": household.MAX_MEMBERS}
    m = u.membership
    return {
        "household": {"id": m.household_id, "name": m.name, "role": m.role, "is_owner": m.is_owner,
                      "can_write": m.can_write, "owner_id": m.owner_id},
        "members": household.members(m.household_id),
        "invites": household.pending_invites(m.household_id) if m.can_write else [],
        "max_members": household.MAX_MEMBERS,
        # the plan the household actually runs on: members inherit the owner's Family plan
        "plan": u.plan,
    }


@app.post("/api/household", status_code=201)
def create_household(body: HouseholdIn, u: UserData = Depends(require("household"))):
    m = household.create(u.user_id, body.name)
    return {"id": m.household_id, "name": m.name, "role": m.role}


@app.patch("/api/household")
def rename_household(body: HouseholdIn, u: UserData = Depends(require_household(write=True))):
    household.rename(u.membership.household_id, body.name)
    return {"ok": True}


@app.delete("/api/household")
def delete_household(u: UserData = Depends(require_household())):
    if not u.membership.is_owner:
        # a non-owner leaving is just leaving; only the owner can dissolve the household
        household.remove_member(u.membership.household_id, u.user_id)
        return {"ok": True, "left": True}
    household.delete(u.membership.household_id)
    return {"ok": True, "deleted": True}


@app.post("/api/household/invites", status_code=201)
def create_invite(body: InviteIn, background: BackgroundTasks,
                  u: UserData = Depends(require_household(write=True))):
    if not plans.allows(u.plan, "household"):
        raise plans.PlanRequired("household", u.plan)
    token = household.invite(u.membership.household_id, body.email, body.role, u.user_id)
    with get_conn() as conn:
        inviter = conn.execute("SELECT name FROM users WHERE id = ?", (u.user_id,)).fetchone()
    # the link goes only to the invited address — never back in the response, which would let
    # anyone who can call this endpoint mint themselves a join link
    background.add_task(mailer.send_household_invite, auth.normalize_email(body.email),
                        u.membership.name, inviter["name"] if inviter else None, body.role,
                        f"{settings.app_url}/join?token={token}")
    return {"ok": True, "email": auth.normalize_email(body.email), "role": body.role}


@app.delete("/api/household/invites")
def revoke_invite(email: str = Query(..., max_length=254),
                  u: UserData = Depends(require_household(write=True))):
    return {"revoked": household.revoke_invite(u.membership.household_id, email)}


@app.get("/api/household/invites/preview")
def preview_invite(token: str = Query(..., max_length=128)):
    """Public: what a join link points at, so the accept page can name the household."""
    return household.preview_invite(token)


@app.post("/api/household/invites/accept")
def accept_invite(token: str = Query(..., max_length=128), u: UserData = Depends(current_user)):
    m = household.accept(token, u.user_id)
    return {"id": m.household_id, "name": m.name, "role": m.role}


@app.put("/api/household/members/{member_id}")
def update_member(member_id: int, body: RoleIn, u: UserData = Depends(require_household())):
    if not u.membership.is_owner:
        raise HTTPException(403, "Only the household owner can change roles.")
    household.set_role(u.membership.household_id, member_id, body.role, u.user_id)
    return {"ok": True}


@app.delete("/api/household/members/{member_id}")
def remove_member(member_id: int, u: UserData = Depends(require_household())):
    if member_id != u.user_id and not u.membership.is_owner:
        raise HTTPException(403, "Only the household owner can remove other people.")
    household.remove_member(u.membership.household_id, member_id)
    return {"ok": True}


@app.post("/api/household/transfer/{member_id}")
def transfer_household(member_id: int, u: UserData = Depends(require_household())):
    if not u.membership.is_owner:
        raise HTTPException(403, "Only the household owner can transfer ownership.")
    household.transfer_ownership(u.membership.household_id, member_id)
    return {"ok": True}


class AccountSharingIn(BaseModel):
    shared: bool


@app.put("/api/accounts/{account_id}/sharing")
def set_account_sharing(account_id: int, body: AccountSharingIn, u: UserData = Depends(current_user)):
    """Keep an account out of the household ledger (or put it back). Only its owner decides."""
    with get_conn() as conn:
        changed = conn.execute("UPDATE accounts SET shared = ? WHERE id = ? AND user_id = ?",
                               (int(body.shared), account_id, u.user_id)).rowcount
    if not changed:
        raise HTTPException(404, "Account not found")
    return {"ok": True, "shared": body.shared}


@app.get("/api/accounts")
def list_accounts(u: UserData = Depends(current_user)):
    """The user's own accounts, with whether each is shared with their household."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, type, institution, opening_balance_cents, shared FROM accounts "
            "WHERE user_id = ? ORDER BY id", (u.user_id,)).fetchall()
    return [{**dict(r), "shared": bool(r["shared"])} for r in rows]


# ----------------------------------------------------------------------------- automations

@app.exception_handler(automations.RuleError)
def rule_error(_, exc: automations.RuleError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


class AlertRuleIn(BaseModel):
    kind: str = Field(max_length=40)
    params: dict = Field(default_factory=dict)
    channels: list[str] = Field(default_factory=lambda: ["inapp"])
    active: bool = True


def _channels(values: list[str]) -> str:
    allowed = [c for c in values if c in ("inapp", "email")] or ["inapp"]
    return ",".join(dict.fromkeys(allowed))


@app.get("/api/automations")
def list_automations(u: UserData = Depends(require("automations"))):
    """Every alert rule, the catalogue of rule types, and the digest setting."""
    automations.ensure_default_rules(u.user_id)
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM alert_rules WHERE user_id = ? ORDER BY id", (u.user_id,)).fetchall()
        state = conn.execute("SELECT * FROM automation_state WHERE user_id = ?", (u.user_id,)).fetchone()
    return {
        "rules": [{"id": r["id"], "kind": r["kind"], "params": json.loads(r["params_json"] or "{}"),
                   "channels": r["channels"].split(","), "active": bool(r["active"]),
                   "label": automations.RULE_TYPES[r["kind"]].label if r["kind"] in automations.RULE_TYPES else r["kind"]}
                  for r in rows],
        "types": [{"key": s.key, "label": s.label, "description": s.description,
                   "params": [{"name": n, "type": t, "label": lbl, "default": d}
                              for n, (t, lbl, d) in s.params.items()]}
                  for s in automations.RULE_TYPES.values()],
        "digest_period": (state["digest_period"] if state else "weekly") or "weekly",
        "last_run_at": state["last_run_at"] if state else None,
        "limits": {"rules": plans.limit_for(u.plan, "alert_rules"),
                   "email": plans.allows(u.plan, "alert_email"),
                   "digest": plans.allows(u.plan, "digest")},
    }


@app.post("/api/automations", status_code=201)
def add_automation(body: AlertRuleIn, u: UserData = Depends(require("automations"))):
    plans.check_alert_rule_quota(u.user_id, u.plan)
    params = automations.validate_params(body.kind, body.params)
    channels = _channels(body.channels)
    if "email" in channels and not plans.allows(u.plan, "alert_email"):
        raise plans.PlanRequired("alert_email", u.plan)
    with get_conn() as conn:
        rule_id = conn.execute(
            "INSERT INTO alert_rules (user_id, kind, params_json, channels, active) VALUES (?,?,?,?,?)",
            (u.user_id, body.kind, json.dumps(params), channels, int(body.active))).lastrowid
    return {"id": rule_id, "kind": body.kind, "params": params}


class DigestIn(BaseModel):
    period: str = Field(pattern="^(off|weekly|monthly)$")


# Declared before /automations/{rule_id}: FastAPI matches routes in declaration order, so a literal
# path has to come first or "digest" is parsed as a rule id and the request 422s.
@app.put("/api/automations/digest")
def set_digest(body: DigestIn, u: UserData = Depends(current_user)):
    if body.period != "off" and not plans.allows(u.plan, "digest"):
        raise plans.PlanRequired("digest", u.plan)
    automations.set_digest_period(u.user_id, body.period)
    return {"ok": True}


@app.put("/api/automations/{rule_id}")
def update_automation(rule_id: int, body: AlertRuleIn, u: UserData = Depends(require("automations"))):
    params = automations.validate_params(body.kind, body.params)
    channels = _channels(body.channels)
    if "email" in channels and not plans.allows(u.plan, "alert_email"):
        raise plans.PlanRequired("alert_email", u.plan)
    with get_conn() as conn:
        changed = conn.execute(
            "UPDATE alert_rules SET kind = ?, params_json = ?, channels = ?, active = ? WHERE id = ? AND user_id = ?",
            (body.kind, json.dumps(params), channels, int(body.active), rule_id, u.user_id)).rowcount
    if not changed:
        raise HTTPException(404, "Alert rule not found")
    return {"ok": True}


@app.delete("/api/automations/{rule_id}")
def delete_automation(rule_id: int, u: UserData = Depends(current_user)):
    with get_conn() as conn:
        conn.execute("DELETE FROM alert_rules WHERE id = ? AND user_id = ?", (rule_id, u.user_id))
    return {"ok": True}


@app.post("/api/automations/run")
def run_automations(u: UserData = Depends(require("automations"))):
    """Run every watcher now. Idempotent — already-reported findings are not repeated."""
    return automations.run_user(u.user_id, force=True)


@app.get("/api/automations/digest/preview")
def preview_digest(u: UserData = Depends(require("digest"))):
    return automations.build_digest(u)


@app.get("/api/notifications")
def list_notifications(limit: int = Query(50, ge=1, le=200), unread: bool = False,
                       u: UserData = Depends(current_user)):
    return automations.list_notifications(u.user_id, limit, unread)


@app.post("/api/notifications/read")
def read_notifications(notification_id: int | None = Query(None), u: UserData = Depends(current_user)):
    return {"updated": automations.mark_read(u.user_id, notification_id)}


# ----------------------------------------------------------------------------- plans & billing

@app.get("/api/plans")
def get_plans(u: UserData = Depends(current_user)):
    return plans.catalog(u.user_id, u.plan)


@app.get("/api/plans/public")
def get_public_plans():
    """Public: prices for the landing page, so they're never copied into the web app by hand."""
    return plans.public_catalog()


@app.get("/api/onboarding")
def onboarding(u: UserData = Depends(current_user)):
    """Getting-started checklist state. Each step is read from real data, never ticked by hand."""
    with get_conn() as conn:
        row = conn.execute("SELECT email_verified_at, totp_enabled_at, is_demo FROM users WHERE id = ?",
                           (u.user_id,)).fetchone()
        has = lambda sql: conn.execute(sql, (u.user_id,)).fetchone() is not None  # noqa: E731
        steps = {
            "transactions": has("SELECT 1 FROM transactions WHERE user_id = ? LIMIT 1"),
            "auto_capture": has("SELECT 1 FROM capture_tokens WHERE user_id = ?"),
            "budgets": has("SELECT 1 FROM budgets WHERE user_id = ? LIMIT 1"),
            "goals": has("SELECT 1 FROM goals WHERE user_id = ? LIMIT 1"),
            "email_verified": row["email_verified_at"] is not None,
            "two_factor": row["totp_enabled_at"] is not None,
        }
    return {"steps": steps, "done": sum(steps.values()), "total": len(steps), "is_demo": bool(row["is_demo"]),
            "auto_capture_allowed": plans.allows(u.plan, "sms_autocapture")}


@app.exception_handler(billing.BillingError)
def billing_error(_, exc: billing.BillingError):
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


@app.get("/api/billing")
def billing_status(u: UserData = Depends(current_user)):
    billing.reconcile(u.user_id)
    return billing.status(u.user_id)


class CheckoutIn(BaseModel):
    plan: str = Field(pattern="^(pro|family)$")
    period: str = Field(pattern="^(monthly|annual)$")


@app.post("/api/billing/checkout")
def billing_checkout(body: CheckoutIn, u: UserData = Depends(current_user)):
    """Create the Razorpay subscription the browser's Checkout popup pays for."""
    return billing.start_checkout(u.user_id, body.plan, body.period)


class VerifyPaymentIn(BaseModel):
    razorpay_payment_id: str = Field(min_length=1, max_length=64)
    razorpay_signature: str = Field(min_length=1, max_length=128)
    razorpay_subscription_id: str | None = Field(None, max_length=64)   # auto-renewing checkout
    razorpay_order_id: str | None = Field(None, max_length=64)          # prepaid pass checkout


@app.post("/api/billing/verify")
def billing_verify(body: VerifyPaymentIn, u: UserData = Depends(current_user)):
    """Called by Checkout's success handler: verify the signature and unlock the plan right away."""
    return billing.verify_checkout(u.user_id, body.razorpay_payment_id, body.razorpay_signature,
                                   subscription_id=body.razorpay_subscription_id, order_id=body.razorpay_order_id)


@app.post("/api/billing/cancel")
def billing_cancel(u: UserData = Depends(current_user)):
    return billing.cancel(u.user_id)


@app.post("/api/billing/webhook")
async def billing_webhook(request: Request):
    """Razorpay → Ledgerly. Authenticated by the X-Razorpay-Signature HMAC, not by a session."""
    body = await request.body()
    return billing.handle_webhook(body, request.headers.get("x-razorpay-signature"), request.headers.get("x-razorpay-event-id"))


# ----------------------------------------------------------------------------- chat

class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    thread_id: str | None = None


@app.post("/api/chat")
def chat(body: ChatIn, u: UserData = Depends(current_user)):
    plans.check_ai_quota(u.user_id, u.plan)
    thread_id = body.thread_id or uuid.uuid4().hex[:12]
    with get_conn() as conn:
        history = [dict(r) for r in conn.execute(
            "SELECT role, content FROM chat_messages WHERE user_id = ? AND thread_id = ? ORDER BY id",
            (u.user_id, thread_id))]
    try:
        result = run_agent(u.user_id, body.message, history)
    except Exception as exc:  # surface LLM/network failures as a clean API error
        raise HTTPException(502, f"Assistant unavailable: {exc}") from exc
    meta = {k: v for k, v in result.items() if k != "answer"}
    with get_conn() as conn:
        conn.execute("INSERT INTO chat_messages (user_id, thread_id, role, content) VALUES (?,?,?,?)",
                     (u.user_id, thread_id, "user", body.message))
        conn.execute("INSERT INTO chat_messages (user_id, thread_id, role, content, meta_json) VALUES (?,?,?,?,?)",
                     (u.user_id, thread_id, "assistant", result["answer"], json.dumps(meta)))
    return {"thread_id": thread_id, **result}


@app.get("/api/chat/threads")
def chat_threads(u: UserData = Depends(current_user)):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT thread_id, MIN(id) AS first_id, MAX(created_at) AS updated_at,
                      (SELECT content FROM chat_messages c2 WHERE c2.thread_id = c.thread_id AND c2.user_id = c.user_id
                       AND role = 'user' ORDER BY id LIMIT 1) AS title
               FROM chat_messages c WHERE user_id = ? GROUP BY thread_id ORDER BY MAX(id) DESC LIMIT 30""",
            (u.user_id,)).fetchall()
    return [{"thread_id": r["thread_id"], "title": (r["title"] or "")[:60], "updated_at": r["updated_at"]} for r in rows]


@app.get("/api/chat/{thread_id}")
def chat_history(thread_id: str, u: UserData = Depends(current_user)):
    with get_conn() as conn:
        rows = conn.execute("SELECT role, content, meta_json, created_at FROM chat_messages WHERE user_id = ? AND thread_id = ? ORDER BY id",
                            (u.user_id, thread_id)).fetchall()
    return [{"role": r["role"], "content": r["content"], "meta": json.loads(r["meta_json"]) if r["meta_json"] else None,
             "created_at": r["created_at"]} for r in rows]
