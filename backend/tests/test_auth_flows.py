"""Email verification, password reset and Google sign-in (email and Google are faked; no network)."""
import base64
import dataclasses
import json
import re
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app import auth, config, google_oauth, mailer

PW = "correct horse battery"


@pytest.fixture()
def outbox(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, text, html=None: sent.append({"to": to, "subject": subject, "text": text}))
    return sent


@pytest.fixture()
def client(seeded, outbox):
    from app.main import app
    auth._failures.clear()
    return TestClient(app, follow_redirects=False)


def link(mail: dict, path: str) -> str:
    return re.search(rf"{path}\?token=([\w-]+)", mail["text"]).group(1)


def signup(client, email, **kw):
    return client.post("/api/auth/signup", json={"name": "Sam", "email": email, "password": PW, **kw})


# ----------------------------------------------------------------------------- verification

def test_signup_sends_verification_and_link_verifies_once(client, outbox):
    signup(client, "verify@example.com")
    assert client.get("/api/me").json()["email_verified"] is False
    mail = next(m for m in outbox if m["to"] == "verify@example.com")
    assert "Confirm" in mail["subject"]
    token = link(mail, "/verify-email")
    assert client.post("/api/auth/verify", json={"token": token}).status_code == 200
    assert client.get("/api/me").json()["email_verified"] is True
    assert client.post("/api/auth/verify", json={"token": token}).status_code == 400   # single use
    assert client.post("/api/auth/verify/resend").status_code == 400                    # already verified


def test_resend_invalidates_old_link_and_is_rate_limited(client, outbox):
    signup(client, "resend@example.com")
    first = link(outbox[-1], "/verify-email")
    assert client.post("/api/auth/verify/resend").status_code == 200
    second = link(outbox[-1], "/verify-email")
    assert client.post("/api/auth/verify", json={"token": first}).status_code == 400
    assert client.post("/api/auth/verify/resend").status_code == 200
    assert client.post("/api/auth/verify/resend").status_code == 200
    assert client.post("/api/auth/verify/resend").status_code == 429
    assert client.post("/api/auth/verify", json={"token": second}).status_code == 400    # superseded by later resends


# ----------------------------------------------------------------------------- password reset

def test_forgot_password_does_not_reveal_accounts(client, outbox):
    signup(client, "known@example.com")
    before = len(outbox)
    unknown = client.post("/api/auth/forgot", json={"email": "nobody@example.com"})
    known = client.post("/api/auth/forgot", json={"email": "KNOWN@example.com"})
    assert unknown.status_code == known.status_code == 200 and unknown.json() == known.json()
    assert [m["to"] for m in outbox[before:]] == ["known@example.com"]
    assert "token=" not in known.text                                                   # link only ever goes by email


def test_reset_password_flow(client, outbox):
    signup(client, "reset@example.com")
    other_device = TestClient(client.app)
    other_device.post("/api/auth/login", json={"email": "reset@example.com", "password": PW})
    client.post("/api/auth/forgot", json={"email": "reset@example.com"})
    token = link(outbox[-1], "/reset-password")

    fresh = TestClient(client.app)
    assert fresh.post("/api/auth/reset", json={"token": token, "password": "short"}).status_code == 422
    r = fresh.post("/api/auth/reset", json={"token": token, "password": "a brand new secret"})
    assert r.status_code == 200 and fresh.get("/api/me").json()["email_verified"] is True   # signed in + verified
    assert other_device.get("/api/me").status_code == 401                                   # everyone else signed out
    assert "password was changed" in outbox[-1]["subject"]
    assert fresh.post("/api/auth/reset", json={"token": token, "password": "another secret!"}).status_code == 400
    assert fresh.post("/api/auth/login", json={"email": "reset@example.com", "password": PW}).status_code == 401
    assert fresh.post("/api/auth/login", json={"email": "reset@example.com", "password": "a brand new secret"}).status_code == 200


def test_reset_token_expires(client, outbox):
    from app.db import get_conn
    signup(client, "expire@example.com")
    client.post("/api/auth/forgot", json={"email": "expire@example.com"})
    token = link(outbox[-1], "/reset-password")
    with get_conn() as conn:
        conn.execute("UPDATE auth_tokens SET expires_at = '2000-01-01 00:00:00' WHERE purpose = 'reset_password'")
    assert client.post("/api/auth/reset", json={"token": token, "password": "a brand new secret"}).status_code == 400
    # a verify token can't be used as a reset token
    verify = link(next(m for m in outbox if m["to"] == "expire@example.com" and "Confirm" in m["subject"]), "/verify-email")
    assert client.post("/api/auth/reset", json={"token": verify, "password": "a brand new secret"}).status_code == 400


def test_forgot_is_rate_limited(client):
    for _ in range(3):
        assert client.post("/api/auth/forgot", json={"email": "spam@example.com"}).status_code == 200
    assert client.post("/api/auth/forgot", json={"email": "spam@example.com"}).status_code == 429


def test_change_password(client):
    signup(client, "change@example.com")
    assert client.post("/api/auth/password", json={"current_password": "wrong", "new_password": "new secret 123"}).status_code == 400
    assert client.post("/api/auth/password", json={"current_password": PW, "new_password": "new secret 123"}).status_code == 200
    assert client.get("/api/me").status_code == 200                                   # this device got a fresh session
    assert client.post("/api/auth/login", json={"email": "change@example.com", "password": "new secret 123"}).status_code == 200


# ----------------------------------------------------------------------------- Google sign-in

@pytest.fixture()
def google(monkeypatch):
    from app import main
    s = dataclasses.replace(config.settings, google_client_id_override="cid.apps.googleusercontent.com",
                            google_client_secret_override="secret", app_url="http://app.test")
    monkeypatch.setattr(config, "settings", s)
    monkeypatch.setattr(main, "settings", s)
    claims = {}

    class FakeResp:
        status_code = 200

        def json(self):
            b64 = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
            return {"id_token": f"{b64({'alg': 'RS256'})}.{b64(claims)}.sig"}

    def fake_post(url, data, timeout, verify=None):
        assert url == google_oauth.TOKEN_URL and data["code_verifier"] and data["redirect_uri"] == "http://app.test/api/auth/google/callback"
        return FakeResp()

    monkeypatch.setattr(google_oauth.httpx, "post", fake_post)
    return claims


def google_login(client, claims, sub, email, **override):
    start = client.get("/api/auth/google/start", params={"next": "/budgets"})
    assert start.status_code == 302
    q = parse_qs(urlparse(start.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["client_id"] == ["cid.apps.googleusercontent.com"]
    claims.clear()
    claims.update({"iss": "https://accounts.google.com", "aud": "cid.apps.googleusercontent.com", "exp": time.time() + 300,
                   "nonce": q["nonce"][0], "sub": sub, "email": email, "email_verified": True, "name": "Gee User", **override})
    return client.get("/api/auth/google/callback", params={"code": "abc", "state": override.pop("state", q["state"][0])})


def test_google_creates_verified_account(client, google):
    r = google_login(client, google, "g-1", "gee@example.com")
    assert r.status_code == 302 and r.headers["location"] == "http://app.test/budgets"
    me = client.get("/api/me").json()
    assert me["email"] == "gee@example.com" and me["email_verified"] and me["google_linked"] and not me["has_password"]
    client.post("/api/auth/logout")
    google_login(client, google, "g-1", "gee@example.com")                 # same Google account -> same user
    assert client.get("/api/me").json()["id"] == me["id"]


@pytest.mark.parametrize("override,msg", [
    ({"aud": "someone-else"}, "Invalid response"),
    ({"nonce": "replayed"}, "didn't match"),
    ({"email_verified": False}, "isn't verified"),
    ({"exp": 1}, "expired"),
    ({"state": "forged"}, "didn't match"),
])
def test_google_rejects_bad_tokens(client, google, override, msg):
    r = google_login(client, google, "g-bad", "bad@example.com", **override)
    loc = r.headers["location"]
    assert loc.startswith("http://app.test/login?error=") and msg in parse_qs(urlparse(loc).query)["error"][0]
    assert client.get("/api/me").status_code == 401


def test_google_cancel_and_missing_cookie(client, google):
    assert "cancelled" in client.get("/api/auth/google/callback", params={"error": "access_denied"}).headers["location"]
    fresh = TestClient(client.app, follow_redirects=False)
    assert "expired" in fresh.get("/api/auth/google/callback", params={"code": "x", "state": "y"}).headers["location"]


def test_google_link_wipes_unverified_squatter(client, google):
    squatter = TestClient(client.app)
    signup(squatter, "victim@example.com")                                  # attacker pre-registers victim's email
    google_login(client, google, "g-victim", "victim@example.com")          # real owner signs in with Google
    me = client.get("/api/me").json()
    assert me["email"] == "victim@example.com" and me["google_linked"] and not me["has_password"]
    assert squatter.get("/api/me").status_code == 401                       # attacker's session killed
    assert squatter.post("/api/auth/login", json={"email": "victim@example.com", "password": PW}).status_code == 401


def test_google_disabled_by_default(client):
    assert client.get("/api/auth/providers").json()["google"] is False
    r = client.get("/api/auth/google/start")
    assert r.status_code == 302 and "/login?error=" in r.headers["location"] and "unavailable" in r.headers["location"]


def test_google_follows_env_file_without_restart(client, monkeypatch, tmp_path):
    from app import main
    env = tmp_path / ".env"
    s = dataclasses.replace(config.settings, env_file=env)
    monkeypatch.setattr(config, "settings", s)
    monkeypatch.setattr(main, "settings", s)
    providers = lambda: client.get("/api/auth/providers").json()["google"]  # noqa: E731

    env.write_text("GOOGLE_CLIENT_ID=abc.apps.googleusercontent.com\nGOOGLE_CLIENT_SECRET=GOCSPX-x\n")
    assert providers() is True
    assert client.get("/api/auth/google/start").headers["location"].startswith(google_oauth.AUTH_URL)

    env.write_text("GOOGLE_CLIENT_ID=abc.apps.googleusercontent.com\nGOOGLE_CLIENT_SECRET=\n")  # secret removed
    assert providers() is False
    assert "/login?error=" in client.get("/api/auth/google/start").headers["location"]
    # a sign-in already in flight is refused too
    assert "unavailable" in client.get("/api/auth/google/callback", params={"code": "c", "state": "s"}).headers["location"]

    env.write_text("GOOGLE_CLIENT_ID=abc.apps.googleusercontent.com\nGOOGLE_CLIENT_SECRET=GOCSPX-y\n")  # restored
    assert providers() is True
    env.unlink()                                                                                     # file gone
    assert providers() is False
