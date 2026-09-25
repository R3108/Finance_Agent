"""SMS capture, split & settle, "can I afford it?", two-factor sign-in, devices and API hardening."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import auth, capture, growth, mailer, mfa, splits
from app.db import get_conn

from .conftest import AS_OF, IN_USER

PW = "correct horse battery"
TODAY = date(2026, 9, 19)


@pytest.fixture()
def outbox(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, text, html=None: sent.append({"to": to, "subject": subject}))
    return sent


@pytest.fixture()
def client(seeded, outbox):
    from app.main import app
    auth._failures.clear()
    return TestClient(app, follow_redirects=False)


def signup(client, email, **kw):
    r = client.post("/api/auth/signup", json={"name": "Asha Rao", "email": email, "password": PW, **kw})
    assert r.status_code == 201, r.text
    return r.json()["user_id"]


# ----------------------------------------------------------------------------- SMS parsing

@pytest.mark.parametrize("sms, amount, payee, when", [
    ("Sent Rs.250.00\nFrom HDFC Bank A/C *1234\nTo SWIGGY\nOn 14/09/26\nRef 425712345678\nNot You?", -25000, "SWIGGY", "2026-09-14"),
    ("Dear UPI user A/C X1234 debited by 120.0 on date 14Sep26 trf to BLINKIT Refno 425712345670. -SBI", -12000, "BLINKIT", "2026-09-14"),
    ("ICICI Bank Acct XX123 debited for Rs 1,249.00 on 14-Sep-26; AMAZON PAY credited. UPI:425712345671.", -124900, "AMAZON PAY", "2026-09-14"),
    ("Spent INR 2,340.50\nAxis Bank Card no. XX4321\n14-09-26 19:22:10\nDMART\nAvl Limit: INR 1,23,456.00", -234050, "DMART", "2026-09-14"),
    ("Rs.649 spent on HDFC Bank Card x9876 at NETFLIX on 2026-09-14:10:22:11. Avl Lmt: Rs 1,50,000", -64900, "NETFLIX", "2026-09-14"),
    ("Refund of Rs 349.00 from MYNTRA credited to your A/c XX1234 on 12-09-26", 34900, "MYNTRA", "2026-09-12"),
])
def test_parses_common_indian_bank_alerts(sms, amount, payee, when):
    p = capture.parse_message(sms, TODAY)
    assert p.ok, p.reason
    assert (p.amount_cents, p.payee, p.date) == (amount, payee, when)


def test_balance_and_limit_figures_are_never_the_amount():
    p = capture.parse_message("Avl Bal Rs 45,000.00. A/c XX1234 debited Rs 500.00 on 10-09-26 to ZOMATO", TODAY)
    assert p.amount_cents == -50000


@pytest.mark.parametrize("sms", [
    "123456 is your OTP for txn of Rs.500 at AMAZON. Do not share",
    "Transaction of Rs 5,000 at FLIPKART on card XX9876 has been declined",
    "Your Card XX9876 statement: Total Amt Due Rs 12,345, Min Amt Due Rs 620 due by 25-09-26",
    "Rs 499 will be debited from your A/c XX1234 on 20-09-26 for NETFLIX mandate",
    "Hello, your KYC is complete.",
])
def test_non_transactions_are_skipped_with_a_reason(sms):
    p = capture.parse_message(sms, TODAY)
    assert not p.ok and p.reason


def test_upi_alerts_become_upi_descriptors_the_categoriser_understands(user_in):
    preview = capture.preview(IN_USER, "Sent Rs.99.00 from Kotak Bank AC X1234 to spotify@axl on 14-09-26.UPI Ref 425712345672", TODAY)
    item = preview["items"][0]
    assert item["description"] == "UPI/DR/425712345672/SPOTIFY"
    assert (item["merchant"], item["category"]) == ("Spotify", "Subscriptions")


def test_card_bill_payments_are_transfers_not_spending():
    p = capture.parse_message("Payment of Rs 12,345.00 received towards your HDFC Bank Credit Card XX9876", TODAY)
    assert p.ok and p.is_transfer and p.amount_cents == 1234500


def test_batch_splits_on_blank_lines_or_one_alert_per_line_and_dedupes_by_reference():
    lines = ("Sent Rs.10.00 to a@ybl on 14-09-26 Ref 111111111111\n"
             "Sent Rs.20.00 to b@ybl on 14-09-26 Ref 222222222222\n"
             "Sent Rs.10.00 to a@ybl on 14-09-26 Ref 111111111111")
    items = capture.parse_batch(lines, TODAY)
    assert [i.amount_cents for i in items] == [-1000, -2000, -1000]
    assert [i.ok for i in items] == [True, True, False]           # same UPI reference twice
    assert len(capture.split_messages("Spent INR 5\nCard XX1\n\nSpent INR 6\nCard XX2")) == 2


def test_import_is_idempotent(client):
    signup(client, "sms-import@example.com")
    text = "Sent Rs.250.00 From HDFC Bank A/C *1234 To SWIGGY On 14/09/26 Ref 425799999999"
    first = client.post("/api/capture/import", json={"text": text}).json()
    again = client.post("/api/capture/import", json={"text": text}).json()
    assert (first["inserted"], again["inserted"], again["duplicates_skipped"]) == (1, 0, 1)


def test_forwarding_webhook_needs_pro_and_a_valid_token(client):
    uid = signup(client, "sms-forward@example.com")
    assert client.post("/api/capture/token", json={}).status_code == 402      # Pro feature
    growth.grant_days(uid, 30, "pro", "grant")
    token = client.post("/api/capture/token", json={}).json()["token"]
    assert token.startswith("lcap_")
    phone = TestClient(client.app)                                            # no session cookie
    assert phone.post("/api/capture/inbound", content="Sent Rs.5 to x@ybl", headers={"X-Capture-Token": "lcap_nope"}).status_code == 401
    sms = "Sent Rs.75.00 From HDFC Bank A/C *1234 To BLINKIT On 15/09/26 Ref 425700000001"
    r = phone.post("/api/capture/inbound", json={"message": sms}, headers={"X-Capture-Token": token})
    assert r.status_code == 200 and r.json()["inserted"] == 1
    r = phone.post(f"/api/capture/inbound?token={token}", content=sms, headers={"content-type": "text/plain"})
    assert r.json() == {"inserted": 0, "duplicates_skipped": 1, "skipped": []}
    client.delete("/api/capture/token")
    assert phone.post("/api/capture/inbound", json={"text": sms}, headers={"X-Capture-Token": token}).status_code == 401


# ----------------------------------------------------------------------------- splits

def test_equal_shares_always_add_up_exactly():
    assert splits.equal_shares(100000, 3) == [33334, 33333, 33333]
    assert sum(splits.equal_shares(99999, 7)) == 99999


def test_split_a_transaction_and_settle_up(client):
    uid = signup(client, "splitter@example.com", sample_data=True)
    client.patch("/api/me", json={"upi_vpa": "asha@okicici"})
    with get_conn() as conn:
        txn = conn.execute("SELECT id, amount_cents FROM transactions WHERE user_id = ? AND amount_cents < -300000 "
                           "ORDER BY date DESC LIMIT 1", (uid,)).fetchone()
    r = client.post("/api/splits", json={"transaction_id": txn["id"],
                                         "people": [{"name": "Ravi"}, {"name": "Meera", "vpa": "meera@ybl"}]})
    assert r.status_code == 201
    shares = [s["amount"] for s in r.json()["shares"]]
    assert round(sum(shares) + r.json()["your_share"], 2) == abs(txn["amount_cents"]) / 100
    # the user also owes Meera for a cab she paid for
    client.post("/api/splits", json={"amount": 300, "direction": "i_owe", "include_me": False,
                                     "people": [{"name": "meera"}], "note": "Cab"})
    view = client.get("/api/splits").json()
    meera = next(b for b in view["balances"] if b["person"].lower() == "meera")
    assert meera["net"] == round(shares[1] - 300, 2)
    assert "upi://pay?pa=asha%40okicici" in meera["reminder"]                  # her reminder carries the user's UPI id
    assert view["this_month"]["your_share"] <= view["this_month"]["spending"]
    assert client.post("/api/splits/settle", json={"person": "MEERA"}).json()["settled"] == 2
    assert all(b["person"].lower() != "meera" for b in client.get("/api/splits").json()["balances"])


def test_split_validation(client):
    signup(client, "split-bad@example.com")
    assert client.post("/api/splits", json={"amount": 100, "people": [{"name": "A", "vpa": "not a vpa"}]}).status_code == 422
    assert client.post("/api/splits", json={"amount": 100, "people": [{"name": "A"}, {"name": "a"}]}).status_code == 422
    assert client.post("/api/splits", json={"amount": 100, "people": [{"name": "A", "share": 80}, {"name": "B", "share": 40}]}).status_code == 422
    assert client.patch("/api/me", json={"upi_vpa": "bad"}).status_code == 422


# ----------------------------------------------------------------------------- can I afford it?

def test_small_purchase_is_comfortable_and_huge_one_is_not(user_in):
    from app import afford
    small = afford.check(user_in.tx, user_in.accounts, user_in.goals, 5_000_00, None, False, user_in.as_of)
    assert small["verdict"] == "comfortable"
    assert small["lowest_balance_after"] == round(small["lowest_balance_before"] - 5_000, 2)
    huge = afford.check(user_in.tx, user_in.accounts, user_in.goals, 5_00_00_000_00, None, False, user_in.as_of)
    assert huge["verdict"] == "not_now" and huge["lowest_balance_after"] < 0


def test_a_mid_size_purchase_gets_a_date_when_it_would_fit(user_in):
    from app import afford
    r = afford.check(user_in.tx, user_in.accounts, user_in.goals, 60_000_00, None, False, user_in.as_of)
    assert r["verdict"] == "tight" and r["wait_until"] > user_in.as_of.isoformat()
    assert any(g["delay_months"] for g in r["goals"])


def test_recurring_cost_above_the_surplus_is_not_now(user_in):
    from app import afford
    r = afford.check(user_in.tx, user_in.accounts, user_in.goals, 10_00_000_00, None, True, user_in.as_of)
    assert r["verdict"] == "not_now" and r["annual_cost"] == 1_20_00_000


def test_afford_api_and_offline_assistant(client):
    signup(client, "afford@example.com", sample_data=True)
    r = client.post("/api/afford", json={"amount": 2500, "label": "headphones"})
    assert r.status_code == 200 and r.json()["verdict"] in ("comfortable", "tight", "not_now")
    from app.agent import _question_amount, offline_answer
    assert _question_amount("can I afford a 1.5 lakh bike in 2027?") == 150_000_00
    assert _question_amount("Can I afford a $2k laptop") == 2_000_00
    out = offline_answer(IN_USER, "Can I afford a ₹60,000 phone next month?")
    assert out["tools_used"] == ["can_i_afford"]


# ----------------------------------------------------------------------------- two-factor sign-in

def test_totp_matches_rfc6238_reference_vector():
    # RFC 6238 appendix B, SHA-1, T = 59s -> 94287082 (8 digits); the last 6 digits are what apps show
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert mfa.code_at(secret, 59 // 30) == "287082"


def _current_code(uid):
    with get_conn() as conn:
        secret = conn.execute("SELECT totp_secret FROM users WHERE id = ?", (uid,)).fetchone()[0]
    return mfa.code_at(secret, mfa.current_step())


def test_full_two_factor_lifecycle(client, outbox):
    uid = signup(client, "twofa@example.com")
    setup = client.post("/api/auth/mfa/setup").json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/Ledgerly")
    assert client.get("/api/me").json()["mfa_enabled"] is False                  # not on until a code is proven
    assert client.post("/api/auth/mfa/enable", json={"code": "000000"}).status_code == 422
    codes = client.post("/api/auth/mfa/enable", json={"code": _current_code(uid)}).json()["recovery_codes"]
    assert len(codes) == 10 and client.get("/api/me").json()["mfa_enabled"] is True
    assert any("Two-factor sign-in is on" in m["subject"] for m in outbox)

    # password alone no longer signs in
    fresh = TestClient(client.app)
    step1 = fresh.post("/api/auth/login", json={"email": "twofa@example.com", "password": PW}).json()
    assert step1["mfa_required"] and "ledgerly_session" not in fresh.cookies
    assert fresh.get("/api/me").status_code == 401
    # the code just used to enable can't be replayed
    assert fresh.post("/api/auth/login/mfa", json={"mfa_token": step1["mfa_token"], "code": _current_code(uid)}).status_code == 401
    # a recovery code works exactly once, and the step-one token is single-use
    r = fresh.post("/api/auth/login/mfa", json={"mfa_token": step1["mfa_token"], "code": codes[0].upper()})
    assert r.status_code == 200 and r.json()["recovery_codes_left"] == 9
    assert fresh.get("/api/me").status_code == 200
    assert fresh.post("/api/auth/login/mfa", json={"mfa_token": step1["mfa_token"], "code": codes[1]}).status_code == 400
    again = TestClient(client.app).post("/api/auth/login", json={"email": "twofa@example.com", "password": PW}).json()
    assert TestClient(client.app).post("/api/auth/login/mfa", json={"mfa_token": again["mfa_token"], "code": codes[0]}).status_code == 401

    # turning it off needs a code, not just the session
    assert client.post("/api/auth/mfa/disable", json={"code": "123456"}).status_code == 422
    assert client.post("/api/auth/mfa/disable", json={"code": codes[2]}).status_code == 200
    assert client.get("/api/me").json()["mfa_enabled"] is False
    kinds = [e["kind"] for e in client.get("/api/auth/security").json()["events"]]
    assert {"mfa_enabled", "login_recovery_code", "mfa_disabled"} <= set(kinds)


def test_wrong_codes_lock_out(client):
    uid = signup(client, "brute@example.com")
    client.post("/api/auth/mfa/setup")
    client.post("/api/auth/mfa/enable", json={"code": _current_code(uid)})
    token = TestClient(client.app).post("/api/auth/login", json={"email": "brute@example.com", "password": PW}).json()["mfa_token"]
    for _ in range(mfa.MAX_CODE_FAILURES):
        client.post("/api/auth/login/mfa", json={"mfa_token": token, "code": "999999"})
    r = client.post("/api/auth/login/mfa", json={"mfa_token": token, "code": "999999"})
    assert r.status_code == 401 and "Too many" in r.json()["detail"]


def test_password_reset_does_not_bypass_two_factor(client, monkeypatch):
    uid = signup(client, "reset2fa@example.com")
    client.post("/api/auth/mfa/setup")
    client.post("/api/auth/mfa/enable", json={"code": _current_code(uid)})
    token = auth.issue_email_token(uid, "reset_password")
    fresh = TestClient(client.app)
    r = fresh.post("/api/auth/reset", json={"token": token, "password": "a brand new password"})
    assert r.json()["mfa_required"] is True and fresh.get("/api/me").status_code == 401


# ----------------------------------------------------------------------------- devices

def test_device_list_and_remote_sign_out(client):
    signup(client, "devices@example.com")
    phone = TestClient(client.app, headers={"user-agent": "Mozilla/5.0 (Linux; Android 14) Chrome/128.0 Mobile Safari/537.36"})
    phone.post("/api/auth/login", json={"email": "devices@example.com", "password": PW})
    sessions = client.get("/api/auth/security").json()["sessions"]
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1
    other = next(s for s in sessions if not s["current"])
    assert other["device"] == "Chrome on Android"
    mine = next(s for s in sessions if s["current"])
    assert client.delete(f"/api/auth/sessions/{mine['id']}").status_code == 400
    assert client.delete(f"/api/auth/sessions/{other['id']}").status_code == 200
    assert phone.get("/api/me").status_code == 401
    assert client.get("/api/me").status_code == 200


def test_export_never_contains_second_factor_secrets(client):
    uid = signup(client, "export2fa@example.com")
    client.post("/api/auth/mfa/setup")
    client.post("/api/auth/mfa/enable", json={"code": _current_code(uid)})
    body = client.get("/api/privacy/export.json").text
    with get_conn() as conn:
        secret = conn.execute("SELECT totp_secret FROM users WHERE id = ?", (uid,)).fetchone()[0]
    assert secret not in body and "code_hash" not in body and "totp_secret" not in body


# ----------------------------------------------------------------------------- landing & onboarding

def test_public_plans_need_no_session_and_reveal_no_usage(client):
    anon = TestClient(client.app)
    r = anon.get("/api/plans/public")
    assert r.status_code == 200 and "usage" not in r.json()
    pro = next(p for p in r.json()["plans"] if p["id"] == "pro")
    assert pro["price_monthly"] == 299 and any(f["key"] == "sms_autocapture" and f["included"] for f in pro["features"])


def test_onboarding_steps_follow_real_data(client):
    uid = signup(client, "onboard@example.com")
    o = client.get("/api/onboarding").json()
    assert o["done"] == 0 and o["total"] == 6 and o["auto_capture_allowed"] is False
    client.post("/api/capture/import", json={"text": "Sent Rs.250.00 From HDFC Bank A/C *1234 To SWIGGY On 14/09/26 Ref 425711112222"})
    client.put("/api/budgets", json={"category": "Dining", "limit": 5000})
    steps = client.get("/api/onboarding").json()["steps"]
    assert steps["transactions"] and steps["budgets"] and not steps["goals"]
    assert TestClient(client.app).get("/api/onboarding").status_code == 401


# ----------------------------------------------------------------------------- hardening

def test_security_headers_request_id_and_probes(client):
    r = client.get("/api/health/ready")
    assert r.status_code == 200 and r.json()["database"] == "ok"
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"
    assert len(r.headers["x-request-id"]) >= 8
    echoed = client.get("/api/health/live", headers={"X-Request-ID": "abc12345-trace"})
    assert echoed.headers["x-request-id"] == "abc12345-trace"
    assert client.get("/api/health/live", headers={"X-Request-ID": "<script>"}).headers["x-request-id"] != "<script>"


def test_per_ip_login_limit(client, monkeypatch):
    monkeypatch.setenv("IP_RATE_LIMITS", "on")
    for i in range(30):
        client.post("/api/auth/login", json={"email": f"nobody{i}@example.com", "password": "x"})
    r = client.post("/api/auth/login", json={"email": "someone-else@example.com", "password": "x"})
    assert r.status_code == 429


def test_client_ip_trusts_only_our_own_proxy_hop(client):
    from starlette.requests import Request
    from app.main import client_ip
    req = Request({"type": "http", "headers": [(b"x-forwarded-for", b"6.6.6.6, 203.0.113.9")], "client": ("127.0.0.1", 1)})
    assert client_ip(req) == "203.0.113.9"      # the spoofable left-most value is ignored
